"""UniversalAgent — 薄层状态机主循环。

Runtime 只做状态迁移。
真正的业务逻辑全部委托给 ExecutionOrchestrator。
这样 Runtime 永远不会变胖。

P0.1: Runtime Recovery —— 最后一道防线。
任何 Exception 都被 Runtime 捕获 → 结构化分类 → 友好回答 → Session 继续。
Python Traceback 永不暴露到 CLI。
"""
import logging
import time
import asyncio
import os
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from agent.services import RepositoryService
from agent.event_bus import Subscription
from agent.registry.skill_registry import skill_registry
from agent.state import AgentState
from agent.orchestrator import ExecutionOrchestrator
from agent.bootstrap import print_timings as print_bootstrap_timings
from agent.runtime_context import (
    ApplicationContext,
    RunContext,
    SessionContext,
)

logger = logging.getLogger(__name__)

MAX_RUNTIME_SECONDS = float(os.getenv("TSAGENT_MAX_RUNTIME_SECONDS", "120"))
MAX_STATE_TRANSITIONS = int(os.getenv("TSAGENT_MAX_STATE_TRANSITIONS", "24"))
FACT_EXTRACTION_TIMEOUT = float(os.getenv("TSAGENT_FACT_TIMEOUT", "15"))
DEFAULT_WORKSPACE_ROOT = Path(__file__).resolve().parent.parent


def _runtime_has_unfinished_work(state: AgentState) -> bool:
    """Project the current Runtime cache to the continuation contract signal.

    Conversation Runtime does not store tasks or plans.  It receives only this
    boolean so a later bare "继续" can distinguish plan resumption from chat.
    """
    tasks = state.get("plan", []) or []
    return any(
        str(task.get("status", "pending")) not in {"succeeded", "skipped"}
        for task in tasks
        if isinstance(task, dict)
    )


def _runtime_pending_target(state: AgentState) -> str:
    """Project one pending target without copying the Runtime plan to Conversation."""
    for task in state.get("plan", []) or []:
        if not isinstance(task, dict):
            continue
        if str(task.get("status", "pending")) in {"succeeded", "skipped"}:
            continue
        target = str(task.get("target", "") or "").strip()
        if target:
            return target
    return ""


def _build_run_evidence(state: AgentState, final_answer: str, transitions: int) -> dict:
    """Project the last run into benchmark-safe continuation evidence.

    This is deliberately a small read-only projection. It exposes verifier
    outcomes and resolved targets without making ConversationState own plans,
    tasks, or artifacts.
    """
    tasks = list(state.get("plan", []) or [])
    observations = [
        observation
        for task in tasks
        for observation in (task.get("observations", []) or [])
        if isinstance(observation, dict)
    ]
    snapshot = state.get("conversation_snapshot")
    return {
        "conversation_intent": state.get("conversation_intent", ""),
        "requires_execution": any(
            str(task.get("status", "")) not in {"succeeded", "skipped"}
            for task in tasks
        ) or bool(state.get("conversation_intent") == "continue_plan"),
        "execution_progress": len(observations),
        "verified_success": any(
            observation.get("status") == "succeeded"
            for observation in observations
        ),
        "resolved_target": state.get("resolved_target", ""),
        "pending_target": _runtime_pending_target(state),
        "previous_answer": getattr(snapshot, "last_answer", "") if snapshot else "",
        "answer": final_answer or "",
        "transitions": transitions,
        "runtime_pending": _runtime_has_unfinished_work(state),
    }


class RuntimeState(str, Enum):
    INIT = "INIT"
    PLAN = "PLAN"
    EXECUTE = "EXECUTE"
    NEXT_TASK = "NEXT_TASK"
    RECOVER = "RECOVER"
    REPLAN = "REPLAN"
    FINISH = "FINISH"
    FAIL = "FAIL"


def _build_repo_context(user_input: str) -> str:
    """根据用户输入搜索相关代码片段。"""
    hits = RepositoryService.search_similar(user_input, k=5)
    if not hits:
        return ""
    return "\n\n".join(f"[{h['path']}]\n{h['content']}" for h in hits)


class UniversalAgent:
    """薄层状态机。
    
    职责：
    1. 状态迁移（INIT → PLAN → EXECUTE → FINISH/FAIL）
    2. 初始上下文构建
    3. 调用 Orchestrator 处理各阶段
    4. 耗时统计和输出
    """

    def __init__(
        self,
        user_id: str = "default",
        *,
        tenant_id: str = "default",
        session_context: Optional[SessionContext] = None,
        run_context: Optional[RunContext] = None,
        workspace: Optional[Any] = None,
    ):
        self.user_id = user_id
        if session_context is None:
            self._application_context = ApplicationContext(
                workspace_root=(
                    workspace
                    if isinstance(workspace, Path)
                    else DEFAULT_WORKSPACE_ROOT
                ),
            )
            self._session_context = self._application_context.create_session(
                user_id=user_id,
                tenant_id=tenant_id,
            )
            self._owns_session_context = True
        else:
            self._application_context = session_context.application
            self._session_context = session_context
            self._owns_session_context = False
        self._memory_namespace = self._session_context.memory_namespace
        self._memory_view = self._session_context.memory_view
        self._workspace = (
            workspace
            if workspace is not None
            else self._application_context.workspace_root
        )
        self.orchestrator = ExecutionOrchestrator(
            session_context=self._session_context,
        )
        self._timings: dict = {}
        self.last_run_evidence: dict = {}
        self._pending_execution_target = ""
        self._run_context: Optional[RunContext] = None
        self._task_subscription: Optional[Subscription] = None
        if run_context is not None:
            self.attach_run(run_context)

    @property
    def session_context(self) -> SessionContext:
        return self._session_context

    @property
    def run_context(self) -> Optional[RunContext]:
        return self._run_context

    def close(self) -> None:
        """Release process resources for the attached logical Run."""
        run_context = self._run_context
        self.detach_run()
        if run_context is not None and not run_context.closed:
            run_context.close()
        if self._owns_session_context:
            self._session_context.close()
            self._application_context.close()

    def attach_run(self, run_context: RunContext) -> None:
        """Attach this Agent to an existing logical RunContext."""
        run_context.ensure_open()
        if run_context.session is not self._session_context:
            raise ValueError("RunContext belongs to another SessionContext")
        self._session_context.activate_run(run_context.run_id)
        if self._run_context is not None and self._run_context is not run_context:
            raise RuntimeError("agent is already attached to another RunContext")
        self._run_context = run_context
        bind_run_context = getattr(self.orchestrator, "bind_run_context", None)
        if callable(bind_run_context):
            bind_run_context(run_context)

    def detach_run(self) -> None:
        """Detach the Agent without closing the logical Run or its stores."""
        run_context = self._run_context
        if self._task_subscription is not None:
            self._task_subscription.close()
            self._task_subscription = None
        clear_run_context = getattr(self.orchestrator, "clear_run_context", None)
        if callable(clear_run_context):
            clear_run_context()
        self._run_context = None
        if run_context is not None:
            self._session_context.deactivate_run(run_context.run_id)

    def _ensure_run_subscription(self) -> RunContext:
        """Create or reuse the logical Run and bind its event subscription."""
        run_context = self._run_context
        if run_context is None:
            run_context = self._session_context.create_run(workspace=self._workspace)
            self.attach_run(run_context)
        else:
            run_context.ensure_open()
            bind_run_context = getattr(self.orchestrator, "bind_run_context", None)
            if callable(bind_run_context):
                bind_run_context(run_context)
        if self._task_subscription is None:
            self._task_subscription = run_context.event_bus.subscribe(
                "task_end", self._on_task_end,
            )
        return run_context

    def _emit(self, event_type: str, data: object) -> None:
        if self._run_context is None:
            raise RuntimeError("agent has no active RunContext")
        self._run_context.event_bus.emit(event_type, data)

    async def _on_task_end(self, data):
        print(f"[EVENT] Task {data['task']} ended with {data['status']}")

    def _print_timing_summary(self):
        print_bootstrap_timings()
        total = sum(self._timings.values())
        # Merge orchestrator timings
        orch_timings = self.orchestrator.get_timings()
        all_timings = dict(self._timings)
        all_timings.update(orch_timings)
        total = sum(all_timings.values())
        print("=" * 50)
        print("⚡ 执行耗时 Profile")
        print("=" * 50)
        for name, elapsed in sorted(all_timings.items(), key=lambda x: -x[1]):
            pct = (elapsed / total * 100) if total > 0 else 0
            bar = "█" * int(pct / 5) + "░" * (20 - int(pct / 5))
            print(f"  {name:20s} {elapsed:>6.2f}s  {bar} {pct:.0f}%")
        print(f"  {'TOTAL':20s} {total:>6.2f}s")
        print("=" * 50 + "\n")

    def _recover(self, e: Exception, user_input: str, user_id: str) -> str:
        """Runtime Recovery：异常 → 结构化分类 → 友好回答 → Session 继续。

        返回最终答案；Traceback 永不暴露到 CLI。
        """
        layer, code, friendly = self._classify(e)
        logger.error("Runtime recovered: layer=%s code=%s error=%s", layer, code, e)
        # 结构化记录（供 Recovery Dataset / Metrics）
        self._emit("runtime_recovered", {
            "layer": layer, "error_code": code, "error": str(e)[:300],
        })
        answer = f"抱歉，刚才在处理「{user_input[:30]}」时遇到了一点问题（{friendly}），请换一种说法再试试。"
        try:
            self._memory_view.record_full_exchange(user_input, answer)
        except Exception:
            pass
        return answer

    @staticmethod
    def _classify(e: Exception) -> tuple:
        """异常 → (layer, error_code, friendly_hint)。"""
        name = type(e).__name__
        msg = str(e)
        if "unexpected keyword" in msg or "TypeError" in msg:
            return "integration", "TOOL_CONTRACT", "接口参数不匹配"
        if "TimeoutError" in name or "timeout" in msg.lower():
            return "tool", "TOOL_TIMEOUT", "工具执行超时"
        if "ValidationError" in name or "validation" in msg.lower():
            return "compiler", "CONTRACT_VIOLATION", "计划不合法"
        if "KeyError" in name or "IndexError" in name:
            return "runtime", "DATA_ACCESS", "内部数据访问异常"
        return "runtime", f"{name.upper()}", "内部错误"

    async def run(self, user_input: str) -> str:
        """Run one message inside the attached logical RunContext."""
        self._ensure_run_subscription()
        return await self._run_in_context(user_input)

    async def _run_in_context(self, user_input: str) -> str:
        """State machine body; caller owns the RunContext lifecycle."""
        # ── 初始化 ──
        self._timings = {}
        self.orchestrator.reset_timings()
        self.orchestrator.replan_count = 0
        t0 = time.perf_counter()

        try:
            await asyncio.wait_for(
                self._memory_view.extract_and_save_facts(user_input),
                timeout=FACT_EXTRACTION_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning("事实抽取超时，继续执行主任务")
        except Exception as exc:
            # Do not emit a traceback from an optional memory enhancement.
            logger.warning("事实抽取失败，继续执行主任务: %s", str(exc)[:200])
        self._memory_view.record_user_message(user_input)

        from agent.query_normalizer import QueryNormalizer
        normalized_input = QueryNormalizer.process(user_input, self._memory_namespace)
        context = self._memory_view.get_context(normalized_input)
        context["runtime_pending_target"] = self._pending_execution_target
        repo_context = _build_repo_context(normalized_input)
        skill = skill_registry.select(normalized_input)
        skill_hint = skill.planner_hint if skill else ""

        self._timings["init"] = round(time.perf_counter() - t0, 3)

        # ── 状态机循环 ──
        rt_state = RuntimeState.INIT
        state: AgentState = {
            "messages": [],
            "plan": [],
            "current_task_index": 0,
            "artifacts": {},
            "memory_context": "\n\n".join(
                part for part in (context.get("session", ""), context.get("short_term", ""))
                if part
            ),
            "repo_context": repo_context,
            "skill_hint": skill_hint,
            "retries": 0,
            "workflow": None,
        }
        best_answer = None
        loop_started = time.perf_counter()
        transitions = 0

        # ── 状态机循环（P0.1: Runtime Recovery 最后防线）──
        try:
            while rt_state not in (RuntimeState.FINISH, RuntimeState.FAIL):
                transitions += 1
                if (
                    transitions > MAX_STATE_TRANSITIONS
                    or time.perf_counter() - loop_started > MAX_RUNTIME_SECONDS
                ):
                    logger.warning(
                        "Runtime execution budget exhausted: transitions=%s elapsed=%.1fs",
                        transitions,
                        time.perf_counter() - loop_started,
                    )
                    best_answer = best_answer or "任务执行达到时间或步骤上限，已停止继续重试。"
                    rt_state = RuntimeState.FINISH
                    break

                if rt_state == RuntimeState.INIT:
                    rt_state = RuntimeState.PLAN

                elif rt_state == RuntimeState.PLAN:
                    # 委托 Orchestrator
                    state, next_state, answer = await self.orchestrator.plan(
                        user_input=normalized_input,
                        user_id=self._memory_namespace,
                        context=context,
                        repo_context=repo_context,
                        skill_hint=skill_hint,
                    )
                    if answer:
                        best_answer = answer
                    rt_state = RuntimeState[next_state] if isinstance(next_state, str) else next_state
                    self._timings["plan"] = self.orchestrator._timings.get("plan_llm", 0)

                elif rt_state == RuntimeState.EXECUTE:
                    state, next_state = await self.orchestrator.execute(state)
                    rt_state = RuntimeState[next_state] if isinstance(next_state, str) else next_state

                elif rt_state == RuntimeState.RECOVER:
                    state, next_state = await self.orchestrator.replan(
                        state, normalized_input, self._memory_namespace,
                    )
                    rt_state = RuntimeState[next_state] if isinstance(next_state, str) else next_state

                elif rt_state == RuntimeState.NEXT_TASK:
                    best_answer = await self.orchestrator.finalize(
                        state=state,
                        user_input=user_input,
                        user_id=self._memory_namespace,
                    )
                    rt_state = RuntimeState.FINISH

                elif rt_state == RuntimeState.REPLAN:
                    # 已由 RECOVER 处理
                    pass
        except Exception as e:
            # P0.1: Runtime Recovery —— 不暴露 Traceback，Session 继续
            print(f"  ⚠️ Runtime 捕获异常并恢复: {type(e).__name__}")
            best_answer = self._recover(e, user_input, self._memory_namespace)
            rt_state = RuntimeState.FINISH

        # ── 最终输出 ──
        final_answer = await self.orchestrator.finalize(
            state=state,
            user_input=user_input,
            user_id=self._memory_namespace,
            best_answer=best_answer,
        )
        # ── v2.1B-1：会话状态更新（ADR-0013；每轮 answer 后）──
        conversation_diagnostic = None
        try:
            conversation_tracker = self._session_context.conversation_tracker
            conversation_tracker.update(
                user_id=self._memory_namespace,
                user_input=user_input,
                assistant_answer=final_answer,
                intent=getattr(self.orchestrator, "last_intent", None),
                runtime_pending=_runtime_has_unfinished_work(state),
            )
        except (AttributeError, ImportError, KeyError, TypeError) as exc:
            from agent.diagnostics import handle_contract_violation

            event = handle_contract_violation(
                boundary="runtime",
                operation="conversation_tracker.update",
                expected="ConversationTracker.update(user_id/user_input/assistant_answer/intent/runtime_pending)",
                error=exc,
                event_bus_instance=(
                    self._run_context.event_bus if self._run_context is not None else None
                ),
                diagnostics=(
                    self._run_context.diagnostics if self._run_context is not None else None
                ),
            )
            conversation_diagnostic = {
                "type": "contract_violation",
                "event_id": event.id,
                "failure": event.failure,
            }
        self._pending_execution_target = _runtime_pending_target(state)
        self.last_run_evidence = _build_run_evidence(
            state, final_answer, transitions,
        )
        if conversation_diagnostic:
            self.last_run_evidence["diagnostics"] = [conversation_diagnostic]
        self._print_timing_summary()
        return final_answer
