"""Loop toolset — the model-visible tool catalog for one Run.

Entries map a model-facing tool name to either an executor-level tool
name (``executor_tool`` — dispatched through ExecutionStage/PlanExecutor)
or a loop builtin (``executor_tool=None`` — handled by the loop itself).

Authorization is a gate at build time: tools the intent did not authorize
never appear in the catalog, and ``validate_tool_action`` remains the
per-call enforcement layer underneath.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from agent.llm import function_tool_spec
from agent.registry.tool_registry import registry as _tool_registry


@dataclass(frozen=True)
class LoopTool:
    name: str
    executor_tool: str | None
    verb: str
    spec: dict[str, Any]
    # Opt-in parallel scheduling: safe calls may overlap siblings in one
    # assistant step; everything else stays an exclusive barrier.
    concurrency_safe: bool = False
    # Discovered MCP tool link: (MCPServerSpec, server-side tool name).
    # Set only for tools discovered via expose_tools; dispatch is the
    # builtin branch, not the executor.
    mcp: Any = None
    # §4.1 adapter manifest: risk tier drives the approval gate,
    # side_effects/scope drive authorization, timeout/cancel/produced
    # kinds feed the runtime policy surface.
    risk_level: str = "R0"
    permission_scope: str = ""
    side_effects: bool = False
    supports_cancel: bool = True
    produces: tuple = ()


def _fs_spec(
    name: str,
    description: str,
    properties: Mapping[str, Any],
    required: Sequence[str],
) -> dict[str, Any]:
    return function_tool_spec(name, description, {
        "type": "object",
        "properties": dict(properties),
        "required": list(required),
    })


# Model name → (executor tool, task verb, spec).  Filesystem tools use the
# scoped executor argument names, not the legacy registry signatures.
_FILESYSTEM_TOOLS = (
    ("read_file", "filesystem.read", "read", _fs_spec(
        "read_file", "读取工作区内的文本文件内容",
        {"path": {"type": "string", "description": "相对路径"}},
        ["path"],
    )),
    ("list_directory", "filesystem.list", "list", _fs_spec(
        "list_directory", "列出工作区内一个目录的内容",
        {"path": {"type": "string", "description": "相对路径，默认 '.'"}},
        [],
    )),
    ("write_file", "filesystem.write", "write", _fs_spec(
        "write_file", "写入或创建文本文件（整体覆盖或追加）",
        {
            "path": {"type": "string", "description": "相对路径"},
            "content": {"type": "string", "description": "完整文件内容"},
            "mode": {"type": "string", "enum": ["overwrite", "append"]},
        },
        ["path", "content"],
    )),
    ("edit_file", "filesystem.edit", "modify", _fs_spec(
        "edit_file",
        "精确替换文件中的一段文本；old_string 必须在文件中唯一，未修改的内容自动保留",
        {
            "path": {"type": "string", "description": "相对路径"},
            "old_string": {"type": "string", "description": "要被替换的原文（必须在文件内唯一）"},
            "new_string": {"type": "string", "description": "替换后的新文本"},
        },
        ["path", "old_string", "new_string"],
    )),
    ("copy_file", "filesystem.copy", "copy", _fs_spec(
        "copy_file", "复制文件",
        {
            "source": {"type": "string"},
            "destination": {"type": "string"},
        },
        ["source", "destination"],
    )),
    ("move_file", "filesystem.move", "move", _fs_spec(
        "move_file", "移动/重命名文件",
        {
            "source": {"type": "string"},
            "destination": {"type": "string"},
        },
        ["source", "destination"],
    )),
    ("delete_file", "filesystem.delete", "delete", _fs_spec(
        "delete_file", "删除文件",
        {"path": {"type": "string"}},
        ["path"],
    )),
)

_MUTATION_TOOL_NAMES = frozenset({
    "write_file", "edit_file", "copy_file", "move_file", "delete_file",
})

_CODE_EXECUTION_TOOLS = ("run_python", "run_python_file")


def _browser_spec(name: str, desc: str, props: dict, required: list) -> dict:
    return function_tool_spec(name, desc, {
        "type": "object", "properties": props, "required": required,
        "additionalProperties": False,
    })


# Browser tools are stateful (one playwright page per RunContext) — they
# dispatch through the builtin path, like spawn_subtask.  Read ops are R1;
# click/type mutate remote state → R2 → approval gate.
_BROWSER_TOOLS: tuple[tuple[str, str, str, dict], ...] = (
    ("browser_navigate", "read", "R1", _browser_spec(
        "browser_navigate", "打开网页并返回页面标题和 URL",
        {"url": {"type": "string", "description": "http(s) 地址"}},
        ["url"],
    )),
    ("browser_snapshot", "read", "R1", _browser_spec(
        "browser_snapshot", "读取当前页面正文文本（截断返回）",
        {}, [],
    )),
    ("browser_extract", "read", "R1", _browser_spec(
        "browser_extract", "读取指定 CSS 选择器元素的文本",
        {"selector": {"type": "string"}},
        ["selector"],
    )),
    ("browser_screenshot", "read", "R1", _browser_spec(
        "browser_screenshot", "截取当前页面保存到工作区文件",
        {"path": {"type": "string", "description": "工作区相对路径"}},
        ["path"],
    )),
    ("browser_click", "execute", "R2", _browser_spec(
        "browser_click", "点击页面元素（会改变页面/远端状态）",
        {"selector": {"type": "string"}},
        ["selector"],
    )),
    ("browser_type", "execute", "R2", _browser_spec(
        "browser_type", "在输入框填入文本（会改变页面/远端状态）",
        {"selector": {"type": "string"}, "text": {"type": "string"}},
        ["selector", "text"],
    )),
)
_COMMAND_EXECUTION_TOOLS = ("shell",)
_WEB_TOOLS = ("web_search", "web_news_search", "web_deep_search", "web_fetch")


def _registry_spec(name: str) -> dict[str, Any] | None:
    entry = next(
        (item for item in _tool_registry.schemas() if item["name"] == name),
        None,
    )
    if entry is None:
        return None
    return function_tool_spec(
        entry["name"], entry["description"], entry["parameters"]
    )


def build_toolset(
    authorization,
    *,
    workflows: Sequence[Mapping[str, Any]] = (),
    skills: Mapping[str, Any] | None = None,
    mcp_tools: Sequence[Any] = (),
) -> dict[str, LoopTool]:
    """Build the per-Run tool catalog under the intent-derived authorization."""
    tools: dict[str, LoopTool] = {}
    for name, executor_tool, verb, spec in _FILESYSTEM_TOOLS:
        if name in _MUTATION_TOOL_NAMES and not authorization.allows_file_mutation:
            continue
        _mutating = name in _MUTATION_TOOL_NAMES
        tools[name] = LoopTool(
            name=name, executor_tool=executor_tool, verb=verb, spec=spec,
            concurrency_safe=name in {"read_file", "list_directory"},
            risk_level="R1" if _mutating else "R0",
            permission_scope="workspace",
            side_effects=_mutating,
            produces=("file",) if _mutating else (),
        )

    def add_registry(name: str, verb: str, *, declares_outputs: bool = False) -> None:
        spec = _registry_spec(name)
        if spec is not None:
            if declares_outputs:
                spec = {
                    **spec,
                    "function": {
                        **spec["function"],
                        "parameters": {
                            **spec["function"]["parameters"],
                            "properties": {
                                **spec["function"]["parameters"].get("properties", {}),
                                "output_paths": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "本步骤预期产出的工作区文件/目录，用于效果核验",
                                },
                            },
                        },
                    },
                }
            tools[name] = LoopTool(
                name=name, executor_tool=name, verb=verb, spec=spec,
                concurrency_safe=name in _WEB_TOOLS,
            )

    if authorization.code_execution_allowed:
        for name in _CODE_EXECUTION_TOOLS:
            add_registry(name, "execute", declares_outputs=True)
    if authorization.command_execution_allowed:
        for name in _COMMAND_EXECUTION_TOOLS:
            add_registry(name, "execute", declares_outputs=True)
    for name in _WEB_TOOLS:
        add_registry(name, "read" if name == "web_fetch" else "search")

    # MCP-discovered tools: server schemas enter the catalog verbatim
    # under a namespaced name; the spec's expose_tools allowlist is the
    # authorization decision (see agent.mcp.tools).
    try:
        import playwright.async_api  # noqa: F401

        for name, verb, risk, spec in _BROWSER_TOOLS:
            tools[name] = LoopTool(
                name=name, executor_tool=None, verb=verb, spec=spec,
                concurrency_safe=False,
                risk_level=risk,
                permission_scope="network" if risk == "R1" else "remote",
                side_effects=risk == "R2",
                produces=("file",) if name == "browser_screenshot" else (),
            )
    except ImportError:
        pass

    for exposed in mcp_tools:
        name = exposed.catalog_name
        schema = dict(exposed.input_schema or {})
        tools[name] = LoopTool(
            name=name,
            executor_tool=None,
            verb="mcp",
            concurrency_safe=True,
            mcp=(exposed.spec, exposed.tool_name),
            spec=function_tool_spec(
                name,
                exposed.description,
                schema or {"type": "object", "properties": {}},
            ),
        )

    if workflows:
        ids = [str(item.get("id", "")) for item in workflows]
        lines = ["根据题目/输入文件执行专用工作流。"]
        for item in workflows:
            bindings = ", ".join(item.get("required_bindings") or ())
            lines.append(
                f"- {item['id']}: {item.get('description', '')} "
                f"(必需参数: {bindings or '无'})"
            )
        tools["run_workflow"] = LoopTool(
            name="run_workflow",
            executor_tool=None,
            verb="execute",
            risk_level="R1",
            permission_scope="workflow",
            side_effects=True,
            spec=function_tool_spec("run_workflow", "\n".join(lines), {
                "type": "object",
                "properties": {
                    "workflow_id": {"type": "string", "enum": ids},
                    "bindings": {
                        "type": "object",
                        "description": "工作流所需的绑定参数，如 question_path/output_path",
                    },
                },
                "required": ["workflow_id", "bindings"],
            }),
        )

    # Long-term memory sidecar (ReMe service).  Read-only like load_skill;
    # the service's own agent decides what is worth persisting — the model
    # only queries.  Unavailable service surfaces as a tool error at call
    # time, the same posture as the DuckDuckGo research tools.
    tools["reme_search"] = LoopTool(
        name="reme_search",
        executor_tool=None,
        verb="search",
        concurrency_safe=True,
        spec=function_tool_spec("reme_search", (
            "检索跨会话长期记忆（用户偏好、历史事实、过往结论、项目经验）。"
            "当问题可能依赖之前会话的信息时使用；无结果即无记忆，不要编造。"
        ), {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索查询"},
                "limit": {"type": "integer", "description": "最大结果数，默认 8"},
            },
            "required": ["query"],
        }),
    )

    tools["spawn_subtask"] = LoopTool(
        name="spawn_subtask",
        executor_tool=None,
        verb="execute",
        risk_level="R1",
        permission_scope="delegation",
        side_effects=True,
        spec=function_tool_spec("spawn_subtask", (
            "把一个明确的子任务委派给子 Agent 执行并等待结果。"
            "适合可独立完成的调研、文档、代码片段等任务；子任务有独立的 Run"
            "和 Task 记录，结果作为工具结果返回。不要委派需要当前上下文的任务。"
        ), {
            "type": "object",
            "properties": {
                "request_text": {"type": "string", "description": "子任务的完整描述"},
                "target_planet": {
                    "type": "string",
                    "description": "目标场景/星球（如 mercury/mars），留空为通用执行",
                },
                "profile_id": {"type": "string", "description": "目标 Agent Profile，可空"},
                "context_refs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "传给子任务的上下文引用（文件路径、artifact id 等）",
                },
            },
            "required": ["request_text"],
        }),
    )

    if skills is None:
        from agent.skills import skills_index

        skills = skills_index()

    if skills:
        available = ", ".join(sorted(skills))
        tools["load_skill"] = LoopTool(
            name="load_skill",
            executor_tool=None,
            verb="read",
            concurrency_safe=True,
            spec=function_tool_spec("load_skill", (
                "加载一个技能的完整操作说明。可用技能: " + available
            ), {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            }),
        )
    return tools
