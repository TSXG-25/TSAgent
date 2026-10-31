"""Tool Registry — 工具注册与能力发现。

核心演进：
- Tool 注册时声明 capability tags
- resolve_by_capability() 按能力搜索工具
- LLM 不直接知道工具名，通过 capability 发现
"""
import inspect
import types
from dataclasses import dataclass
from typing import Annotated, Any, Dict, Callable, List, Literal, Optional, Union, get_args, get_origin, get_type_hints


_JSON_SCHEMA_TYPES = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}

_BUILTIN_ANNOTATION_NAMES = {
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "list": list,
    "dict": dict,
}


def _parameter_json_schema(annotation: Any) -> Dict[str, Any]:
    """Map one parameter annotation to a JSON-schema fragment."""
    if annotation is inspect.Parameter.empty or annotation is Any:
        return {"type": "string"}
    if isinstance(annotation, str):
        # Unresolved string annotation (e.g. ``from __future__ import
        # annotations`` where get_type_hints could not evaluate).
        annotation = _BUILTIN_ANNOTATION_NAMES.get(annotation.strip(), str)
    origin = get_origin(annotation)
    if origin is Annotated:
        return _parameter_json_schema(get_args(annotation)[0])
    if origin is Literal:
        values = list(get_args(annotation))
        schema = _parameter_json_schema(type(values[0]) if values else str)
        schema["enum"] = values
        return schema
    if origin is Union or origin is types.UnionType:
        options = [arg for arg in get_args(annotation) if arg is not type(None)]
        return _parameter_json_schema(options[0] if options else str)
    if origin is not None:
        return {"type": _JSON_SCHEMA_TYPES.get(origin, "string")}
    return {"type": _JSON_SCHEMA_TYPES.get(annotation, "string")}


def _signature_json_schema(func: Callable) -> Dict[str, Any]:
    """Build a JSON-schema object from a tool function signature."""
    try:
        hints = get_type_hints(func, include_extras=True)
    except Exception:
        hints = {}
    properties: Dict[str, Any] = {}
    required: List[str] = []
    for param in inspect.signature(func).parameters.values():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        properties[param.name] = _parameter_json_schema(
            hints.get(param.name, param.annotation)
        )
        if param.default is inspect.Parameter.empty:
            required.append(param.name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


@dataclass(frozen=True)
class ToolAvailability:
    """Current readiness of a registered tool and its actionable failure."""

    available: bool
    reason_code: str = ""
    reason: str = ""

    def __post_init__(self) -> None:
        if self.available and (self.reason_code or self.reason):
            raise ValueError("available tools cannot carry an unavailable reason")
        if not self.available and (not self.reason_code or not self.reason):
            raise ValueError("unavailable tools require a reason code and explanation")


class _LazyStructuredTool:
    """Defer LangChain tool construction until a tool is actually consumed."""

    def __init__(
        self,
        func: Callable,
        *,
        name: str,
        description: str,
        is_async: bool,
        availability: Callable[[], ToolAvailability] | None,
    ) -> None:
        self._func = func
        self.name = name
        self.description = description
        self._is_async = is_async
        self._availability = availability
        self._tool: Any = None

    def availability(self) -> ToolAvailability:
        if self._availability is None:
            return ToolAvailability(available=True)
        return self._availability()

    def _build(self):
        if self._tool is None:
            from langchain_core.tools import StructuredTool

            if self._is_async:
                self._tool = StructuredTool.from_function(
                    coroutine=self._func,
                    name=self.name,
                    description=self.description,
                )
            else:
                self._tool = StructuredTool.from_function(
                    func=self._func,
                    name=self.name,
                    description=self.description,
                )
        return self._tool

    @property
    def args_schema(self):
        return self._build().args_schema

    def invoke(self, *args, **kwargs):
        return self._build().invoke(*args, **kwargs)

    async def ainvoke(self, *args, **kwargs):
        return await self._build().ainvoke(*args, **kwargs)

    def __getattr__(self, name: str):
        return getattr(self._build(), name)


class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, _LazyStructuredTool] = {}
        self._categories: Dict[str, List[str]] = {}
        self._tags: Dict[str, List[str]] = {}  # capability → [tool_name]

    def register(self, func: Callable, name: Optional[str] = None,
                 category: str = "general", tags: List[str] = None,
                 availability: Callable[[], ToolAvailability] | None = None):
        tool_name = name or func.__name__
        description = func.__doc__ or tool_name

        tool_obj = _LazyStructuredTool(
            func,
            name=tool_name,
            description=description,
            is_async=inspect.iscoroutinefunction(func),
            availability=availability,
        )

        self._tools[tool_name] = tool_obj
        self._categories.setdefault(category, []).append(tool_name)

        # Register capability tags
        if tags:
            self._tags.setdefault("all", []).append(tool_name)
            for tag in tags:
                self._tags.setdefault(tag, []).append(tool_name)
        # Even without tags, add to "general"
        if not tags:
            self._tags.setdefault("general", []).append(tool_name)

    def get(self, name: str) -> _LazyStructuredTool | None:
        return self._tools.get(name)

    def get_availability(self, name: str) -> ToolAvailability:
        """Read readiness for a registered tool; unknown names fail fast."""
        return self._tools[name].availability()

    def get_all(self) -> Dict[str, _LazyStructuredTool]:
        return self._tools

    def get_all_tools(self) -> List[_LazyStructuredTool]:
        return list(self._tools.values())

    def get_by_tag(self, tag: str) -> List[_LazyStructuredTool]:
        """按 capability tag 查找工具。"""
        return [
            self._tools[name]
            for name in self._tags.get(tag, [])
            if self._tools[name].availability().available
        ]

    def resolve_by_capability(self, capabilities: List[str]) -> List[_LazyStructuredTool]:
        """按能力标签搜索工具。
        
        Args:
            capabilities: 需要的 capability 列表，如 ["filesystem", "read"]
            
        Returns:
            匹配至少一个 capability 的工具列表，按匹配数量排序
        """
        cap_set = set(capabilities)
        scored: List[tuple[int, _LazyStructuredTool]] = []

        for name, tool in self._tools.items():
            if not tool.availability().available:
                continue
            # Find which capabilities this tool's tags match
            tool_tags = set()
            for tag, names in self._tags.items():
                if name in names:
                    tool_tags.add(tag)
            # Remove "all" and "general" from scoring
            tool_tags.discard("all")
            tool_tags.discard("general")
            
            matches = len(cap_set & tool_tags)
            if matches > 0:
                scored.append((matches, tool))

        # Sort by match count descending
        scored.sort(key=lambda x: -x[0])
        return [tool for _, tool in scored]

    def schemas(self) -> List[Dict[str, Any]]:
        """Model-facing tool contracts generated from registered signatures.

        One entry per available tool: ``name``, ``description`` (docstring
        first line) and ``parameters`` (JSON-schema object).  Tools that are
        unavailable in this process are excluded so the model never sees a
        tool it cannot call.  No execution internals are exposed.
        """
        entries: List[Dict[str, Any]] = []
        for name, tool in self._tools.items():
            if not tool.availability().available:
                continue
            doc = (tool.description or "").strip().splitlines()
            entries.append({
                "name": name,
                "description": doc[0] if doc else "",
                "parameters": _signature_json_schema(tool._func),
            })
        return entries

    def get_all_capabilities(self) -> List[str]:
        """获取所有已注册的 capability tags。"""
        return [tag for tag in self._tags if tag not in ("all", "general")]

    @property
    def tags(self):
        return self._tags


registry = ToolRegistry()
