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


class Finalizer:
    """最终答案生成器。

    持有对 ExecutionOrchestrator 容器的反向引用（访问 _timings）。
    """

    def __init__(self, orchestrator):
        self._orch = orchestrator

    async def run(
        self,
        state: AgentState,
        user_input: str,
        user_id: str,
        best_answer: Optional[str] = None,
    ) -> str:
        """生成最终答案并提交记忆。"""
        if best_answer:
            MemoryService.record_full_exchange(user_id, user_input, best_answer)
            return best_answer

        t_answer = time.perf_counter()
        final_answer = await generate_final_answer(state, user_input)
        MemoryService.record_full_exchange(user_id, user_input, final_answer)
        self._orch._timings["answer_gen"] = round(time.perf_counter() - t_answer, 3)
        return final_answer
