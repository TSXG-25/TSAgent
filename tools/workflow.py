# tools/workflow.py
"""Workflow management tools for the agent.

Provides tools to query and manage available workflows,
allowing the agent to inspect and trigger workflows dynamically.
"""
from agent.registry.tool_registry import registry


def list_workflows() -> str:
    """列出所有可用的工作流及其描述。

    Returns:
        工作流名称和描述的列表
    """
    from agent.registry.workflow_registry import workflow_registry
    workflows = workflow_registry.list()
    if not workflows:
        return "暂无可用的工作流。"

    lines = []
    for name in workflows:
        try:
            wf_func = workflow_registry.get(name)
            desc = getattr(wf_func, '__doc__', '') or '无描述'
            desc = desc.strip().split('\n')[0] if desc.strip() else '无描述'
            lines.append(f"- {name}: {desc}")
        except Exception:
            lines.append(f"- {name}")

    return "\n".join(lines)


def get_workflow(name: str) -> str:
    """获取指定工作流的详细信息，包括其执行步骤说明。

    Args:
        name: 工作流名称

    Returns:
        工作流的详细信息和步骤说明
    """
    from agent.registry.workflow_registry import workflow_registry
    wf_func = workflow_registry.get(name)
    if not wf_func:
        available = workflow_registry.list()
        avail_str = ", ".join(available) if available else "无"
        return f"工作流 '{name}' 不存在。可用工作流: {avail_str}"

    doc = wf_func.__doc__ or "无文档描述"
    return f"工作流: {name}\n描述: {doc.strip()}"


def run_workflow(name: str, input_text: str = "") -> str:
    """运行指定的工作流来处理输入。

    Args:
        name: 工作流名称
        input_text: 工作流输入文本

    Returns:
        工作流执行结果
    """
    from agent.registry.workflow_registry import workflow_registry
    wf_func = workflow_registry.get(name)
    if not wf_func:
        available = workflow_registry.list()
        avail_str = ", ".join(available) if available else "无"
        return f"工作流 '{name}' 不存在。可用工作流: {avail_str}"

    try:
        import asyncio
        if asyncio.iscoroutinefunction(wf_func):
            result = asyncio.run(wf_func(input_text, ""))
        else:
            result = wf_func(input_text, "")
        return f"工作流 '{name}' 执行成功:\n{result}"
    except Exception as e:
        return f"工作流 '{name}' 执行失败: {type(e).__name__}: {e}"


# 注册工具
registry.register(list_workflows, category="workflow", tags=["workflow", "list"])
registry.register(get_workflow, category="workflow", tags=["workflow", "info"])
registry.register(run_workflow, category="workflow", tags=["workflow", "execution"])