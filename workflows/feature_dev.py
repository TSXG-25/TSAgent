# workflows/feature_dev.py
"""
Feature Development Workflow - 从需求到代码实现。
步骤：分析需求 → 设计方案 → 编写代码 → 验证

V2 版本：输出 Task(verb, target) 格式，而非旧版 goal 格式。
"""
import re
from agent.registry.workflow_registry import workflow_registry
from agent.services.workflow_router import router


async def feature_dev_workflow(user_input: str, memory_context: str = "",
                               resolved_target: str = "") -> list[dict]:
    """功能开发工作流：需求分析 → 代码实现 → 验证

    Args:
        user_input: 用户原始输入
        memory_context: 上下文
        resolved_target: Workspace 解析后的目标文件路径（由 Orchestrator 传入）

    Returns:
        Task dict 列表：每项包含 verb + target + goal
    """
    # 尝试从输入或参数提取 target
    target = resolved_target or _extract_target_from_input(user_input)

    # 如果没有明确 target，使用默认 workfile
    if not target:
        target = ""
        return [
            {"verb": "read", "target": "", "goal": user_input},
        ]

    # 输出 V2 Task 格式：verb + target
    return [
        {"verb": "read", "target": target, "goal": f"读取 {target} 当前实现代码"},
        {"verb": "analyze", "target": target, "goal": f"分析 {target} 的复杂度"},
        {"verb": "modify", "target": target, "goal": f"优化 {target} 的实现"},
        {"verb": "verify", "target": target, "goal": f"验证 {target} 修改正确"},
    ]


def _extract_target_from_input(text: str) -> str:
    """从用户输入中提取文件名。"""
    # 匹配 output/xxx 或 input/xxx 等路径
    path_match = re.search(r'(?:output|input|src|agent|workflows|tools|skills|tests)/[\w./\\-]+\.\w+', text)
    if path_match:
        return path_match.group(0)
    # 匹配任意 .py/.txt/.md/.docx 文件名
    file_match = re.search(r'[\w-]+\.(?:py|txt|md|docx|json|yaml|yml|cfg|ini|toml)', text)
    if file_match:
        return file_match.group(0)
    return ""


workflow_registry.register("feature_dev", feature_dev_workflow)

router.register_workflow(
    workflow_id="feature_dev",
    examples=[
        "给项目添加一个新功能模块",
        "实现用户登录注册功能",
        "开发一个 REST API 接口",
        "添加新的前端页面",
        "实现数据导出导入功能",
        "开发新的业务模块",
        "优化 output 里 solution.py 的时间复杂度",
        "给 agent/runtime.py 添加缓存",
        "修改 workflows/feature_dev.py 的功能",
        "实现 tools/web.py 的搜索功能",
    ],
    keywords=["功能开发", "新功能", "feature", "开发模块", "业务功能"],
    workflow_obj=feature_dev_workflow,
)