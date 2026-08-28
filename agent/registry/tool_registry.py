"""Tool Registry — 工具注册与能力发现。

核心演进：
- Tool 注册时声明 capability tags
- resolve_by_capability() 按能力搜索工具
- LLM 不直接知道工具名，通过 capability 发现
"""
import inspect
from typing import Any, Dict, Callable, List, Optional


class _LazyStructuredTool:
    """Defer LangChain tool construction until a tool is actually consumed."""

    def __init__(
        self,
        func: Callable,
        *,
        name: str,
        description: str,
        is_async: bool,
    ) -> None:
        self._func = func
        self.name = name
        self.description = description
        self._is_async = is_async
        self._tool: Any = None

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
                 category: str = "general", tags: List[str] = None):
        tool_name = name or func.__name__
        description = func.__doc__ or tool_name

        tool_obj = _LazyStructuredTool(
            func,
            name=tool_name,
            description=description,
            is_async=inspect.iscoroutinefunction(func),
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

    def get_all(self) -> Dict[str, _LazyStructuredTool]:
        return self._tools

    def get_all_tools(self) -> List[_LazyStructuredTool]:
        return list(self._tools.values())

    def get_by_tag(self, tag: str) -> List[_LazyStructuredTool]:
        """按 capability tag 查找工具。"""
        return [self._tools[name] for name in self._tags.get(tag, [])]

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

    def get_all_capabilities(self) -> List[str]:
        """获取所有已注册的 capability tags。"""
        return [tag for tag in self._tags if tag not in ("all", "general")]

    @property
    def tags(self):
        return self._tags


registry = ToolRegistry()
