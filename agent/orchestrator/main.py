"""ExecutionOrchestrator — 执行编排容器。

Runtime 只做状态机迁移。
Orchestrator 负责任务编排：
- PLAN 阶段：ContextBuilder 构建上下文 → PlannerStage 规划
- EXECUTE 阶段：ExecutionStage 分发执行
- 后处理：Finalizer 生成答案并提交记忆

Phase C.1：从 orchestrator.py 拆包。
共享状态（timings / selector / replan_count）保留在容器，
各 Stage 通过 self._orch 反向引用访问。
"""
from typing import Any, Dict, Optional, Tuple

from agent.state import AgentState
from agent.runtime_context import RunContext, SessionContext
from agent.runtime_budget import RunBudget
from agent.loop.runner import AgentLoop

from .context_builder import ContextBuilder
from .planner import PlannerStage
from .executor import ExecutionStage
from .finalizer import Finalizer

class ExecutionOrchestrator:
    """执行编排器。

    Runtime 调用 Orchestrator 来处理每个阶段。
    Orchestrator 不知道状态机，只返回处理结果。
    """

    def __init__(
        self,
        *,
        session_context: Optional[SessionContext] = None,
    ):
        self._timings: Dict[str, float] = {}
        self.replan_count = 0
        self.run_budget: RunBudget | None = None
        self._run_context: Optional[RunContext] = None
        self._session_context = session_context

        # 阶段对象（共享容器状态）
        self._context_builder = ContextBuilder(self)
        self._planner = PlannerStage(self)
        self._executor = ExecutionStage(self)
        self._loop = AgentLoop(self)
        self._finalizer = Finalizer(self)

    @property
    def run_context(self) -> Optional[RunContext]:
        """The currently bound RunContext, if this orchestrator is executing."""
        return self._run_context

    @property
    def session_context(self) -> Optional[SessionContext]:
        """The SessionContext owning this orchestrator, if explicitly bound."""
        return self._session_context

    def bind_run_context(self, run_context: RunContext) -> None:
        """Bind one execution scope without creating a new orchestrator."""
        run_context.ensure_open()
        self._run_context = run_context

    def clear_run_context(self) -> None:
        """Drop the current execution binding after RunContext teardown."""
        self._run_context = None

    def bind_run_budget(self, budget: RunBudget) -> None:
        """Bind the one budget owner for the current logical Run."""
        self.run_budget = budget

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
        runtime_state: Optional[AgentState] = None,
    ) -> Tuple[AgentState, str, Optional[str]]:
        """CONTRACT 阶段：CognitiveContext → IntentEngine → 效果契约。"""
        return await self._planner.run(
            user_input,
            user_id,
            context,
            repo_context,
            runtime_state=runtime_state,
        )

    async def run_loop(
        self,
        state: AgentState,
        user_input: str,
        user_id: str,
    ) -> Optional[str]:
        """LOOP 阶段：模型原生 tool-calling 循环直到最终答复。"""
        return await self._loop.run(
            state,
            user_input=user_input,
            user_id=user_id,
        )

    async def finalize(
        self,
        state: AgentState,
        user_input: str,
        user_id: str,
        best_answer: Optional[str] = None,
    ) -> str:
        """生成最终答案并提交记忆。"""
        return await self._finalizer.run(state, user_input, user_id, best_answer)

    def verify_goal(self, state: AgentState, answer: str):
        """Run the delivery verifier before finalization can claim success."""
        return self._finalizer.verify_goal(state, answer)
