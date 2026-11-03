"""Finalizer — 最终输出阶段。

职责：生成最终答案并提交记忆。
- 若已有 best_answer（Plan 阶段直答 / Workflow summary）→ 直接记录并返回
- 否则调用 AnswerGenerator 综合 artifacts 生成

Phase C.1：从 orchestrator.py 的 finalize() 迁移。
"""
import re
import time
from pathlib import Path
from typing import Optional

from agent.state import AgentState
from agent.answer_generator import generate_final_answer
from agent.task import Verb
from agent.effect_truth import (
    effect_label,
    enforce_completion_gate,
    execution_truth,
    initialize_effect_contract,
)
from agent.goal import GoalState, GoalStatus, GoalVerifier
from agent.runtime_gates import has_fresh_evidence
from agent.registry.capability_registry import registry as _capability_registry


class Finalizer:
    """最终答案生成器。

    持有对 ExecutionOrchestrator 容器的反向引用（访问 _timings）。
    """

    def __init__(self, orchestrator):
        self._orch = orchestrator

    def _refresh_file_evidence(self, state: AgentState) -> None:
        run_context = getattr(self._orch, "run_context", None)
        workspace = getattr(run_context, "workspace", None)
        from agent.executor.verifier import snapshot_outputs

        records = {
            item["path"]: dict(item)
            for item in state.get("file_artifact_evidence", [])
        }
        for task in state.get("plan") or []:
            for path in task.get("consumed_files", []):
                records.pop(path, None)
            for item in task.get("verified_files", []):
                records[item["path"]] = {**item, "producer_task_id": task["id"]}
        state["file_artifact_evidence"] = list(records.values())
        recorded = {path: item["digest"] for path, item in records.items()}
        current = snapshot_outputs(list(recorded), workspace) if recorded else {}
        state["verified_artifact_paths"] = [
            path for path, digest in recorded.items() if current.get(path) == digest
        ]
        if state.get("artifact_required") and any(
            current.get(path) != digest for path, digest in recorded.items()
        ):
            state["runtime_failure_code"] = "ARTIFACT_CHANGED_AFTER_VERIFICATION"
            state["runtime_terminal_status"] = "FAILED_TERMINAL"

    def verify_goal(self, state: AgentState, answer: str):
        """Refresh filesystem facts and evaluate the same goal gate used at delivery."""
        self._refresh_file_evidence(state)
        goal = GoalState.from_dict(state.get("goal_state"))
        if goal.acceptance_criteria or any(
            item.acceptance_criteria for item in goal.delivery_requirements
        ):
            from agent.executor.verifier import verify_file_acceptance

            run_context = getattr(self._orch, "run_context", None)
            workspace = getattr(run_context, "workspace", None)
            state["acceptance_evidence"] = verify_file_acceptance(state, workspace)
        decision = GoalVerifier.verify(state, answer)
        state["goal_evidence"] = [item.to_dict() for item in decision.evidence]
        state["goal_missing"] = list(decision.missing)
        return decision

    def _project_goal_answer(self, state: AgentState, answer: str) -> str:
        """Allow final prose only after the whole-goal verifier accepts it."""
        decision = self.verify_goal(state, answer)
        goal_state = GoalState.from_dict(state.get("goal_state")).with_decision(
            decision
        )
        state["goal_state"] = goal_state.to_dict()
        if decision.can_complete:
            code_suffixes = {".py", ".js", ".jsx", ".ts", ".tsx", ".html"}
            generated_code = any(
                Path(str(path)).suffix.casefold() in code_suffixes
                for path in state.get("verified_artifact_paths", ())
            )
            if (
                "CODE_EXECUTION" in goal_state.prohibited_effects
                and generated_code
                and "运行效果尚未验证" not in answer
            ):
                answer = (
                    f"{answer.rstrip()}\n\n"
                    "按你的要求，本次未运行生成的程序；运行效果尚未验证。"
                )
            unverifiable = [
                item.description
                for item in (
                    *goal_state.acceptance_criteria,
                    *(
                        criterion
                        for requirement in goal_state.delivery_requirements
                        for criterion in requirement.acceptance_criteria
                    ),
                )
                if item.kind == "unverifiable"
            ]
            if unverifiable:
                answer = answer.rstrip() + "\n\n" + "\n".join(
                    f"未自动验证：{item}" for item in unverifiable
                )
            return answer
        if decision.status is GoalStatus.BLOCKED:
            state["runtime_terminal_status"] = "BLOCKED"
        elif state.get("runtime_terminal_status") in {"", "COMPLETED"}:
            state["runtime_terminal_status"] = "FAILED_TERMINAL"
        if not state.get("runtime_failure_code"):
            state["runtime_failure_code"] = decision.blocker or "GOAL_INCOMPLETE"
        if decision.blocker == "UNSUPPORTED_CAPABILITY" or state.get(
            "runtime_failure_code"
        ) == "UNSUPPORTED_CAPABILITY":
            effect_answer = Finalizer._effect_truth_failure_answer(
                state, execution_truth(state)
            )
            if effect_answer:
                return effect_answer
        missing = "、".join(decision.missing) or "完成证据"
        failure_detail = str(state.get("runtime_failure_detail", "") or "").strip()
        detail_suffix = f" 原因：{failure_detail}。" if failure_detail else ""
        partial = state.get("verified_artifact_paths", [])
        suffix = " 已完成并保留的文件：" + "、".join(partial) + "。" if partial else ""
        completed = [
            f"{task['id']}（{task.get('verb', '')} {task.get('target', '')}）"
            for task in state.get("plan") or []
            if task.get("status") == "succeeded"
        ]
        if completed:
            suffix += " 已完成的步骤：" + "、".join(completed) + "。"
        if str(state.get("runtime_failure_code", "")).startswith("PROVIDER_"):
            return f"LLM 服务调用失败（{state['runtime_failure_code']}），本次目标尚未完成。" + suffix
        return (
            f"目标尚未完成，缺少可验证证据：{missing}。"
            f"本次不会报告为成功。{detail_suffix}{suffix}"
        )

    async def run(
        self,
        state: AgentState,
        user_input: str,
        user_id: str,
        best_answer: Optional[str] = None,
    ) -> str:
        """生成最终答案并提交记忆。"""
        run_context = getattr(self._orch, "run_context", None)
        workspace = getattr(run_context, "workspace", None)
        self._refresh_file_evidence(state)
        # v2.3H2: final prose is never the source of truth for an external
        # effect.  Re-project the request here as a last boundary in case a
        # caller bypassed Planner or supplied a best_answer from an LLM.
        initialize_effect_contract(
            state,
            user_input,
            external_effects=state.get("intent_external_effects", ()),
            capability_resolver=_capability_registry.resolve,
        )
        # A clarification question is the legitimate terminal output; the
        # goal/evidence gates must not rewrite it into a failure answer.
        if state.get("conversation_clarification_required") and best_answer:
            return best_answer
        effect_truth = enforce_completion_gate(state)
        effect_failure = self._effect_truth_failure_answer(state, effect_truth)
        if effect_failure:
            effect_failure = self._project_goal_answer(state, effect_failure)
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
            answer = self._project_goal_answer(state, answer)
            return answer

        failure_answer = self._failure_answer(state)
        if failure_answer and not best_answer:
            failure_answer = self._project_goal_answer(state, failure_answer)
            return failure_answer
        run_context = getattr(self._orch, "run_context", None)
        workspace = getattr(run_context, "workspace", None)
        if state.get("artifact_required") and state.get("verified_artifact_paths"):
            assert workspace is not None, "verified artifacts require a scoped workspace"
            paths = [str(workspace.resolve_path(path, must_exist=True)) for path in state["verified_artifact_paths"]]
            manifest = "已生成/更新本地文件：\n" + "\n".join(paths)
            answer = f"{best_answer}\n\n{manifest}" if best_answer else manifest
            execution_answer = self._deterministic_completion_answer(state)
            if execution_answer and any(task.get("verb") == "execute" for task in (state.get("plan") or [])):
                answer += "\n" + execution_answer
            # File delivery and a requested read/explanation are independent
            # outputs. Publishing paths must not erase the terminal answers.
            tasks = state.get("plan", []) or []
            depended_on = {dep for task in tasks for dep in task.get("dependencies", [])}
            answer_tasks = {
                task["id"]: task for task in tasks
                if task["id"] not in depended_on
                and task.get("status") == "succeeded"
                and task.get("verb") in {"read", "list", "explain", "analyze"}
            }
            for item in (state.get("inbox") or {}).get("next_step", []):
                if item.get("task_id") in answer_tasks and item["result"].get("ok"):
                    answer += "\n\n" + str(item["result"].get("content", ""))
            answer = self._project_goal_answer(state, answer)
            return answer
        if best_answer:
            checked = self._verify_written_files(
                state,
                best_answer,
                workspace=workspace,
            )
            if checked is not None:
                checked = self._project_goal_answer(state, checked)
                return checked
            best_answer = self._project_goal_answer(state, best_answer)
            return best_answer

        deterministic_answer = self._deterministic_completion_answer(state)
        if deterministic_answer:
            deterministic_answer = self._project_goal_answer(
                state, deterministic_answer
            )
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
        final_answer = self._project_goal_answer(state, final_answer)
        self._orch._timings["answer_gen"] = round(time.perf_counter() - t_answer, 3)
        return final_answer

    @staticmethod
    def _effect_truth_failure_answer(state: AgentState, truth) -> Optional[str]:
        """Explain an unresolved effect without making a success claim."""

        if truth.unsupported_effects:
            requirement = truth.unsupported_effects[0]
            label = effect_label(requirement)
            return (
                f"当前没有可用的{label}能力，因此本次未执行该外部操作。"
                "没有可验证的执行证据，不能报告为已完成。"
            )
        if truth.unresolved_requested_outcomes:
            labels = {
                "FILE_READ": "文件读取",
                "FILE_MUTATION": "文件变更",
                "CODE_EXECUTION": "代码执行",
                "COMMAND_EXECUTION": "命令执行",
            }
            missing = "、".join(
                labels.get(item, item)
                for item in truth.unresolved_requested_outcomes
            )
            return (
                f"未获得{missing}的可验证执行证据，因此不能声称已经完成；"
                "本次不会将任务报告为成功。"
            )
        if not truth.unresolved_required_effects:
            return None
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
            output = str((task.get("facts") or {}).get("output", ""))
            target = str(task.get("target", ""))
            return f"已执行 {target}，真实输出：\n{output}" if output else f"已执行 {target}，stdout 为空。"
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
            return "当前 LLM 服务调用失败，本次目标尚未完成。"

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

        # A malformed/LLM-generated plan can omit its write Task; prose
        # alone is never trusted — the goal gate still requires verified
        # artifact evidence before the run can complete.
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
