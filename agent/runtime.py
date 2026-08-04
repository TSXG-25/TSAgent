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
from agent.services import MemoryService, RepositoryService, ArtifactService
from agent.event_bus import event_bus
from agent.registry.skill_registry import skill_registry
from agent.state import AgentState
from agent.orchestrator import ExecutionOrchestrator
from agent.bootstrap import print_timings as print_bootstrap_timings

logger = logging.getLogger(__name__)

MAX_RUNTIME_SECONDS = float(os.getenv("TSAGENT_MAX_RUNTIME_SECONDS", "120"))
MAX_STATE_TRANSITIONS = int(os.getenv("TSAGENT_MAX_STATE_TRANSITIONS", "24"))
FACT_EXTRACTION_TIMEOUT = float(os.getenv("TSAGENT_FACT_TIMEOUT", "15"))


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

    def __init__(self, user_id: str = "default"):
        self.user_id = user_id
        self.orchestrator = ExecutionOrchestrator()
        self._timings: dict = {}
        event_bus.subscribe("task_end", self._on_task_end)

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
        event_bus.emit("runtime_recovered", {
            "layer": layer, "error_code": code, "error": str(e)[:300],
        })
        answer = f"抱歉，刚才在处理「{user_input[:30]}」时遇到了一点问题（{friendly}），请换一种说法再试试。"
        try:
            MemoryService.record_full_exchange(user_id, user_input, answer)
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
        """主入口：状态机循环。"""
        # ── 初始化 ──
        self._timings = {}
        self.orchestrator.reset_timings()
        self.orchestrator.replan_count = 0
        ArtifactService.clear()
        t0 = time.perf_counter()

        try:
            await asyncio.wait_for(
                MemoryService.extract_and_save_facts(self.user_id, user_input),
                timeout=FACT_EXTRACTION_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.warning("事实抽取超时，继续执行主任务")
        except Exception as exc:
            # Do not emit a traceback from an optional memory enhancement.
            logger.warning("事实抽取失败，继续执行主任务: %s", str(exc)[:200])
        MemoryService.record_user_message(self.user_id, user_input)

        from agent.query_normalizer import QueryNormalizer
        normalized_input = QueryNormalizer.process(user_input, self.user_id)
        context = MemoryService.get_context(self.user_id, normalized_input)
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
                        user_id=self.user_id,
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
                        state, normalized_input, self.user_id,
                    )
                    rt_state = RuntimeState[next_state] if isinstance(next_state, str) else next_state

                elif rt_state == RuntimeState.NEXT_TASK:
                    best_answer = await self.orchestrator.finalize(
                        state=state,
                        user_input=user_input,
                        user_id=self.user_id,
                    )
                    rt_state = RuntimeState.FINISH

                elif rt_state == RuntimeState.REPLAN:
                    # 已由 RECOVER 处理
                    pass
        except Exception as e:
            # P0.1: Runtime Recovery —— 不暴露 Traceback，Session 继续
            print(f"  ⚠️ Runtime 捕获异常并恢复: {type(e).__name__}")
            best_answer = self._recover(e, user_input, self.user_id)
            rt_state = RuntimeState.FINISH

        # ── 最终输出 ──
        final_answer = await self.orchestrator.finalize(
            state=state,
            user_input=user_input,
            user_id=self.user_id,
            best_answer=best_answer,
        )
        self._print_timing_summary()
        return final_answer
