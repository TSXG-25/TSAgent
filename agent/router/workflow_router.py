"""WorkflowRouter — 纯执行路由。

接收完整 IntentResult，映射到 Workflow。
不再接收 (domain, action) 字符串对。

路由规则支持条件函数，基于完整 IntentResult 做决策：
    intent.action == "modify" and intent.target.endswith(".py")
    intent.entities contains "Executor"
"""
from typing import Any, Callable, Optional, Tuple

from agent.registry.workflow_registry import workflow_registry
from agent.cognition.intent_schema import (
    IntentResult,
    DOMAIN_DEVELOPMENT, DOMAIN_KNOWLEDGE,
)


class WorkflowRouter:
    """纯执行路由。

    接收完整 IntentResult，基于 domain + action + target + entities 做路由。
    不再承担意图理解职责。
    """

    def __init__(self):
        # (domain, action_prefix) → workflow_id
        self._routes: dict[tuple[str, str], str] = {}
        # domain-only fallback
        self._domain_routes: dict[str, str] = {}
        # condition-based routes (condition function → workflow_id)
        self._condition_routes: list[tuple[Callable[[IntentResult], bool], str]] = []

    def register(self, domain: str, action: str, workflow_id: Optional[str] = None):
        """注册基于 domain+action 的路由规则。

        Args:
            domain: domain 名称
            action: action 前缀或全名
            workflow_id: WorkflowRegistry 中的名称，None 表示不需要 Workflow
        """
        self._routes[(domain, action)] = workflow_id or "__none__"

    def register_domain(self, domain: str, workflow_id: Optional[str] = None):
        """注册 domain 兜底路由。"""
        self._domain_routes[domain] = workflow_id or "__none__"

    def register_condition(
        self,
        condition: Callable[[IntentResult], bool],
        workflow_id: str,
    ):
        """注册基于条件函数的路由规则。

        Args:
            condition: 接收 IntentResult，返回 True 时匹配
            workflow_id: WorkflowRegistry 中的名称
        """
        self._condition_routes.append((condition, workflow_id))

    def route(self, intent: IntentResult) -> Tuple[Optional[Any], str]:
        """路由完整 IntentResult 到 Workflow。

        Args:
            intent: 完整的意图分析结果

        Returns:
            (workflow_object, reason)
        """
        domain = intent.domain
        action = intent.action

        # 0. 条件路由（优先级最高）
        for condition, wf_id in self._condition_routes:
            if condition(intent):
                wf = workflow_registry.get(wf_id)
                if wf:
                    return wf, f"条件路由: {wf_id} (intent.target={intent.target})"

        # 1. 精确匹配 (domain, action)
        if action:
            key = (domain, action)
            if key in self._routes:
                wf_id = self._routes[key]
                if wf_id == "__none__":
                    return None, f"路由: {domain}/{action} → 不需要 Workflow"
                wf = workflow_registry.get(wf_id)
                if wf:
                    return wf, f"路由: {domain}/{action} → {wf_id}"

            # 2. action 前缀匹配（跳过 __none__ 条目）
            for (d, a), wf_id in self._routes.items():
                if wf_id == "__none__":
                    continue
                if d == domain and action.startswith(a):
                    wf = workflow_registry.get(wf_id)
                    if wf:
                        return wf, f"路由: {domain}/{action} → {wf_id} (前缀匹配 {a})"

        # 3. domain 兜底
        if domain in self._domain_routes:
            wf_id = self._domain_routes[domain]
            if wf_id != "__none__":
                wf = workflow_registry.get(wf_id)
                if wf:
                    return wf, f"路由: {domain} → {wf_id} (domain 兜底)"

        return None, f"无匹配路由 (domain={domain}, action={action})"


# 单例
router = WorkflowRouter()

# 注册默认路由规则（模块导入时自动执行）
# code_generation 是“根据题目生成 Python 解题文件”的专用流程，
# 不能作为所有 development/code 请求的 domain 兜底。
def _is_question_code_generation(intent: IntentResult) -> bool:
    """仅在明确的题目解题请求下启用专用 Workflow。"""
    raw = (intent.raw_input or "").lower()
    target = (intent.target or "").lower()
    explicit_question = any(token in raw for token in (
        "题目", "解题", "算法题", "编程题", "question.docx", "question file",
    ))
    if not explicit_question:
        return False

    # 专用流程产出 Python 解题文件；显式 xlsx/docx/pptx 等目标走通用 Planner。
    output_suffixes = (".xlsx", ".xls", ".docx", ".pptx", ".csv", ".txt")
    if target.endswith(output_suffixes):
        return False
    return intent.domain == DOMAIN_DEVELOPMENT and intent.action == "code"


router.register_condition(_is_question_code_generation, "code_generation")

# development domain
# v2.0-B：modify 类（改现有代码）→ 不路由 workflow，走 Planner（多步：读→改→测试→文档）
# code_generation 只适合"生成新代码文件"（write output/solution.py），不适合修改仓库
router.register(DOMAIN_DEVELOPMENT, "modify", "__none__")
router.register_domain(DOMAIN_DEVELOPMENT, None)

# knowledge domain
router.register(DOMAIN_KNOWLEDGE, "weather", None)  # weather → no workflow, handled by Planner
router.register(DOMAIN_KNOWLEDGE, "search", None)    # search → no workflow, handled by Planner
router.register_domain(DOMAIN_KNOWLEDGE, None)

# 注册条件路由示例：修改 .py 文件走 code_generation
# v2.0-B：移除 —— code_generation 是"生成新代码"模板，modify/refactor（改现有代码）
# 应走 Planner（多步修改）。避免修改类请求被模板化到新建文件路径。
