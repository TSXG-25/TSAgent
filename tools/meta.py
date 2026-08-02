# tools/meta.py
"""Meta tools: tools that inspect or manage the agent's tool system itself."""
from agent.registry.tool_registry import registry


def list_all_tools() -> str:
    """列出所有已注册的工具及其分类和标签信息。

    Returns:
        所有可用的工具列表，包含名称、描述、分类和标签
    """
    tools = registry.get_all()
    if not tools:
        return "暂无可用的工具。"

    lines = []
    # Group by category
    categories = registry._categories
    for cat_name, tool_names in sorted(categories.items()):
        lines.append(f"\n## [{cat_name}]")
        for tool_name in sorted(tool_names):
            tool = tools.get(tool_name)
            if tool:
                desc = (tool.description or "无描述").strip().split("\n")[0][:80]
                tags = registry._tags.get(tool_name, [])
                tag_str = f" [{', '.join(tags)}]" if tags else ""
                lines.append(f"  - {tool_name}{tag_str}: {desc}")

    # Also list tools not in any category (shouldn't happen but defensive)
    categorized = set()
    for names in categories.values():
        categorized.update(names)
    uncategorized = set(tools.keys()) - categorized
    if uncategorized:
        lines.append(f"\n## [未分类]")
        for tool_name in sorted(uncategorized):
            tool = tools.get(tool_name)
            desc = (tool.description or "无描述").strip().split("\n")[0][:80]
            lines.append(f"  - {tool_name}: {desc}")

    return "\n".join(lines)


def get_tool_info(name: str) -> str:
    """获取指定工具的详细信息。

    Args:
        name: 工具名称

    Returns:
        工具的完整描述、参数列表和使用说明
    """
    tool = registry.get(name)
    if not tool:
        all_names = ", ".join(sorted(registry.get_all().keys()))
        return f"工具 '{name}' 不存在。可用工具: {all_names}"

    desc = tool.description or "无描述"
    params_info = "无参数"
    if hasattr(tool, 'args_schema') and tool.args_schema is not None:
        schema_cls = tool.args_schema
        try:
            if hasattr(schema_cls, 'model_json_schema'):
                schema = schema_cls.model_json_schema()
            else:
                schema = schema_cls.schema()
            props = schema.get('properties', {})
            if props:
                param_lines = []
                for p_name, p_info in props.items():
                    p_type = p_info.get('type', 'any')
                    p_desc = p_info.get('description', '')
                    required = "(必填)" if p_name in schema.get('required', []) else "(可选)"
                    param_lines.append(f"    {p_name} ({p_type}) {required}: {p_desc}")
                params_info = "\n" + "\n".join(param_lines)
            else:
                params_info = "  无参数"
        except Exception:
            params_info = "  无法解析参数信息"

    cat_info = ""
    for cat, names in registry._categories.items():
        if name in names:
            cat_info = f"\n分类: {cat}"
            break

    tag_info = ""
    for tag, names in registry._tags.items():
        if name in names:
            tag_info = f"\n标签: {tag}"

    return f"工具: {name}{cat_info}{tag_info}\n描述:\n{desc}\n参数:{params_info}"


# Register meta tools
registry.register(list_all_tools, category="meta", tags=["meta", "list", "tools"])
registry.register(get_tool_info, category="meta", tags=["meta", "info", "tools"])