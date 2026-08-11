"""Finalizer — 最终输出阶段。

职责：生成最终答案并提交记忆。
- 若已有 best_answer（Plan 阶段直答 / Workflow summary）→ 直接记录并返回
- 否则调用 AnswerGenerator 综合 artifacts 生成

Phase C.1：从 orchestrator.py 的 finalize() 迁移。
"""
import re
import time
from typing import Optional

from agent.state import AgentState
from agent.answer_generator import generate_final_answer
from agent.services import MemoryService
from agent.task import Verb
from agent.effect_truth import (
    effect_label,
    enforce_completion_gate,
    initialize_effect_contract,
)
from agent.runtime_gates import has_fresh_evidence
from agent.registry.capability_registry import registry as _capability_registry


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
        # v2.3H2: final prose is never the source of truth for an external
        # effect.  Re-project the request here as a last boundary in case a
        # caller bypassed Planner or supplied a best_answer from an LLM.
        initialize_effect_contract(
            state,
            user_input,
            capability_resolver=_capability_registry.resolve,
        )
        effect_truth = enforce_completion_gate(state)
        effect_failure = self._effect_truth_failure_answer(state, effect_truth)
        if effect_failure:
            self._memory().record_full_exchange(user_input, effect_failure)
            return effect_failure

        # H3: a temporal/source-grounded request may not fall through to an
        # LLM-only answer when the execution plan produced no source evidence.
        # This is a Runtime boundary, not a prompt preference.
        if (
            bool(state.get("freshness_required", False))
            or bool(state.get("source_grounding_required", False))
        ) and not has_fresh_evidence(state):
            state["runtime_terminal_status"] = "BLOCKED"
            state["runtime_failure_code"] = "RESEARCH_TOOL_UNAVAILABLE"
            answer = (
                "当前没有可核验的外部最新来源，因此不能可靠回答这项时效性问题；"
                "本次未生成无来源的当前信息。"
            )
            self._memory().record_full_exchange(user_input, answer)
            return answer

        failure_answer = self._failure_answer(state)
        if failure_answer and not best_answer:
            self._memory().record_full_exchange(user_input, failure_answer)
            return failure_answer
        run_context = getattr(self._orch, "run_context", None)
        workspace = getattr(run_context, "workspace", None)
        if best_answer:
            checked = self._verify_written_files(
                state,
                best_answer,
                workspace=workspace,
            )
            if checked is not None:
                self._memory().record_full_exchange(user_input, checked)
                return checked
            self._memory().record_full_exchange(user_input, best_answer)
            return best_answer

        deterministic_answer = self._deterministic_completion_answer(state)
        if deterministic_answer:
            self._memory().record_full_exchange(user_input, deterministic_answer)
            return deterministic_answer

        t_answer = time.perf_counter()
        final_answer = await generate_final_answer(state, user_input)
        checked = self._verify_written_files(
            state,
            final_answer,
            workspace=workspace,
        )
        if checked is not None:
            final_answer = checked
        self._memory().record_full_exchange(user_input, final_answer)
        self._orch._timings["answer_gen"] = round(time.perf_counter() - t_answer, 3)
        return final_answer

    @staticmethod
    def _effect_truth_failure_answer(state: AgentState, truth) -> Optional[str]:
        """Explain an unresolved effect without making a success claim."""

        if not truth.unresolved_required_effects:
            return None
        if truth.unsupported_effects:
            requirement = truth.unsupported_effects[0]
            label = effect_label(requirement)
            return (
                f"当前没有可用的{label}能力，因此本次未执行该外部操作。"
                "没有可验证的执行证据，不能报告为已完成。"
            )
        return (
            "外部操作尚未获得可验证的执行证据，因此不能确认已经完成；"
            "本次不会将任务报告为成功。"
        )

    @staticmethod
    def _deterministic_completion_answer(state: AgentState) -> Optional[str]:
        """Describe verified deterministic effects without another LLM call."""
        tasks = list(state.get("plan", []) or [])
        if not tasks or any(task.get("status") == "failed" for task in tasks):
            return None
        for task in tasks:
            inputs = task.get("inputs") or {}
            if inputs.get("operation") == "merge_unique_lines":
                count = (task.get("facts") or {}).get("duplicate_count", "0")
                return (
                    f"已合并并去重文本内容，结果已写入 {task.get('target', '')}；"
                    f"删除了 {count} 行重复内容。"
                )
        executed = [
            task for task in tasks
            if str(task.get("verb", "")) == Verb.EXECUTE.value
            and task.get("status") == "succeeded"
        ]
        if executed:
            task = executed[-1]
            output = str((task.get("facts") or {}).get("output", "")).strip()
            suffix = f"，运行输出：{output[:240]}" if output else ""
            return f"已创建并运行 {task.get('target', '')}{suffix}。"
        written = [
            str(task.get("target", ""))
            for task in tasks
            if str(task.get("verb", "")) == Verb.WRITE.value
            and task.get("status") == "succeeded"
            and task.get("target")
        ]
        if written:
            return "已成功写入：" + "、".join(written)
        return None

    @staticmethod
    def _failure_answer(state: AgentState) -> Optional[str]:
        """Turn failed execution into an honest, actionable user-facing result."""

        runtime_failure_code = str(state.get("runtime_failure_code", "") or "")
        if runtime_failure_code == "INVALID_REQUEST":
            return "当前输入为空或仅包含标点，请提供具体问题或任务。"
        if runtime_failure_code.startswith("PROVIDER_"):
            return "当前 LLM 服务暂时不可用，本次未生成或执行任务。"

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
        if (
            "RESEARCH_TOOL_UNAVAILABLE" in joined
            or "网络搜索功能不可用" in joined
            or "未找到关于" in joined
        ):
            return (
                "当前无法访问可用的外部检索来源，因此不能可靠回答这项近期市场研究；"
                "本次未生成无来源的股票或热点推荐。"
            )
        if "UNSUPPORTED_CAPABILITY" in joined:
            return (
                "当前未注册所需的能力，任务已阻止；没有执行未授权的外部操作，"
                "也没有伪造成功结果。"
            )
        return f"任务未完成，未确认目标文件已成功写入：{failures[-1][:240]}"

    @staticmethod
    def _verify_written_files(
        state: AgentState,
        answer: str,
        *,
        workspace=None,
    ) -> Optional[str]:
        """假成功拦截：声称已写入/已生成但目标文件不存在的回答 → 如实纠正。

        以文件系统为准（确定性）；只有计划里存在 write 任务时才启用。
        """
        write_targets = []
        for task in state.get("plan", []) or []:
            if str(task.get("verb", "")) in ("write", Verb.WRITE.value) and task.get("target"):
                write_targets.append(str(task["target"]))

        # A malformed/LLM-generated plan can omit its write Task.  Successful
        # prose still cannot be trusted: extract only affirmative write claims
        # and verify those paths independently of the plan projection.
        claim_pattern = re.compile(
            r"(?:已|已经|成功|并)\s*(?:写入|保存|创建|生成|写出|追加)"
            r"(?:到|为|成)?\s*[`\"']?"
            r"((?:/?[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.[A-Za-z0-9]+)"
        )
        claimed_targets = claim_pattern.findall(answer or "")
        has_write_claim = bool(claimed_targets) or any(
            claim in (answer or "")
            for claim in (
                "已写入", "已保存", "已创建", "已生成", "已写出", "已追加",
                "成功写入", "成功保存", "成功创建", "成功生成",
            )
        )
        if not has_write_claim:
            return None
        for target in claimed_targets:
            if target not in write_targets:
                write_targets.append(target)

        if not write_targets:
            return None
        from agent.executor.verifier import verify_write
        missing = [
            t for t in write_targets
            if not verify_write(t, workspace=workspace)
        ]
        if missing:
            return (
                f"任务未完成：未能确认 {missing[0]} 已成功创建。"
                "请重试，或检查文件系统/沙箱权限。"
            )
        return None
