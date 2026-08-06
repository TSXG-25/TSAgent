"""Finalizer — 最终输出阶段。

职责：生成最终答案并提交记忆。
- 若已有 best_answer（Plan 阶段直答 / Workflow summary）→ 直接记录并返回
- 否则调用 AnswerGenerator 综合 artifacts 生成

Phase C.1：从 orchestrator.py 的 finalize() 迁移。
"""
import time
from typing import Optional

from agent.state import AgentState
from agent.answer_generator import generate_final_answer
from agent.services import MemoryService
from agent.task import Verb


class Finalizer:
    """最终答案生成器。

    持有对 ExecutionOrchestrator 容器的反向引用（访问 _timings）。
    """

    def __init__(self, orchestrator):
        self._orch = orchestrator

    def _memory(self):
        session_context = getattr(self._orch, "session_context", None)
        return getattr(session_context, "memory_view", None) or MemoryService

    async def run(
        self,
        state: AgentState,
        user_input: str,
        user_id: str,
        best_answer: Optional[str] = None,
    ) -> str:
        """生成最终答案并提交记忆。"""
        failure_answer = self._failure_answer(state)
        if failure_answer and not best_answer:
            self._memory().record_full_exchange(user_input, failure_answer)
            return failure_answer
        if best_answer:
            checked = self._verify_written_files(state, best_answer)
            if checked is not None:
                self._memory().record_full_exchange(user_input, checked)
                return checked
            self._memory().record_full_exchange(user_input, best_answer)
            return best_answer

        t_answer = time.perf_counter()
        final_answer = await generate_final_answer(state, user_input)
        checked = self._verify_written_files(state, final_answer)
        if checked is not None:
            final_answer = checked
        self._memory().record_full_exchange(user_input, final_answer)
        self._orch._timings["answer_gen"] = round(time.perf_counter() - t_answer, 3)
        return final_answer

    @staticmethod
    def _failure_answer(state: AgentState) -> Optional[str]:
        """Turn failed execution into an honest, actionable user-facing result."""
        failures = [
            str(task.get("error", ""))
            for task in (state.get("plan", []) or [])
            if task.get("status") == "failed" and task.get("error")
        ]
        if not failures:
            return None
        joined = "\n".join(failures)
        if (
            "本地执行默认关闭" in joined
            or "没有可用的隔离执行环境" in joined
            or "Office 二进制" in joined
        ):
            return (
                "当前环境没有可用的 Docker 沙箱，且本地执行已关闭，"
                "因此无法直接生成 Office 二进制文件。"
                "可以改为生成一个可审阅、可在本地运行的 Python 生成脚本。"
            )
        return f"任务未完成，未确认目标文件已成功写入：{failures[-1][:240]}"

    @staticmethod
    def _verify_written_files(state: AgentState, answer: str) -> Optional[str]:
        """假成功拦截：声称已写入/已生成但目标文件不存在的回答 → 如实纠正。

        以文件系统为准（确定性）；只有计划里存在 write 任务时才启用。
        """
        write_targets = []
        for task in state.get("plan", []) or []:
            if str(task.get("verb", "")) in ("write", Verb.WRITE.value) and task.get("target"):
                write_targets.append(str(task["target"]))
        if not write_targets:
            return None
        if not any(claim in answer for claim in ("已写入", "已保存", "已创建", "已生成", "已写出", "已追加")):
            return None
        from agent.executor.verifier import verify_write
        missing = [t for t in write_targets if not verify_write(t)]
        if missing:
            return (
                f"任务未完成：未能确认 {missing[0]} 已成功创建。"
                "请重试，或检查文件系统/沙箱权限。"
            )
        return None
