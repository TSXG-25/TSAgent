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
) -> dict[str, LoopTool]:
    """Build the per-Run tool catalog under the intent-derived authorization."""
    tools: dict[str, LoopTool] = {}
    for name, executor_tool, verb, spec in _FILESYSTEM_TOOLS:
        if name in _MUTATION_TOOL_NAMES and not authorization.allows_file_mutation:
            continue
        tools[name] = LoopTool(name=name, executor_tool=executor_tool,
                               verb=verb, spec=spec)

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
            tools[name] = LoopTool(name=name, executor_tool=name,
                                   verb=verb, spec=spec)

    if authorization.code_execution_allowed:
        for name in _CODE_EXECUTION_TOOLS:
            add_registry(name, "execute", declares_outputs=True)
    if authorization.command_execution_allowed:
        for name in _COMMAND_EXECUTION_TOOLS:
            add_registry(name, "execute", declares_outputs=True)
    for name in _WEB_TOOLS:
        add_registry(name, "read" if name == "web_fetch" else "search")

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

    from agent.skills import skills_index

    if skills_index():
        available = ", ".join(sorted(skills_index()))
        tools["load_skill"] = LoopTool(
            name="load_skill",
            executor_tool=None,
            verb="read",
            spec=function_tool_spec("load_skill", (
                "加载一个技能的完整操作说明。可用技能: " + available
            ), {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            }),
        )
    return tools
