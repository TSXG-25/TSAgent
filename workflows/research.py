# workflows/research.py
"""
Research Workflow - 调研任务。
步骤：搜索资料 → 阅读摘要 → 整理报告
"""
from agent.registry.workflow_registry import workflow_registry
from agent.services.workflow_router import router


async def research_workflow(user_input: str, memory_context: str = "", resolved_target: str = "", **kwargs) -> list[dict]:
    """调研工作流：搜索 → 阅读 → 整理答案"""
    return [
        {"goal": "使用 web_search 搜索相关信息和最新资料", "status": "pending"},
        {"goal": "阅读搜索结果中的重要链接内容", "status": "pending"},
        {"goal": "综合所有信息整理出完整回答", "status": "pending"},
    ]


workflow_registry.register("research", research_workflow)

router.register_workflow(
    workflow_id="research",
    examples=[
        "搜索 Transformer 论文的最新进展",
        "调研最近 AI 行业发展趋势",
        "搜索 Rust 语言在 2025 年的生态发展",
        "查一下 Python 3.13 相比 3.12 的新特性列表",
        "搜索最新的开源大模型排行榜",
    ],
    keywords=["搜索", "调研", "research", "查资料", "调查", "查询", "搜一下"],
    workflow_obj=research_workflow,
)