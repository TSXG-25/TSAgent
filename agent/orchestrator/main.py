"""ExecutionOrchestrator — 执行编排容器。

Runtime 只做状态机迁移。
Orchestrator 负责任务编排：
- PLAN 阶段：ContextBuilder 构建上下文 → PlannerStage 规划
- EXECUTE 阶段：ExecutionStage 分发执行
- 后处理：Finalizer 生成答案并提交记忆

Phase C.1：从 orchestrator.py 拆包。
共享状态（timings / selector / conversation_state / replan_count）保留在容器，
各 Stage 通过 self._orch 反向引用访问。
"""
from typing import Any, Dict, Optional, Tuple

from agent.state import AgentState
from agent.cognition.cognitive_context import ConversationState
from agent.cognition.reference_resolver import ReferenceResolver
from agent.selector.tool_selector import ToolSelector
from agent.selector.rules import DEFAULT_RULES

from .context_builder import ContextBuilder
from .planner import PlannerStage
from .executor import ExecutionStage
from .finalizer import Finalizer

MAX_REPLAN = 2


class ExecutionOrchestrator:
    """执行编排器。

    Runtime 调用 Orchestrator 来处理每个阶段。
    Orchestrator 不知道状态机，只返回处理结果。
    """

    def __init__(self):
        self._timings: Dict[str, float] = {}
        self.replan_count = 0
        # 初始化 ToolSelector（注册所有规则）
        self._selector = ToolSelector()
        for rule in DEFAULT_RULES:
            self._selector.add_rule(rule)

        # 认知层组件
        self._reference_resolver = ReferenceResolver()
        self._conversation_state = ConversationState()

        # 阶段对象（共享容器状态）
        self._context_builder = ContextBuilder(self)
        self._planner = PlannerStage(self)
        self._executor = ExecutionStage(self)
        self._finalizer = Finalizer(self)

    def reset_timings(self):
        self._timings = {}

    def get_timings(self) -> Dict[str, float]:
        return dict(self._timings)

    # ── 公共 API：委托给阶段对象 ──

    async def plan(
        self,
        user_input: str,
        user_id: str,
        context: Dict,
        repo_context: str,
        skill_hint: str,
    ) -> Tuple[AgentState, str, Optional[str]]:
        """PLAN 阶段：Build CognitiveContext → ReferenceResolver → IntentEngine → WorkflowRouter → Planner。"""
        return await self._planner.run(user_input, user_id, context, repo_context, skill_hint)

    async def execute(self, state: AgentState) -> Tuple[AgentState, str]:
        """EXECUTE 阶段：通过 ExecutorFactory 分发执行。"""
        return await self._executor.run(state)

    async def replan(
        self,
        state: AgentState,
        user_input: str,
        user_id: str,
    ) -> Tuple[AgentState, str]:
        """REPLAN 阶段：重规划失败任务。"""
        return await self._planner.replan(state, user_input, user_id)

    async def finalize(
        self,
        state: AgentState,
        user_input: str,
        user_id: str,
        best_answer: Optional[str] = None,
    ) -> str:
        """生成最终答案并提交记忆。"""
        return await self._finalizer.run(state, user_input, user_id, best_answer)
