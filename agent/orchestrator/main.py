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
from agent.compiler.tool_selector import Compiler
from agent.compiler.rules import DEFAULT_RULES
from agent.runtime_context import RunContext, SessionContext
from agent.context_policy import ContextPolicy
from agent.runtime_budget import RunBudget
from agent.inbox import AgentInbox
from agent.next_action import NextAction

from .context_builder import ContextBuilder
from .planner import PlannerStage
from .executor import ExecutionStage
from .finalizer import Finalizer

class ExecutionOrchestrator:
    """执行编排器。

    Runtime 调用 Orchestrator 来处理每个阶段。
    Orchestrator 不知道状态机，只返回处理结果。
    """

    def __init__(self, *, session_context: Optional[SessionContext] = None):
        self._timings: Dict[str, float] = {}
        self.replan_count = 0
        self.run_budget: RunBudget | None = None
        self._run_context: Optional[RunContext] = None
        self._session_context = session_context
        # 初始化 Compiler（注册所有 lowering rules）
        self._selector = Compiler()
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
        skill_hint: str,
        context_policy: Optional[ContextPolicy] = None,
        runtime_state: Optional[AgentState] = None,
    ) -> Tuple[AgentState, str, Optional[str]]:
        """PLAN 阶段：Build CognitiveContext → ReferenceResolver → IntentEngine → WorkflowRouter → Planner。"""
        return await self._planner.run(
            user_input,
            user_id,
            context,
            repo_context,
            skill_hint,
            context_policy=context_policy,
            runtime_state=runtime_state,
        )

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

    async def observe_failure(
        self,
        state: AgentState,
        user_input: str,
        user_id: str,
    ) -> Tuple[AgentState, str]:
        """Turn an ordinary action observation into the next bounded action.

        Action failures are facts for the result-driven loop.  They must not
        silently re-enter the open-ended Planner/Reflection path.  A retryable
        action may be retried once using the already compiled plan; all other
        failures become a terminal, truthful partial outcome.
        """
        retryable = bool(state.get("runtime_failure_retryable", False))
        retry_count = int(state.get("action_retry_count", 0) or 0)
        if retryable and retry_count < 1:
            state["action_retry_count"] = retry_count + 1
            state["runtime_failure_code"] = ""
            state["runtime_terminal_status"] = ""
            tasks = list(state.get("plan") or [])
            current_index = int(state.get("current_task_index", 0) or 0)
            if 0 <= current_index < len(tasks):
                task = tasks[current_index]
                state["next_action"] = NextAction.tool_call(
                    str((state.get("execution_plans") or [])[current_index].executor)
                    if current_index < len(state.get("execution_plans") or [])
                    else "",
                    task_id=str(task.get("id", "")),
                    reason="retry the retryable action observation",
                ).to_dict()
            return state, "NEXT_ACTION"

        state["runtime_terminal_status"] = "FAILED_TERMINAL"
        state["runtime_failure_code"] = (
            str(state.get("runtime_failure_code", "") or "")
            or str(
                (state.get("runtime_failure") or {}).get("error_code", "")
                if isinstance(state.get("runtime_failure"), dict)
                else ""
            )
            or "ACTION_EXECUTION_FAILED"
        )
        inbox = AgentInbox.from_dict(state.get("inbox"))
        inbox.add_step({
            "kind": "terminal_observation",
            "error_code": state["runtime_failure_code"],
            "reason": "ordinary action failure is not recoverable by replanning",
        })
        state["inbox"] = inbox.to_dict()
        return state, "FAIL"

    async def recover_structural_failure(
        self,
        state: AgentState,
        user_input: str,
        user_id: str,
        directive: Any,
    ) -> Tuple[AgentState, str]:
        """Apply a structural recovery directive without invoking the Planner.

        Structural recovery is deliberately action-local.  ``RETRY`` puts the
        failed action back in the pending queue; ``SWITCH`` records the failed
        strategy and advances to an already planned pending action.  Neither
        branch may manufacture a new plan, because doing so would create a
        second control spine and could repeat already committed effects.
        """
        state["recovery_directive"] = directive.to_dict()
        budget = self.run_budget
        if budget is not None and not budget.consume_recovery():
            state["runtime_failure_code"] = "RUNTIME_RECOVERY_BUDGET_EXHAUSTED"
            state["runtime_terminal_status"] = "FAILED_TERMINAL"
            return state, "FAIL"

        recovery_count = int(state.get("structural_recovery_count", 0) or 0) + 1
        state["structural_recovery_count"] = recovery_count
        state["retries"] = recovery_count

        tasks = list(state.get("plan") or [])
        failed_index = next(
            (
                index for index, task in enumerate(tasks)
                if str(task.get("status", "")) == "failed"
            ),
            None,
        )
        inbox = AgentInbox.from_dict(state.get("inbox"))
        failure = getattr(directive, "failure", None)
        failure_code = str(getattr(failure, "code", "") or "UNKNOWN")
        inbox.add_step({
            "kind": "structural_failure",
            "error_code": failure_code,
            "recovery_action": directive.action,
            "reason": directive.reason,
        })

        if directive.action == "retry" and failed_index is not None:
            task = tasks[failed_index]
            task["status"] = "pending"
            task["error"] = ""
            task["error_code"] = ""
            task["failed_component"] = ""
            task["retryable"] = False
            state["current_task_index"] = failed_index
            state["runtime_failure"] = {}
            state["runtime_failure_kind"] = ""
            state["runtime_failure_class"] = ""
            state["runtime_failure_source"] = ""
            state["runtime_failure_retryable"] = False
            state["runtime_failure_code"] = ""
            state["runtime_terminal_status"] = ""
            retry_plan = (
                (state.get("execution_plans") or [])[failed_index]
                if failed_index < len(state.get("execution_plans") or [])
                else None
            )
            state["next_action"] = NextAction.tool_call(
                getattr(retry_plan, "executor", "") or "",
                task_id=str(task.get("id", "")),
                reason=directive.reason,
            ).to_dict()
            inbox.add_step({
                "kind": "structural_recovery",
                "action": "retry",
                "task_id": str(task.get("id", "")),
                "reason": directive.reason,
            })
            state["plan"] = tasks
            state["inbox"] = inbox.to_dict()
            return state, "NEXT_ACTION"

        if directive.action == "switch":
            pending_index = next(
                (
                    index for index, task in enumerate(tasks)
                    if str(task.get("status", "pending"))
                    not in {"succeeded", "skipped", "failed"}
                ),
                None,
            )
            if pending_index is not None:
                state["current_task_index"] = pending_index
                pending_plan = (
                    (state.get("execution_plans") or [])[pending_index]
                    if pending_index < len(state.get("execution_plans") or [])
                    else None
                )
                state["next_action"] = NextAction.tool_call(
                    getattr(pending_plan, "executor", "") or "",
                    task_id=str(tasks[pending_index].get("id", "")),
                    reason=directive.reason,
                ).to_dict()
                inbox.add_step({
                    "kind": "structural_recovery",
                    "action": "switch",
                    "task_id": str(tasks[pending_index].get("id", "")),
                    "reason": directive.reason,
                })
                state["plan"] = tasks
                state["inbox"] = inbox.to_dict()
                return state, "NEXT_ACTION"

        state["runtime_terminal_status"] = (
            "BLOCKED" if directive.action == "ask" else "FAILED_TERMINAL"
        )
        state["runtime_failure_code"] = failure_code
        state["plan"] = tasks
        state["inbox"] = inbox.to_dict()
        return state, "FAIL"

    async def finalize(
        self,
        state: AgentState,
        user_input: str,
        user_id: str,
        best_answer: Optional[str] = None,
    ) -> str:
        """生成最终答案并提交记忆。"""
        return await self._finalizer.run(state, user_input, user_id, best_answer)
