# workflows/code_review.py
"""
Code Review Workflow - 代码审查。
步骤：读取代码 → 检查问题 → 输出审查报告
"""
from agent.registry.workflow_registry import workflow_registry
from agent.services.workflow_router import router


async def code_review_workflow(user_input: str, memory_context: str = "", resolved_target: str = "", **kwargs) -> list[dict]:
    """代码审查工作流：读取文件 → 分析问题 → 输出审查报告"""
    return [
        {"goal": "读取需要审查的代码文件", "status": "pending"},
        {"goal": "分析代码中的潜在问题（bug、性能、安全、风格）", "status": "pending"},
        {"goal": "汇总审查结果并输出改进建议", "status": "pending"},
    ]


workflow_registry.register("code_review", code_review_workflow)

router.register_workflow(
    workflow_id="code_review",
    examples=[
        "审查这段代码",
        "帮我 review 代码",
        "代码质量检查",
        "代码评审",
        "检查代码是否有问题",
    ],
    keywords=["审查", "review", "代码检查", "评审", "code review"],
    workflow_obj=code_review_workflow,
)