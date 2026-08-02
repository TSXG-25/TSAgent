# workflows/bug_fix.py
"""
Bug Fix Workflow - 修复代码中的 bug。
步骤：读取文件 → 分析错误 → 生成补丁 → 应用补丁 → 验证
"""
from agent.registry.workflow_registry import workflow_registry
from agent.services.workflow_router import router


async def bug_fix_workflow(user_input: str, memory_context: str = "", resolved_target: str = "", **kwargs) -> list[dict]:
    """Bug 修复工作流：分析错误 → 定位文件 → 生成补丁 → 验证"""
    return [
        {"goal": "读取相关文件内容并定位 bug 位置", "status": "pending"},
        {"goal": "分析 bug 原因并生成修复补丁", "status": "pending"},
        {"goal": "应用补丁并进行验证测试", "status": "pending"},
    ]


workflow_registry.register("bug_fix", bug_fix_workflow)

router.register_workflow(
    workflow_id="bug_fix",
    examples=[
        "修复代码中的 bug",
        "这段代码报错了，帮我修复",
        "我的程序崩溃了",
        "debug 和修复",
        "修复 output/solution.py 中的错误",
    ],
    keywords=["修复", "bug", "debug", "报错", "崩溃", "fix"],
    workflow_obj=bug_fix_workflow,
)