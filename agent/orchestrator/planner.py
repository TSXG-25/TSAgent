"""PlannerStage — PLAN 阶段编排。

认知链路：
    User Input
        │
        ▼
    ContextBuilder.build → CognitiveContext
        │
        ▼
    ReferenceResolver.resolve（确定性消歧）
        │
        ▼
    IntentEngine.analyze（意图理解）
        │
        ▼
    WorkflowRouter.route（命中 → WorkflowExecutor / 未命中 → Planner）
        │
        ▼
    Compiler.compile（Task → ExecutionPlan）

REPLAN：保留成功任务的 Facts，重新规划失败任务。

Phase C.1：从 orchestrator.py 的 plan() / replan() / _print_plan() 迁移。
"""
import re
import time
import json
from datetime import datetime
from typing import Any, Dict, Mapping, Optional, Tuple
from langchain_core.messages import SystemMessage, HumanMessage

from agent.state import AgentState
from agent.planner.planner import plan_with_metadata
from agent.task import ExecutionPlan, ExecutionStep, Task, Verb
from agent.compiler.context import CompilerContext
from agent.registry.tool_registry import registry as _tool_registry
from agent.registry.capability_registry import registry as _capability_registry
from agent.registry.workflow_registry import workflow_registry
from agent.cognition.intent_schema import (
    DOMAIN_CHAT,
    DOMAIN_DEVELOPMENT,
    DOMAIN_MEMORY,
    IntentResult,
)
from agent.execution_errors import (
    classify_execution_error,
    is_non_retriable,
    stable_error_message,
)
from agent.cognition.research_policy import research_query, research_timeliness
from agent.cognition.execution_need import (
    RequestedOutcome,
    analyze_requested_outcomes,
    extract_explicit_command,
)
from agent.cognition.resource_binding import (
    extract_bound_targets,
    extract_explicit_paths,
)
from agent.context_policy import ContextMode, ContextPolicy
from agent.inbox import AgentInbox
from agent.tool_identity import registry_tool_name
from agent.workflow_decision import WorkflowDecisionKind
from agent.workflow_selector import (
    WorkflowContextProjection,
    WorkflowDefinitionProjection,
    WorkflowSelectionError,
)


def _get_memory_service():
    from agent.services import MemoryService

    return MemoryService


def _get_skill_registry():
    from agent.registry.skill_registry import skill_registry

    return skill_registry


def _get_workflow_router():
    from agent.router.workflow_router import router

    return router


def _workflow_route_requested(intent: Any) -> bool:
    """Keep the optional Workflow stack off ordinary Planner requests."""
    raw = str(getattr(intent, "raw_input", "") or "").lower()
    return bool(
        getattr(intent, "domain", "") == DOMAIN_DEVELOPMENT
        and getattr(intent, "action", "") == "code"
        and any(
            token in raw
            for token in (
                "题目",
                "解题",
                "算法题",
                "编程题",
                "question.docx",
                "question file",
            )
        )
    )


def _get_intent_engine():
    from agent.cognition.intent_engine import engine

    return engine


class _LazyIntentEngine:
    """Patchable compatibility handle without importing the Provider stack."""

    def analyze(self, *args, **kwargs):
        return _get_intent_engine().analyze(*args, **kwargs)

    async def analyze_async(self, *args, **kwargs):
        return await _get_intent_engine().analyze_async(*args, **kwargs)


class _LazySkillRegistry:
    """Patchable registry handle with lazy embedding/skill imports."""

    def select(self, *args, **kwargs):
        return _get_skill_registry().select(*args, **kwargs)


intent_engine = _LazyIntentEngine()
skill_registry = _LazySkillRegistry()


_REPLAN_EFFECT_VERBS = frozenset(
    {
        Verb.WRITE.value,
        Verb.MODIFY.value,
        Verb.DELETE.value,
        Verb.MOVE.value,
        Verb.COPY.value,
        Verb.EXECUTE.value,
    }
)


def _normalized_effect_value(value: Any) -> str:
    return str(value or "").strip().replace("\\", "/").casefold()


def _apply_planner_failure(
    state: AgentState,
    plan_output: Any,
) -> Optional[Tuple[AgentState, str, Optional[str]]]:
    """Turn a stable Planner failure into a terminal Runtime fact."""

    failure_code = str(getattr(plan_output, "failure_code", "") or "")
    if not failure_code:
        return None
    state["runtime_failure_code"] = failure_code
    state["runtime_failure_class"] = (
        "provider" if failure_code.startswith("PROVIDER_") else "planning"
    )
    state["runtime_failure_retryable"] = failure_code in {
        "PROVIDER_TIMEOUT",
        "PROVIDER_NETWORK",
        "PROVIDER_UNAVAILABLE",
    }
    state["runtime_terminal_status"] = "FAILED_TERMINAL"
    message = str(
        getattr(plan_output, "failure_message", "")
        or "Planner 未能生成可执行计划。"
    )
    return state, "FAIL", message


def _task_effect_signature(task: Mapping[str, Any]) -> tuple[str, ...] | None:
    """Return a content-free identity for a potentially effectful Task."""

    raw_verb = task.get("verb", "")
    verb = (
        raw_verb.value
        if isinstance(raw_verb, Verb)
        else str(raw_verb or "").strip().lower()
    )
    if verb not in _REPLAN_EFFECT_VERBS:
        return None
    raw_inputs = task.get("inputs") or {}
    inputs = raw_inputs if isinstance(raw_inputs, Mapping) else {}
    if verb in {Verb.COPY.value, Verb.MOVE.value}:
        source = _normalized_effect_value(
            inputs.get("source", inputs.get("src", ""))
        )
        destination = _normalized_effect_value(
            inputs.get(
                "destination",
                inputs.get("dst", task.get("target", "")),
            )
        )
        return (verb, source, destination) if source or destination else None
    if verb == Verb.EXECUTE.value:
        command = _normalized_effect_value(
            task.get("target")
            or inputs.get("command")
            or inputs.get("script")
            or inputs.get("spec")
        )
        return (verb, command) if command else None
    target = _normalized_effect_value(
        task.get("target") or inputs.get("path")
    )
    return (verb, target) if target else None


def _reconcile_replan_tasks(
    current_plan: list[dict[str, Any]],
    proposed_plan: list[dict[str, Any]],
    *,
    replan_attempt: int,
) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    """Remove already verified effects and mint collision-free replacement IDs."""

    succeeded = [
        task for task in current_plan if task.get("status") == "succeeded"
    ]
    succeeded_ids = {
        str(task.get("id", "")).strip() for task in succeeded
    }
    current_ids = {
        str(task.get("id", "")).strip()
        for task in current_plan
        if str(task.get("id", "")).strip()
    }
    unfinished_ids = {
        str(task.get("id", "")).strip()
        for task in current_plan
        if task.get("status") in {"pending", "running"}
        and str(task.get("id", "")).strip()
    }
    seen_effects = {
        signature
        for task in succeeded
        if (signature := _task_effect_signature(task)) is not None
    }
    used_ids = set(current_ids)
    kept: list[tuple[str, dict[str, Any]]] = []
    id_map: dict[str, str] = {}
    skipped_ids: set[str] = set()

    for index, original in enumerate(proposed_plan, 1):
        task = dict(original)
        original_id = str(task.get("id", "") or f"task-{index}").strip()
        signature = _task_effect_signature(task)
        if signature is not None and signature in seen_effects:
            skipped_ids.add(original_id)
            continue
        if signature is not None:
            seen_effects.add(signature)

        candidate = f"replan-{replan_attempt}-{index}"
        suffix = 1
        while candidate in used_ids:
            suffix += 1
            candidate = f"replan-{replan_attempt}-{index}-{suffix}"
        used_ids.add(candidate)
        id_map.setdefault(original_id, candidate)
        task["id"] = candidate
        kept.append((original_id, task))

    for _original_id, task in kept:
        dependencies = task.get("dependencies") or []
        remapped: list[str] = []
        for dependency in dependencies:
            dependency_id = str(dependency).strip()
            if not dependency_id:
                continue
            if dependency_id in id_map:
                resolved = id_map[dependency_id]
            elif dependency_id in skipped_ids or dependency_id in succeeded_ids:
                # The dependency is already satisfied by a verified effect.
                continue
            elif dependency_id in unfinished_ids:
                resolved = dependency_id
            elif dependency_id in current_ids:
                # Failed/retired Tasks are replaced by this new plan and must
                # not remain as dangling dependency IDs.
                continue
            else:
                resolved = dependency_id
            if resolved != task["id"] and resolved not in remapped:
                remapped.append(resolved)
        task["dependencies"] = remapped

    return [task for _original_id, task in kept], tuple(sorted(skipped_ids))


def _render_runtime_continuation(state: AgentState) -> str:
    """CONTINUE_PLAN（继续）：从 Runtime 状态恢复当前执行，不占 Conversation 边界。

    Conversation 负责 recent_goal/last_answer；当前 plan/task 属于 Runtime。
    """
    plan = state.get("plan", []) or []
    active = [t for t in plan if t.get("status") in ("pending", "running")]
    if not active:
        return ""
    parts = ["## 当前执行（继续此前的任务，必须基于此回答）"]
    for t in active[:3]:
        goal = str(t.get("goal", "")).strip()
        if goal:
            parts.append(f"- 进行中任务: {goal}")
    return "\n".join(parts) if len(parts) > 1 else ""


def _normalize_continuation_target(target: str) -> str:
    return str(target or "").strip().replace("\\", "/").casefold().rstrip("/")


def _record_contract_diagnostic(
    state: AgentState,
    operation: str,
    error: Exception,
    *,
    run_context=None,
) -> None:
    from agent.diagnostics import handle_contract_violation

    event = handle_contract_violation(
        boundary="planner",
        operation=operation,
        expected="ConversationRetrieverProtocol.get/snapshot/runtime_pending/events",
        error=error,
        event_bus_instance=(run_context.event_bus if run_context is not None else None),
        diagnostics=(run_context.diagnostics if run_context is not None else None),
    )
    state.setdefault("diagnostics", []).append({
        "type": "contract_violation",
        "event_id": event.id,
        "failure": event.failure,
    })


def _apply_conversation_contract(
    intent,
    user_id: str,
    user_input: str,
    *,
    pending_target: str = "",
    reference_target: str = "",
    conversation_retriever=None,
):
    """Apply ADR-0013 continuation semantics before routing.

    The Conversation Runtime supplies only the previous ``runtime_pending`` bit;
    it does not own or reconstruct a plan.  Explicit continuation phrases are
    classified by IntentEngine, while a bare "继续" is resolved against that bit.
    """
    from agent.conversation import (
        ConversationIntent,
        classify_conversation_intent,
    )
    retriever = conversation_retriever
    if retriever is None:
        from agent.conversation import ConversationRetriever, ConversationTracker
        retriever = ConversationRetriever(ConversationTracker())

    kind = classify_conversation_intent(
        intent,
        user_input,
        runtime_pending=retriever.runtime_pending(user_id),
    )
    if kind is ConversationIntent.CONTINUE_PLAN:
        intent.domain = DOMAIN_DEVELOPMENT
        intent.action = "continue_plan"
        intent.requires_execution = True
        intent.reference_kind = "runtime"
    elif kind is ConversationIntent.CONTINUE_CHAT:
        intent.domain = DOMAIN_CHAT
        intent.action = "continue_chat"
        intent.requires_execution = False
        intent.reference_kind = "answer"
    elif kind is ConversationIntent.CONTINUE_REFERENCE:
        if (
            pending_target
            and reference_target
            and _normalize_continuation_target(pending_target)
            != _normalize_continuation_target(reference_target)
        ):
            intent.domain = DOMAIN_CHAT
            intent.action = "clarify"
            intent.requires_execution = False
            intent.reference_kind = "instruction"
            intent.summary = "引用目标与未完成执行目标冲突，需要澄清"
            return kind
        intent.domain = DOMAIN_MEMORY
        intent.action = "reference"
        intent.requires_execution = False
        intent.reference_kind = "instruction"
    return kind


def _extract_explicit_output_path(user_input: str) -> Optional[str]:
    """Extract the user-requested output path, without inventing a default."""
    paths = _extract_explicit_output_paths(user_input)
    return paths[-1] if paths else None


def _extract_explicit_output_paths(user_input: str) -> list[str]:
    """Extract all explicit output paths in their input order."""
    paths: list[str] = []
    for normalized in extract_explicit_paths(user_input):
        if normalized.lower().startswith("input/") or normalized.lower().endswith("question.docx"):
            continue
        if normalized.casefold() not in {item.casefold() for item in paths}:
            paths.append(normalized)
    return paths


_OUTPUT_MATERIALIZATION_RE = re.compile(
    r"保存到|保存为|写入到|写到|输出到|落盘|另存为|写成|生成|创建|新建"
)
_TEXT_OUTPUT_SUFFIXES = (
    ".py", ".txt", ".md", ".json", ".csv", ".yaml", ".yml",
    ".js", ".ts", ".rs", ".go", ".java", ".sh", ".html", ".css",
)


def _ensure_explicit_output_write_task(
    plan: list[dict],
    user_input: str,
    intent=None,
) -> list[dict]:
    """Guarantee an explicit text-file output request reaches the write chain.

    The LLM Planner may correctly create search/explain tasks while omitting the
    world-state mutation implied by ``保存到 output/example.py``.  That omission
    is a contract violation, not a reason to trust the final prose.  Add one
    canonical ``write`` Task after the preceding tasks so the existing WriteRule
    and ExecutionVerifier own materialization and truthfulness.
    """
    if intent is not None and not getattr(intent, "requires_execution", False):
        return plan
    if not _OUTPUT_MATERIALIZATION_RE.search(user_input or ""):
        return plan

    targets = [
        target for target in _extract_explicit_output_paths(user_input)
        if target.lower().endswith(_TEXT_OUTPUT_SUFFIXES)
    ]
    if not targets:
        # Office/binary targets retain their dedicated degradation path.
        return plan

    write_tasks = [
        task for task in plan
        if str(task.get("verb", "")).lower() == Verb.WRITE.value
    ]
    source_grounded = bool(
        intent is not None
        and (
            getattr(intent, "source_grounding_required", False)
            or getattr(intent, "action", "") == "fresh_research"
        )
    )

    def configure_write(task: dict, target: str) -> None:
        inputs = dict(task.get("inputs") or {})
        inputs["use_prior_facts"] = True
        if source_grounded:
            name = target.replace("\\", "/").rsplit("/", 1)[-1].casefold()
            inputs["research_output_format"] = (
                "sources_json"
                if target.casefold().endswith(".json") and "source" in name
                else "markdown_summary"
            )
        task["inputs"] = inputs

    normalized_targets = {
        target.casefold().rstrip("/") for target in targets
    }
    assigned_targets = {
        str(task.get("target", "")).replace("\\", "/").casefold().rstrip("/")
        for task in write_tasks
        if task.get("target")
    }
    for index, target in enumerate(targets):
        normalized_target = target.casefold().rstrip("/")
        if normalized_target in assigned_targets:
            continue
        # Reuse an unbound Planner write task before appending a new one.
        reusable = next(
            (
                task for task in write_tasks
                if not task.get("target")
                or str(task.get("target", "")).replace("\\", "/").casefold().rstrip("/")
                not in normalized_targets
            ),
            None,
        )
        if reusable is not None:
            reusable["target"] = target
            reusable["target_type"] = "file"
            configure_write(reusable, target)
            assigned_targets.add(normalized_target)

    for task in write_tasks:
        target = str(task.get("target", "")).replace("\\", "/")
        if target.casefold().rstrip("/") in normalized_targets:
            configure_write(task, target)

    missing_targets = [
        target for target in targets
        if target.casefold().rstrip("/") not in assigned_targets
    ]
    if not missing_targets:
        return plan

    existing_ids = [str(task.get("id", "")) for task in plan if task.get("id")]
    base_dependencies = list(existing_ids)
    next_number = len(plan) + 1
    for target in missing_targets:
        next_id = f"task-{next_number}"
        while next_id in existing_ids:
            next_number += 1
            next_id = f"task-{next_number}"
        plan.append({
            "id": next_id,
            "verb": Verb.WRITE.value,
            "target": target,
            "target_type": "file",
            "goal": f"根据用户需求生成内容并保存到 {target}",
            "description": (
                "这是用户明确要求的文件落盘步骤。保留前置检索/分析结果，"
                "只输出目标文件的完整内容并实际写入。\n"
                f"原始需求：{user_input}"
            ),
            "success_condition": f"文件 {target} 存在且非空",
            "dependencies": base_dependencies,
            "children": [],
            "inputs": {},
            "status": "pending",
            "observations": [],
            "error": "",
        })
        configure_write(plan[-1], target)
        existing_ids.append(next_id)
        next_number += 1
    return plan


def _extract_literal_file_write(
    user_input: str,
    *,
    allow_execution: bool = False,
) -> Optional[tuple[str, str]]:
    """Extract a complete one-file write request without invoking an LLM.

    This fast path is deliberately narrow: it only accepts one explicit text
    path and a literal ``内容为/内容是`` clause, with no research, read, or
    execution verb.  The resulting Task still goes through Compiler,
    PlanExecutor, and ExecutionVerifier.
    """
    value = str(user_input or "").strip()
    if not value or len(_extract_explicit_output_paths(value)) != 1:
        return None
    if re.search(r"搜索|检索|查找|读取|分析|合并|根据|然后", value):
        return None
    if not allow_execution and re.search(r"运行|执行", value):
        return None
    if not re.search(r"创建|新建|写入|写到|保存到|输出到|生成", value):
        return None
    match = re.search(r"内容(?:为|是|：|:)?\s*(.+?)\s*$", value, re.DOTALL)
    if match is None and allow_execution:
        match = re.search(
            r"(?:把|将)\s*(.+?)\s*(?:写入|写到|保存到|输出到)\s*",
            value,
            re.DOTALL,
        )
    if match is None:
        return None
    target = _extract_explicit_output_paths(value)[0]
    if not target.lower().endswith(_TEXT_OUTPUT_SUFFIXES):
        return None
    content = match.group(1).strip().strip("`\"'")
    if not content:
        return None
    return target, content


def _extract_literal_file_append(user_input: str) -> Optional[tuple[str, str]]:
    """Extract a complete one-line append request without an LLM round-trip."""
    value = str(user_input or "").strip()
    paths = _extract_explicit_output_paths(value)
    if len(paths) != 1 or not re.search(r"追加|附加|append", value, re.IGNORECASE):
        return None
    match = re.search(
        r"(?:追加|附加)(?:一行|一条)?\s*(?:内容为|内容是|[:：])?\s*([A-Za-z0-9][\w ._-]*)",
        value,
    )
    if match is None:
        match = re.search(r"append\s+([A-Za-z0-9][\w ._-]*)", value, re.IGNORECASE)
    if match is None:
        return None
    content = match.group(1).strip().rstrip("。；;，,")
    if not content:
        return None
    return paths[0], content


def _build_text_merge_execution(
    user_input: str,
) -> Optional[tuple[Task, ExecutionPlan]]:
    """Compile an explicit text merge/deduplicate request deterministically."""
    value = str(user_input or "")
    if not re.search(r"合并.*去重|去重.*合并", value):
        return None
    if not re.search(r"保存到|保存为|写入到|写到|输出到", value):
        return None
    paths = [
        path for path in _extract_explicit_output_paths(value)
        if path.casefold().endswith((".txt", ".csv", ".md"))
    ]
    if len(paths) < 3:
        return None
    target = paths[-1]
    sources = paths[:-1]
    if target in sources:
        return None
    task = Task.from_dict({
        "id": "task-1",
        "verb": Verb.WRITE.value,
        "target": target,
        "target_type": "file",
        "goal": f"合并并去重 {len(sources)} 个文本文件，保存到 {target}",
        "description": value,
        "success_condition": f"文件 {target} 存在、非空且内容已去重",
        "dependencies": [],
        "children": [],
        "inputs": {
            "operation": "merge_unique_lines",
            "sources": sources,
        },
    })
    steps: list[ExecutionStep] = []
    merge_args: dict[str, str] = {}
    for index, source in enumerate(sources, 1):
        path_key = f"source_path_{index}"
        content_key = f"content_{index}"
        steps.extend((
            ExecutionStep(
                tool="workspace",
                args={"spec": source},
                outputs=[path_key],
            ),
            ExecutionStep(
                tool="filesystem.read",
                args={"path": f"${path_key}"},
                outputs=[content_key],
            ),
        ))
        merge_args[content_key] = f"${content_key}"
    steps.extend((
        ExecutionStep(
            tool="text.merge_unique",
            args=merge_args,
            outputs=["content", "duplicate_count", "unique_line_count"],
        ),
        ExecutionStep(
            tool="workspace",
            args={"spec": target, "operation": "write"},
            outputs=["output_path"],
        ),
        ExecutionStep(
            tool="filesystem.write",
            args={
                "path": "$output_path",
                "content": "$content",
                "mode": "overwrite",
                "exact": True,
            },
            outputs=["result"],
        ),
    ))
    return task, ExecutionPlan(task=task, steps=steps)


def _build_code_run_tasks(user_input: str) -> Optional[list[Task]]:
    """Build the minimal write→run chain for one explicit Python target."""
    value = str(user_input or "")
    paths = [
        path for path in _extract_explicit_output_paths(value)
        if path.casefold().endswith(".py")
    ]
    if len(paths) != 1:
        return None
    if not re.search(r"创建|新建|生成|写入|写到", value):
        return None
    if not re.search(r"运行|执行", value):
        return None
    target = paths[0]
    literal_write = _extract_literal_file_write(value, allow_execution=True)
    literal_content = (
        literal_write[1]
        if literal_write is not None and literal_write[0].casefold() == target.casefold()
        else None
    )
    write_task = Task.from_dict({
        "id": "task-1",
        "verb": Verb.WRITE.value,
        "target": target,
        "target_type": "file",
        "goal": f"根据完整需求生成可运行的 Python 程序并写入 {target}",
        "description": value,
        "success_condition": f"文件 {target} 存在且非空",
        "dependencies": [],
        "children": [],
        **({"inputs": {"content": literal_content}} if literal_content else {}),
    })
    execute_task = Task.from_dict({
        "id": "task-2",
        "verb": Verb.EXECUTE.value,
        "target": target,
        "target_type": "file",
        "goal": f"运行 {target} 并验证输出满足原始需求",
        "description": value,
        "success_condition": "程序成功运行并产生预期输出",
        "dependencies": ["task-1"],
        "children": [],
    })
    return [write_task, execute_task]


def _build_source_code_execution_task(user_input: str) -> Optional[Task]:
    """Build source-direct Python execution when no persistent target is named."""

    value = str(user_input or "").strip()
    if RequestedOutcome.CODE_EXECUTION not in analyze_requested_outcomes(value):
        return None
    if extract_bound_targets(value):
        return None
    if any(path.casefold().endswith(".py") for path in extract_explicit_paths(value)):
        return None
    return Task.from_dict({
        "id": "task-1",
        "verb": Verb.EXECUTE.value,
        "target": "python-source",
        "target_type": "text",
        "goal": "生成并执行满足用户要求的 Python 源码",
        "description": value,
        "success_condition": "Python 程序成功执行并返回输出",
        "inputs": {
            "generate_code": True,
            "code_request": value,
        },
        "policy": {
            "executor": "tool",
            "tool_policy": {"allow": ["run_python"]},
        },
    })


def _build_explicit_command_execution_task(user_input: str) -> Optional[Task]:
    """Build a shell task only from a command explicitly supplied by the user."""

    command = extract_explicit_command(user_input)
    if command is None:
        return None

    return Task.from_dict({
        "id": "task-1",
        "verb": Verb.EXECUTE.value,
        "target": command,
        "target_type": "text",
        "goal": f"执行用户指定命令：{command}",
        "description": "命令由用户明确提供，直接进入 shell 执行并记录真实输出。",
        "success_condition": "命令执行成功并产生可核验的执行证据",
        "inputs": {},
        "policy": {"executor": "tool"},
    })


def _build_file_operation_task(user_input: str) -> Optional[Task]:
    """Extract an explicit copy/move/delete request into one canonical Task."""
    value = str(user_input or "")
    paths = _extract_explicit_output_paths(value)
    if re.search(r"(?:复制|拷贝|copy)", value, re.IGNORECASE) and len(paths) >= 2:
        return Task.from_dict({
            "id": "task-1",
            "verb": Verb.COPY.value,
            "target": paths[1],
            "target_type": "file",
            "goal": f"复制 {paths[0]} 到 {paths[1]}",
            "description": value,
            "success_condition": f"{paths[1]} 存在且与源文件内容一致",
            "inputs": {"source": paths[0], "destination": paths[1]},
        })
    if re.search(r"(?:移动|move)", value, re.IGNORECASE) and len(paths) >= 2:
        return Task.from_dict({
            "id": "task-1",
            "verb": Verb.MOVE.value,
            "target": paths[1],
            "target_type": "file",
            "goal": f"移动 {paths[0]} 到 {paths[1]}",
            "description": value,
            "success_condition": f"{paths[0]} 不存在且 {paths[1]} 存在",
            "inputs": {"source": paths[0], "destination": paths[1]},
        })
    if re.search(r"(?:删除|移除|delete)", value, re.IGNORECASE) and paths:
        return Task.from_dict({
            "id": "task-1",
            "verb": Verb.DELETE.value,
            "target": paths[0],
            "target_type": "file",
            "goal": f"删除 {paths[0]}",
            "description": value,
            "success_condition": f"{paths[0]} 不存在",
        })
    return None


def _build_text_transform_execution(
    user_input: str,
) -> Optional[tuple[Task, ExecutionPlan]]:
    """Build a deterministic read → transform → write chain for text files."""
    value = str(user_input or "")
    if not re.search(r"每行.*(?:大写|转成大写|转换为大写)|(?:转成|转换为)大写", value):
        return None
    paths = [
        path for path in _extract_explicit_output_paths(value)
        if path.casefold().endswith((".txt", ".csv", ".md"))
    ]
    if len(paths) < 2:
        return None
    source, target = paths[0], paths[-1]
    if source.casefold() == target.casefold():
        return None
    task = Task.from_dict({
        "id": "task-1",
        "verb": Verb.WRITE.value,
        "target": target,
        "target_type": "file",
        "goal": f"读取 {source}，逐行转成大写并写入 {target}",
        "description": value,
        "success_condition": f"{target} 存在、非空且内容为大写",
        "inputs": {"operation": "uppercase_lines", "source": source},
    })
    steps = [
        ExecutionStep(
            tool="workspace",
            args={"spec": source, "operation": "source"},
            outputs=["source_path"],
        ),
        ExecutionStep(
            tool="filesystem.read",
            args={"path": "$source_path"},
            outputs=["source_content"],
        ),
        ExecutionStep(
            tool="text.transform_upper",
            args={"content": "$source_content"},
            outputs=["content"],
        ),
        ExecutionStep(
            tool="workspace",
            args={"spec": target, "operation": "write"},
            outputs=["output_path"],
        ),
        ExecutionStep(
            tool="filesystem.write",
            args={
                "path": "$output_path",
                "content": "$content",
                "mode": "overwrite",
                "exact": True,
            },
            outputs=["result"],
        ),
    ]
    return task, ExecutionPlan(task=task, steps=steps)


def _build_modify_execution_tasks(user_input: str) -> Optional[list[Task]]:
    """Build a stable modify→optional-verify chain for explicit Python edits."""
    value = str(user_input or "")
    if not re.search(r"修复|修改|新增|增加|改成|改为|添加", value):
        return None
    paths = [
        path for path in _extract_explicit_output_paths(value)
        if path.casefold().endswith(".py")
    ]
    if not paths:
        return None
    target = paths[0]
    modify = Task.from_dict({
        "id": "task-1",
        "verb": Verb.MODIFY.value,
        "target": target,
        "target_type": "file",
        "goal": value,
        "description": (
            "只修改用户明确要求的符号或行为；保留未请求的函数、导入和代码区域。"
            f"\n原始请求：{value}"
        ),
        "success_condition": f"{target} 已按请求修改且未破坏未请求代码",
        "inputs": {"requested_scope": value},
    })
    if not re.search(r"运行|测试|验证", value):
        return [modify]
    verification_target = paths[1] if len(paths) >= 2 else target
    verification_inputs: dict[str, str] = {}
    if (
        len(paths) == 1
        and re.search(r"一行\s*(?:Python|代码)|大写.*感叹号", value, re.IGNORECASE)
    ):
        symbol_match = re.search(
            r"([A-Za-z_]\w*)\s*(?:函数|方法)",
            value,
        )
        if symbol_match:
            module = target[:-3].replace("/", ".").replace("\\", ".")
            symbol = symbol_match.group(1)
            verification_inputs["verification_code"] = (
                f"from {module} import {symbol}; "
                f"assert {symbol}('hi') == 'HI!'; print('PASS')"
            )
    execute = Task.from_dict({
        "id": "task-2",
        "verb": Verb.EXECUTE.value,
        "target": verification_target,
        "target_type": "file",
        "goal": f"运行 {verification_target} 验证刚才的修改",
        "description": value,
        "success_condition": "验证命令成功完成",
        "dependencies": ["task-1"],
        "inputs": verification_inputs,
    })
    return [modify, execute]


def _explicit_unsupported_capability(user_input: str) -> Optional[str]:
    """Return an explicitly requested but unregistered capability/tool."""
    value = str(user_input or "")
    match = re.search(
        r"(?:使用|调用|用)\s+([A-Za-z][A-Za-z0-9_-]*)\s*工具",
        value,
        re.IGNORECASE,
    )
    if match:
        name = match.group(1)
        if _tool_registry.get(name) is None:
            return name
    if re.search(r"(?:发送|发)\s*邮件|send[_ -]?email", value, re.IGNORECASE):
        if not any("email" in name.lower() for name in _tool_registry.get_all()):
            return "email"
    return None


class PlannerStage:
    """PLAN 阶段规划器。

    持有对 ExecutionOrchestrator 容器的反向引用
    （访问 _timings / _selector / _reference_resolver / _conversation_state / replan_count）。
    """

    def __init__(self, orchestrator):
        self._orch = orchestrator

    def _memory_view(self):
        session_context = getattr(self._orch, "session_context", None)
        return getattr(session_context, "memory_view", None)

    def _record_exchange(self, user_id: str, user_input: str, answer: str) -> None:
        memory_view = self._memory_view()
        if memory_view is not None:
            memory_view.record_full_exchange(user_input, answer)
        else:
            _get_memory_service().record_full_exchange(user_id, user_input, answer)

    def _record_resolution(
        self,
        user_id: str,
        utterance: str,
        resolved_target: str,
        kind: str,
    ) -> None:
        memory_view = self._memory_view()
        if memory_view is not None:
            memory_view.record_resolution(utterance, resolved_target, kind)
        else:
            _get_memory_service().record_resolution(user_id, utterance, resolved_target, kind)

    def _get_user_facts(self, user_id: str) -> str:
        memory_view = self._memory_view()
        if memory_view is not None:
            return memory_view.get_user_facts()
        return _get_memory_service().get_user_facts(user_id)

    async def run(
        self,
        user_input: str,
        user_id: str,
        context: Dict,
        repo_context: str,
        skill_hint: str,
        context_policy: Optional[ContextPolicy] = None,
        runtime_state: Optional[AgentState] = None,
    ) -> Tuple[AgentState, str, Optional[str]]:
        """PLAN 阶段：Build CognitiveContext → ReferenceResolver → IntentEngine → WorkflowRouter → Planner。

        Returns:
            (AgentState, runtime_state_next, best_answer_or_None)
        """
        t0 = time.perf_counter()

        # 构建 AgentState
        from agent.llm import llm
        now = datetime.now()
        system_content = self._orch._context_builder.render_context(context, now)
        if repo_context:
            system_content += f"\n\n相关代码片段:\n{repo_context}"
        skill = (
            skill_registry.select(user_input)
            if context_policy is None or context_policy.semantic_skill_selection
            else None
        )
        if skill:
            skill_prompt = skill.get_system_prompt()
            if skill_prompt:
                system_content += f"\n\n{skill_prompt}"
            skill_hint = skill.planner_hint

        planning_memory = "\n\n".join(
            part for part in (context.get("session", ""), context.get("short_term", ""))
            if part
        )
        state: AgentState = {
            "messages": [
                SystemMessage(content=system_content),
                HumanMessage(content=user_input),
            ],
            "plan": [],
            "current_task_index": 0,
            "artifacts": {},
            "memory_context": planning_memory,
            "repo_context": repo_context,
            "skill_hint": skill_hint,
            "context_policy": (
                context_policy.mode.value if context_policy is not None else "full"
            ),
            "retries": 0,
            "workflow": None,
            "execution_plans": [],
            "execution_mode": "result_driven",
        }
        if runtime_state is not None:
            # Runtime-owned goal/inbox projections must survive the Planner's
            # context rebuild.  The Planner may replace its task projection,
            # but it is not the owner of goal rounds or action observations.
            for key in (
                "goal_state",
                "goal_evidence",
                "goal_missing",
                "inbox",
                "execution_mode",
            ):
                if key in runtime_state:
                    state[key] = runtime_state[key]

        # v2.3H2: external effects are a deterministic capability boundary.
        # Register them before any open-ended Planner call so a missing
        # reservation/message/deployment capability cannot drift into an LLM
        # success narrative or consume a replan budget.
        from agent.effect_truth import initialize_effect_contract

        effect_truth = initialize_effect_contract(
            state,
            user_input,
            capability_resolver=_capability_registry.resolve,
        )
        if effect_truth.unsupported_effects:
            requirement = effect_truth.unsupported_effects[0]
            capability = str(requirement.get("capability", "external_effect"))
            description = str(requirement.get("description", "外部操作"))
            message = (
                f"当前没有可用的 {description} 能力，因此本次未执行该外部操作。"
                "不会伪造已完成的结果。"
            )
            state["plan"] = [{
                "id": "task-1",
                "verb": Verb.EXECUTE.value,
                "target": capability,
                "target_type": "symbol",
                "goal": description,
                "description": user_input,
                "status": "failed",
                "error": f"UNSUPPORTED_CAPABILITY: 未注册能力 {capability}",
                "error_code": "UNSUPPORTED_CAPABILITY",
                "observations": [],
            }]
            return state, "FINISH", message

        # ── Stage 0: 构建 CognitiveContext ──
        # A resumed PLAN must consume the current Runtime projection.  The
        # Planner receives only ContextBuilder's narrow fields; it never sees
        # the raw checkpoint payload.
        context_state = runtime_state if runtime_state is not None else state
        planner_context = self._orch._context_builder.build(
            user_input=user_input,
            user_id=user_id,
            context=context,
            repo_context=repo_context,
            state=context_state,
            context_policy=context_policy,
        )
        print(f"  🧠 规划上下文: {planner_context.short_summary()}")

        # ── Stage 0.5: ReferenceResolver 消歧（v1.2B：产出 ResolutionResult）──
        resolved = self._orch._reference_resolver.resolve(user_input, planner_context)
        planner_context.resolved_query = resolved.to_resolved_query()
        state["resolved_target"] = resolved.target or ""
        state["resolved_symbol"] = resolved.symbol or ""
        if resolved.resolution_trace:
            print(f"  🔍 引用消歧: {resolved.resolution_trace}")

        # ── Stage 1: IntentEngine 意图理解（消费 CognitiveContext）──
        # ContextPolicy has already proved that SIMPLE_CHAT needs no Memory,
        # Repository, Skill or execution context.  Preserve that deterministic
        # result here so an LLM domain classifier cannot turn a greeting into
        # an execution plan before the one answer-generation call.
        intent_started = time.perf_counter()
        if context_policy is not None and context_policy.mode is ContextMode.SIMPLE_CHAT:
            intent = IntentResult(
                domain=DOMAIN_CHAT,
                action="chat",
                confidence=0.99,
                requires_execution=False,
                summary="deterministic simple chat",
                raw_input=user_input,
                requested_outcomes=analyze_requested_outcomes(user_input),
            )
        else:
            intent = await intent_engine.analyze_async(planner_context)
        self._orch._timings["intent_route"] = round(
            time.perf_counter() - intent_started,
            3,
        )
        print(f"  🧠 意图: {intent}")

        # H4: preserve explicit requested outcomes and write authorization
        # before any open-ended Planner output can alter the execution scope.
        from agent.cognition.effect_authorization import EffectAuthorization

        authorization = EffectAuthorization.from_request(user_input)
        state["requested_outcomes"] = [
            outcome.value for outcome in authorization.requested_outcomes
        ]
        state["authorized_write_scopes"] = list(authorization.write_scopes)

        intent_failure_code = str(getattr(intent, "failure_code", "") or "")
        if intent_failure_code:
            state["runtime_failure_code"] = intent_failure_code
            state["runtime_failure_class"] = "provider"
            state["runtime_failure_retryable"] = intent_failure_code in {
                "PROVIDER_TIMEOUT",
                "PROVIDER_NETWORK",
                "PROVIDER_UNAVAILABLE",
            }
            state["runtime_terminal_status"] = "FAILED_TERMINAL"
            return (
                state,
                "FAIL",
                str(
                    getattr(intent, "failure_message", "")
                    or "当前 LLM 服务暂时不可用，本次未生成或执行任务。"
                ),
            )

        # v2.3H3: preserve the deterministic research contract in Runtime
        # state. The final answer cannot establish freshness; only successful
        # source-tool observations can satisfy this requirement.
        state["freshness_required"] = bool(
            getattr(intent, "freshness_required", False)
        )
        state["source_grounding_required"] = bool(
            getattr(intent, "source_grounding_required", False)
        )
        state["fresh_evidence"] = False
        state["answer_required"] = True

        # v2.1B-1/2：记录本轮 intent + 会话快照 + 引用类型 + Runtime 续接
        self._orch.last_intent = intent
        try:
            from agent.conversation import resolve_reference_type
            session_context = getattr(self._orch, "session_context", None)
            session_retriever = (
                session_context.conversation_retriever
                if session_context is not None
                else None
            )
            conversation_kind = _apply_conversation_contract(
                intent,
                user_id,
                user_input,
                pending_target=getattr(planner_context, "runtime_pending_target", ""),
                reference_target=resolved.target,
                conversation_retriever=session_retriever,
            )
            state["conversation_intent"] = conversation_kind.value
            retriever = session_retriever
            if retriever is None:
                from agent.conversation import ConversationRetriever, ConversationTracker
                retriever = ConversationRetriever(ConversationTracker())
            state["conversation_snapshot"] = retriever.snapshot(user_id)
            state["conversation_reference_type"] = resolve_reference_type(intent).value
            state["conversation_runtime_continuation"] = _render_runtime_continuation(state)
            state["conversation_clarification_required"] = intent.action == "clarify"
        except (AttributeError, ImportError, KeyError, TypeError) as exc:
            _record_contract_diagnostic(
                state,
                "conversation_contract",
                exc,
                run_context=getattr(self._orch, "run_context", None),
            )

        # 更新跨轮对话状态（State = Cache：timeline 写入；last_* 为 Deprecated 双写）
        self._orch._context_builder.update_conversation_state(intent, resolution=resolved)

        unsupported_capability = _explicit_unsupported_capability(user_input)
        if unsupported_capability:
            message = (
                f"当前未注册或不支持 `{unsupported_capability}` 能力，"
                "因此不会伪造执行，也不会继续重规划。"
            )
            state["plan"] = [{
                "id": "task-1",
                "verb": Verb.EXECUTE.value,
                "target": unsupported_capability,
                "target_type": "symbol",
                "goal": f"调用 {unsupported_capability}",
                "description": user_input,
                "status": "failed",
                "error": f"UNSUPPORTED_CAPABILITY: 未注册能力 {unsupported_capability}",
                "error_code": "UNSUPPORTED_CAPABILITY",
                "observations": [],
            }]
            state["runtime_failure_code"] = "UNSUPPORTED_CAPABILITY"
            state["runtime_terminal_status"] = "BLOCKED"
            return state, "FINISH", message

        # 记录跨会话解析事实（Memory Facts，v1.2C；不依赖 ResolutionResult 内部）
        try:
            if resolved and resolved.target:
                self._record_resolution(
                    user_id, user_input, resolved.target, resolved.kind,
                )
        except Exception:
            pass

        # 不要求执行（chat / translation / math / creation / identity 等）→ 直接 LLM 回答
        if not intent.requires_execution:
            if intent.action == "clarify":
                answer = (
                    "你提到的引用目标与当前未完成任务可能不是同一个对象。"
                    "请明确要继续当前未完成任务，还是继续你刚才提到的引用目标。"
                )
                self._record_exchange(user_id, user_input, answer)
                return state, "FINISH", answer
            if intent.domain == DOMAIN_MEMORY:
                _facts = ""
                try:
                    _facts = self._get_user_facts(user_id)
                except Exception:
                    pass
                if _facts:
                    system_content += (
                        "\n\n## 用户事实（回答个人/偏好问题时必须优先使用）\n"
                        f"{_facts}\n"
                        "规则：只能基于上述事实回答；事实中不存在则如实说明未记录，禁止编造。"
                    )
            # v2.1B-2（ADR-0013）：Conversation Reference Resolver —— 只注入对应字段
            try:
                from agent.conversation import (
                    resolve_reference_type,
                    ReferenceType, render_reference,
                )
                session_context = getattr(self._orch, "session_context", None)
                conversation_retriever = (
                    session_context.conversation_retriever
                    if session_context is not None
                    else None
                )
                if conversation_retriever is None:
                    from agent.conversation import ConversationRetriever, ConversationTracker
                    conversation_retriever = ConversationRetriever(ConversationTracker())
                _ref_type = resolve_reference_type(intent)
                if _ref_type is ReferenceType.LAST_RUNTIME:
                    _cont = state.get("conversation_runtime_continuation", "") or ""
                    if _cont:
                        system_content += f"\n\n{_cont}"
                elif _ref_type is not ReferenceType.UNKNOWN:
                    _inj = render_reference(
                        conversation_retriever.snapshot(user_id), _ref_type)
                    if _inj:
                        system_content += f"\n\n{_inj}"
            except (AttributeError, ImportError, KeyError, TypeError) as exc:
                _record_contract_diagnostic(
                    state,
                    "conversation_reference_injection",
                    exc,
                    run_context=getattr(self._orch, "run_context", None),
                )
            answer_started = time.perf_counter()
            try:
                response = await llm.ainvoke([
                    SystemMessage(content=system_content),
                    HumanMessage(content=user_input),
                ])
                answer = response.content if hasattr(response, 'content') else str(response)
            except Exception as exc:
                # A provider failure must remain a runtime fact.  Returning a
                # polite fallback string alone makes the answer gate treat the
                # direct-chat path as successful and can emit run_completed.
                # Keep the user-facing wording safe, while preserving a
                # stable terminal classification for the Runtime/Service.
                answer = "抱歉，我暂时无法回答。"
                state["runtime_terminal_status"] = "FAILED_TERMINAL"
                state["runtime_failure_code"] = (
                    classify_execution_error(exc) or "PROVIDER_UNAVAILABLE"
                )
                state["runtime_failure_class"] = "provider"
                state["runtime_failure_retryable"] = True
            self._orch._timings["answer_llm"] = round(
                time.perf_counter() - answer_started,
                3,
            )
            self._record_exchange(user_id, user_input, answer)
            return state, "FINISH", answer

        # Fresh research is a deterministic capability boundary. Build one
        # source-backed task directly instead of asking the general Planner to
        # invent an ``llm_executor`` search substitute.
        if getattr(intent, "freshness_required", False) or intent.action == "fresh_research":
            if not any(
                _tool_registry.get(name) is not None
                for name in (
                    "web_search",
                    "web_news_search",
                    "web_deep_search",
                    "web_fetch",
                )
            ):
                state["runtime_terminal_status"] = "BLOCKED"
                state["runtime_failure_code"] = "RESEARCH_TOOL_UNAVAILABLE"
                state["runtime_failure_class"] = "execution"
                state["runtime_failure_retryable"] = False
                return (
                    state,
                    "FINISH",
                    "当前没有可用的外部检索工具，因此不能可靠回答这项时效性问题；"
                    "本次未生成无来源的当前信息。",
                )
            task_data = {
                "id": "task-1",
                "verb": "search",
                "target": user_input,
                "target_type": "text",
                "goal": "检索外部来源并返回带来源的研究结果",
                "description": "必须使用 web_search；不得依赖模型记忆替代外部检索。",
                "success_condition": "至少返回一个可核验来源，或明确报告检索不可用",
                "dependencies": [],
                "children": [],
                "inputs": {
                    "query": research_query(user_input),
                    "timeliness": research_timeliness(user_input),
                },
                "policy": {
                    "executor": "tool",
                    "tool_policy": {"allow": ["web_search"]},
                },
            }
            fresh_plan = _ensure_explicit_output_write_task(
                [task_data], user_input, intent,
            )
            canonical_plan = []
            fresh_execution_plans = []
            for task in fresh_plan:
                task_obj = Task.from_dict(task)
                canonical_plan.append(task_obj.to_dict())
                fresh_execution_plans.append(
                    self._orch._selector.compile(
                        task_obj,
                        context=CompilerContext(registry=_tool_registry),
                    )
                )
            state["plan"] = canonical_plan
            state["execution_plans"] = fresh_execution_plans
            state["current_task_index"] = 0
            self._print_plan(list(state.get("plan") or []))
            return state, "EXECUTE", None

        file_operation_task = _build_file_operation_task(user_input)
        if file_operation_task and getattr(intent, "requires_execution", False):
            state["plan"] = [file_operation_task.to_dict()]
            state["execution_plans"] = [
                self._orch._selector.compile(
                    file_operation_task,
                    context=CompilerContext(registry=_tool_registry),
                )
            ]
            state["current_task_index"] = 0
            self._print_plan(list(state.get("plan") or []))
            return state, "EXECUTE", None

        transform_execution = _build_text_transform_execution(user_input)
        if transform_execution and getattr(intent, "requires_execution", False):
            task_obj, execution_plan = transform_execution
            state["plan"] = [task_obj.to_dict()]
            state["execution_plans"] = [execution_plan]
            state["current_task_index"] = 0
            self._print_plan(list(state.get("plan") or []))
            return state, "EXECUTE", None

        literal_append = _extract_literal_file_append(user_input)
        if literal_append and getattr(intent, "requires_execution", False):
            target, content = literal_append
            task_obj = Task.from_dict({
                "id": "task-1",
                "verb": Verb.WRITE.value,
                "target": target,
                "target_type": "file",
                "goal": f"向 {target} 追加一行",
                "description": "内容由用户明确提供，不需要再次生成。",
                "success_condition": f"{target} 保留已有内容且只追加本次内容一次",
                "inputs": {"content": f"{content}\n", "mode": "append"},
            })
            compiled = self._orch._selector.compile(
                task_obj,
                context=CompilerContext(registry=_tool_registry),
            )
            state["plan"] = [task_obj.to_dict()]
            state["execution_plans"] = [compiled]
            state["current_task_index"] = 0
            self._print_plan(list(state.get("plan") or []))
            return state, "EXECUTE", None

        merge_execution = _build_text_merge_execution(user_input)
        if merge_execution and getattr(intent, "requires_execution", False):
            task_obj, execution_plan = merge_execution
            state["plan"] = [task_obj.to_dict()]
            state["execution_plans"] = [execution_plan]
            state["current_task_index"] = 0
            self._print_plan(list(state.get("plan") or []))
            return state, "EXECUTE", None

        source_code_task = _build_source_code_execution_task(user_input)
        if source_code_task and getattr(intent, "requires_execution", False):
            state["plan"] = [source_code_task.to_dict()]
            state["execution_plans"] = [
                self._orch._selector.compile(
                    source_code_task,
                    context=CompilerContext(registry=_tool_registry),
                )
            ]
            state["current_task_index"] = 0
            self._print_plan(list(state.get("plan") or []))
            return state, "EXECUTE", None

        code_run_tasks = _build_code_run_tasks(user_input)
        if code_run_tasks and getattr(intent, "requires_execution", False):
            state["plan"] = [task.to_dict() for task in code_run_tasks]
            state["execution_plans"] = [
                self._orch._selector.compile(
                    task,
                    context=CompilerContext(registry=_tool_registry),
                )
                for task in code_run_tasks
            ]
            state["current_task_index"] = 0
            self._print_plan(list(state.get("plan") or []))
            return state, "EXECUTE", None

        command_execution_task = _build_explicit_command_execution_task(user_input)
        if command_execution_task and getattr(intent, "requires_execution", False):
            state["plan"] = [command_execution_task.to_dict()]
            state["execution_plans"] = [
                self._orch._selector.compile(
                    command_execution_task,
                    context=CompilerContext(registry=_tool_registry),
                )
            ]
            state["current_task_index"] = 0
            self._print_plan(list(state.get("plan") or []))
            return state, "EXECUTE", None

        modify_tasks = _build_modify_execution_tasks(user_input)
        if modify_tasks and getattr(intent, "requires_execution", False):
            state["plan"] = [task.to_dict() for task in modify_tasks]
            state["execution_plans"] = [
                self._orch._selector.compile(
                    task,
                    context=CompilerContext(registry=_tool_registry),
                )
                for task in modify_tasks
            ]
            state["current_task_index"] = 0
            self._print_plan(list(state.get("plan") or []))
            return state, "EXECUTE", None

        # A complete literal write is a deterministic world-state change.  Do
        # not spend Planner/LLM calls rediscovering the content already given
        # by the user; it still enters the canonical Compiler → Executor →
        # Verifier chain below.
        literal_write = _extract_literal_file_write(user_input)
        if literal_write and getattr(intent, "requires_execution", False):
            target, content = literal_write
            task_obj = Task.from_dict({
                "id": "task-1",
                "verb": Verb.WRITE.value,
                "target": target,
                "target_type": "file",
                "goal": f"将用户提供的内容写入 {target}",
                "description": "内容已由用户明确提供，无需再次生成。",
                "success_condition": f"文件 {target} 存在且非空",
                "dependencies": [],
                "children": [],
                "inputs": {"content": content},
            })
            compiled = self._orch._selector.compile(
                task_obj,
                context=CompilerContext(registry=_tool_registry),
            )
            state["plan"] = [task_obj.to_dict()]
            state["execution_plans"] = [compiled]
            state["current_task_index"] = 0
            self._print_plan(list(state.get("plan") or []))
            return state, "EXECUTE", None

        # ── Stage 2: optional Workflow route ──
        # Generic requests stay on the single Planner/Action path.  The
        # specialized Workflow registry is loaded only for the one currently
        # supported workflow family (question code generation).
        if _workflow_route_requested(intent):
            from agent.bootstrap import load_all_workflows

            load_all_workflows()
            wf_obj, wf_reason = _get_workflow_router().route(intent)
        else:
            wf_obj, wf_reason = None, "无专用 Workflow，使用通用 Planner"

        if wf_obj:
            wf_name = wf_obj.id if hasattr(wf_obj, 'id') else str(wf_obj)
            from agent.executor.executors.workflow import WorkflowExecutor
            from agent.workflow import Artifact, ExecutionContext, Workflow

            if isinstance(wf_obj, Workflow):
                workflow_projection = WorkflowDefinitionProjection.from_workflow(wf_obj)
                available_capabilities = tuple(
                    capability
                    for capability in workflow_projection.required_capabilities
                    if _tool_registry.get(registry_tool_name(capability)) is not None
                )
                workflow_context = WorkflowContextProjection(
                    artifacts={},
                    capabilities=available_capabilities,
                    facts={},
                    active_workflow=None,
                )
                try:
                    workflow_selection = await self._orch._workflow_selector.select_with_evidence(
                        user_input,
                        workflow_context,
                        (workflow_projection,),
                    )
                except WorkflowSelectionError as exc:
                    print(
                        "  ⚠️ Workflow 选择失败，使用通用 Planner: "
                        f"{stable_error_message(exc, fallback='Workflow selection failed')[:160]}"
                    )
                    return await self._fallback_to_generic_planner(
                        user_input=user_input,
                        context=context,
                        repo_context=repo_context,
                        skill_hint=skill_hint,
                        planner_context=planner_context,
                        intent=intent,
                        state=state,
                        planning_memory=planning_memory,
                        started_at=t0,
                    )

                workflow_decision = workflow_selection.decision
                if workflow_decision.kind is WorkflowDecisionKind.ASK:
                    answer = workflow_decision.reason or "请补充运行该 Workflow 所需的信息。"
                    state["conversation_clarification_required"] = True
                    state["runtime_terminal_status"] = "BLOCKED"
                    state["runtime_failure_code"] = "WORKFLOW_BINDINGS_REQUIRED"
                    self._record_exchange(user_id, user_input, answer)
                    return state, "FINISH", answer
                if workflow_decision.kind is WorkflowDecisionKind.DECLINE:
                    return await self._fallback_to_generic_planner(
                        user_input=user_input,
                        context=context,
                        repo_context=repo_context,
                        skill_hint=skill_hint,
                        planner_context=planner_context,
                        intent=intent,
                        state=state,
                        planning_memory=planning_memory,
                        started_at=t0,
                    )
                if workflow_decision.kind is not WorkflowDecisionKind.INSTANTIATE:
                    raise ValueError("new Workflow selection cannot reuse an active Workflow")

                selected_workflow = workflow_registry.get(workflow_decision.workflow_id)
                if selected_workflow is None:
                    raise ValueError(
                        f"selected Workflow is not registered: {workflow_decision.workflow_id}"
                    )
                wf_obj = selected_workflow
                wf_name = wf_obj.id
                print(f"\n{'='*50}\n🚀 路由到 Workflow: {wf_name}\n{'='*50}")
                self._orch._conversation_state.last_workflow = wf_name

                # Canonical Workflow → WorkflowExecutor
                state["workflow"] = wf_name
                ctx = ExecutionContext(
                    workflow_id=wf_obj.id,
                    user_input=user_input,
                    task=Task(
                        id="root",
                        verb=Verb.READ,
                        target="",
                        goal=user_input,
                    ),
                )
                for binding_name, binding_value in workflow_decision.bindings.items():
                    summary = str(binding_value)
                    ctx.set_artifact(Artifact(
                        id=f"workflow-binding-{binding_name}",
                        type=binding_name,
                        content=binding_value,
                        summary=summary,
                    ))

                try:
                    result = await WorkflowExecutor().execute(wf_obj, ctx)
                except Exception as exc:
                    print(f"  ⚠️ 专用 Workflow 异常，降级到通用 Planner: {str(exc)[:160]}")
                    result = None
                self._orch._timings["plan_llm"] = round(time.perf_counter() - t0, 3)

                if result and result.success and result.outputs.get("_summary"):
                    best_answer = result.outputs["_summary"]
                    self._record_exchange(user_id, user_input, best_answer)
                    return state, "FINISH", best_answer
                else:
                    reason = result.error[:160] if result else "Workflow 异常"
                    print(f"  ⚠️ 专用 Workflow 未完成（{reason}），降级到通用 Planner")
                    return await self._fallback_to_generic_planner(
                        user_input=user_input,
                        context=context,
                        repo_context=repo_context,
                        skill_hint=skill_hint,
                        planner_context=planner_context,
                        intent=intent,
                        state=state,
                        planning_memory=planning_memory,
                        started_at=t0,
                    )
            else:
                raise TypeError(
                    f"WorkflowRegistry 只能返回 canonical Workflow，收到 {type(wf_obj).__name__}"
                )
        else:
            # 未命中 Workflow → Planner 兜底（带契约校验 + Retry + Grounding，PR-7）
            print(f"\n📋 无匹配 Workflow（{wf_reason}），使用 Planner 生成计划。")
            # ── Grounding：缩小 Planner 搜索空间（基于 intent 检索键）──
            grounding_ctx = None
            try:
                from agent.grounding import Grounder, GroundingInput
                ws_ctx = planner_context.workspace
                grounding_ctx = Grounder().ground(GroundingInput(
                    query=user_input,
                    intent=intent,
                    current_file=getattr(ws_ctx, "current_file", "") or "",
                    opened_files=list(getattr(ws_ctx, "opened_files", []) or []),
                    workspace=(
                        getattr(getattr(self._orch, "run_context", None), "workspace", None)
                    ),
                )).context
            except Exception as e:
                print(f"  ⚠️ Grounding 失败（忽略）: {e}")

            plan = None
            execution_plans: list[ExecutionPlan] = []
            last_error = ""
            planner_input = user_input
            for attempt in range(3):
                try:
                    plan_output = await plan_with_metadata(
                        planner_input, planning_memory,
                        repo_context, skill_hint, None,
                        grounding=grounding_ctx,
                        planning_context=planner_context,
                    )
                    planner_failure = _apply_planner_failure(state, plan_output)
                    if planner_failure is not None:
                        return planner_failure
                    plan = plan_output.tasks
                    plan = _ensure_explicit_output_write_task(
                        plan, user_input, intent,
                    )
                    for i, t in enumerate(plan):
                        t.setdefault("id", f"task-{i+1}")
                        t.setdefault("status", "pending")
                        t.setdefault("observations", [])
                        t.setdefault("error", "")
                        t.setdefault("children", [])
                        t.setdefault("description", "")
                        t.setdefault("dependencies", [])
                    # 追加写入：显式"追加"请求 → 写任务 inputs.mode=append（确定性）
                    if any(tok in user_input.lower() for tok in ("追加", "附加", "append")):
                        for _t in plan:
                            if _t.get("verb") == "write":
                                _t.setdefault("inputs", {})["mode"] = "append"
                    self._print_plan(plan)

                    # 契约校验 + 编译（编译期错误 → retry）
                    execution_plans = []
                    for t in plan:
                        task_obj = Task.from_dict(t)
                        ep = self._orch._selector.compile(
                            task_obj,
                            context=CompilerContext(
                                registry=_tool_registry,
                            ),
                        )
                        execution_plans.append(ep)
                    break
                except Exception as e:
                    last_error = stable_error_message(e, fallback="plan validation failed")
                    print(f"  ⚠️ Planner 输出不合法（{attempt+1}/3）: {last_error}")
                    planner_input = (
                        f"你上一次输出的计划不合法，需要修正后重新输出。\n"
                        f"错误：{last_error}\n"
                        f"规则：verb 必须是 read/write/modify/execute/search/list/explain/delete/move/copy/resolve；\n"
                        f"target_type=file 时 target 必须是具体文件路径（禁止中文描述）；\n"
                        f"示例：{{\"verb\": \"read\", \"target\": \"output/solution.py\", \"target_type\": \"file\"}}\n\n"
                        f"原始需求：{user_input}"
                    )

            if plan is None:
                print("❌ Planner 连续 3 次输出不合法，任务失败（PlanningFailure，不进入执行链）")
                return state, "FAIL", None

            # v2.0-A Abstention：Planner 信息不足 → 不猜测，向用户澄清（Uncertainty 横切）
            if not plan:
                print("⚠️ Planner 信息不足（Abstain，不猜测）—— 需要向用户澄清。")
                return state, "FINISH", (
                    "信息不足，需要澄清：请提供具体的目标（如文件路径或模块名），"
                    "以便我制定执行计划。"
                )

            state["execution_plans"] = execution_plans
            state["plan"] = plan
            state["current_task_index"] = 0
            self._orch._timings["plan_llm"] = round(time.perf_counter() - t0, 3)
            return state, "EXECUTE", None

    async def _fallback_to_generic_planner(
        self,
        user_input: str,
        context: Dict,
        repo_context: str,
        skill_hint: str,
        planner_context,
        intent,
        state: AgentState,
        planning_memory: str,
        started_at: float,
    ) -> Tuple[AgentState, str, Optional[str]]:
        """Recover from a specialized workflow failure without false success."""
        grounding_ctx = None
        try:
            from agent.grounding import Grounder, GroundingInput

            ws_ctx = planner_context.workspace
            grounding_ctx = Grounder().ground(GroundingInput(
                query=user_input,
                intent=intent,
                current_file=getattr(ws_ctx, "current_file", "") or "",
                opened_files=list(getattr(ws_ctx, "opened_files", []) or []),
                workspace=(
                    getattr(getattr(self._orch, "run_context", None), "workspace", None)
                ),
            )).context
        except Exception as exc:
            print(
                "  ⚠️ 通用 Planner Grounding 失败（忽略）: "
                f"{stable_error_message(exc, fallback='grounding failed')[:120]}"
            )

        fallback_input = (
            "专用工作流未完成。请改用通用任务计划，直接围绕用户原始需求规划，"
            "不要使用 code_generation 工作流，也不要声称未验证的文件已写入。\n"
            f"原始需求：{user_input}"
        )
        plan = None
        execution_plans: list[ExecutionPlan] = []
        for attempt in range(2):
            try:
                plan_output = await plan_with_metadata(
                    fallback_input,
                    planning_memory,
                    repo_context,
                    skill_hint,
                    None,
                    grounding=grounding_ctx,
                    planning_context=planner_context,
                )
                planner_failure = _apply_planner_failure(state, plan_output)
                if planner_failure is not None:
                    return planner_failure
                plan = plan_output.tasks
                plan = _ensure_explicit_output_write_task(
                    plan, user_input, intent,
                )
                for i, task_data in enumerate(plan):
                    task_data.setdefault("id", f"task-{i + 1}")
                    task_data.setdefault("status", "pending")
                    task_data.setdefault("observations", [])
                    task_data.setdefault("error", "")
                    task_data.setdefault("children", [])
                    task_data.setdefault("description", "")
                    task_data.setdefault("dependencies", [])
                execution_plans = [
                    self._orch._selector.compile(
                        Task.from_dict(task_data),
                        context=CompilerContext(registry=_tool_registry),
                    )
                    for task_data in plan
                ]
                break
            except Exception as exc:
                print(
                    f"  ⚠️ 通用 Planner 输出不合法（{attempt + 1}/2）: "
                    f"{stable_error_message(exc, fallback='planner output invalid')[:160]}"
                )
                plan = None

        if not plan:
            return state, "FINISH", "任务未完成：专用工作流失败，通用 Planner 也未能生成可执行计划。"

        self._print_plan(plan)
        state["execution_plans"] = execution_plans
        state["plan"] = plan
        state["current_task_index"] = 0
        self._orch._timings["plan_llm"] = round(time.perf_counter() - started_at, 3)
        return state, "EXECUTE", None

    # ── REPLAN ──

    async def replan(
        self,
        state: AgentState,
        user_input: str,
        user_id: str,
    ) -> Tuple[AgentState, str]:
        """REPLAN 阶段：重规划失败任务。"""
        current_plan = state.get("plan") or []
        deterministic_failure = next(
            (
                str(task.get("error_code", ""))
                or classify_execution_error(task.get("error", ""))
                for task in current_plan
                if task.get("status") == "failed"
                and (
                    str(task.get("error_code", ""))
                    or classify_execution_error(task.get("error", ""))
                )
            ),
            "",
        )
        if deterministic_failure and is_non_retriable(deterministic_failure):
            state["runtime_failure_code"] = deterministic_failure
            state["runtime_terminal_status"] = (
                "BLOCKED"
                if deterministic_failure in {
                    "RESEARCH_TOOL_UNAVAILABLE",
                    "UNSUPPORTED_CAPABILITY",
                }
                else "FAILED_TERMINAL"
            )
            print(
                f"❌ 确定性错误 {deterministic_failure}，停止重规划"
            )
            return state, "FAIL"

        budget = getattr(self._orch, "run_budget", None)
        if budget is not None:
            if not budget.consume_recovery():
                state["runtime_failure_code"] = "RUNTIME_RECOVERY_BUDGET_EXHAUSTED"
                state["runtime_terminal_status"] = "FAILED_TERMINAL"
                print("❌ 达到 Runtime recovery budget")
                return state, "FAIL"
        elif self._orch.replan_count >= 2:
            # Test-only legacy container without a bound RunBudget. Production
            # orchestrators always bind the shared budget before execution.
            print("❌ 达到最大重试次数")
            return state, "FAIL"

        self._orch.replan_count += 1
        state["retries"] = self._orch.replan_count
        limit = budget.max_recoveries if budget is not None else 2
        print(f"\n⚠️ 任务失败，重规划 ({self._orch.replan_count}/{limit})...")

        t_replan = time.perf_counter()
        failed_info = [
            f"- {t['id']}: {t.get('goal', '?')} (错误: {t.get('error', '?')})"
            for t in current_plan
            if t.get("status") == "failed"
        ]
        completed_info = [
            (
                f"- {t.get('id', '?')}: verb={t.get('verb', '?')} "
                f"target={t.get('target', '')} goal={t.get('goal', '?')}"
            )
            for t in current_plan
            if t.get("status") == "succeeded"
        ]
        replan_input = (
            f"原始需求: {user_input}\n\n以下任务执行失败，需要重新规划：\n"
            + "\n".join(failed_info)
            + (
                "\n\n以下任务已经成功并通过执行验证，不得重复其写入、修改、"
                "删除、移动、复制或命令副作用：\n"
                + "\n".join(completed_info)
                if completed_info
                else ""
            )
        )
        inbox = AgentInbox.from_dict(state.get("inbox"))
        if inbox.next_step:
            replan_input += (
                "\n\n最近动作观察（这是当前事实，不要假设原计划仍然正确）：\n"
                + json.dumps(
                    inbox.next_step[-8:],
                    ensure_ascii=False,
                    default=str,
                )
            )
        plan_output = await plan_with_metadata(replan_input, "", "", "", None)
        planner_failure = _apply_planner_failure(state, plan_output)
        if planner_failure is not None:
            return state, "FAIL"
        new_plan = plan_output.tasks
        new_plan = _ensure_explicit_output_write_task(new_plan, user_input)
        for t in new_plan:
            t.setdefault("status", "pending")
            t.setdefault("observations", [])
            t.setdefault("error", "")
        if any(tok in user_input.lower() for tok in ("追加", "附加", "append")):
            for _t in new_plan:
                if _t.get("verb") == "write":
                    _t.setdefault("inputs", {})["mode"] = "append"

        new_plan, skipped_verified_effects = _reconcile_replan_tasks(
            current_plan,
            new_plan,
            replan_attempt=self._orch.replan_count,
        )
        if skipped_verified_effects:
            state["replan_skipped_verified_effects"] = int(
                state.get("replan_skipped_verified_effects", 0) or 0
            ) + len(skipped_verified_effects)

        preserved_facts = {}
        for t in current_plan:
            if t.get("status") == "succeeded":
                preserved_facts.update(t.get("facts", {}))

        old_unfinished = [
            t for t in current_plan
            if t.get("status") in ("pending", "running")
        ]
        if not old_unfinished and not new_plan:
            state["runtime_failure_code"] = "REPLAN_NO_EXECUTABLE_TASKS"
            state["runtime_terminal_status"] = "FAILED_TERMINAL"
            self._orch._timings["replan_llm"] = round(
                time.perf_counter() - t_replan,
                3,
            )
            print("❌ 重规划未产生新的安全可执行任务")
            return state, "FAIL"
        for t in new_plan:
            t.setdefault("facts", {})
            t["facts"].update(preserved_facts)

        # 重新生成 ExecutionPlan
        execution_plans: list[ExecutionPlan] = []
        for t in new_plan:
            task_obj = Task.from_dict(t)
            ep = self._orch._selector.compile(
                task_obj,
                context=CompilerContext(
                    registry=_tool_registry,
                ),
            )
            execution_plans.append(ep)

        previous_execution_plans = list(state.get("execution_plans") or [])
        preserved_execution_plans = [
            previous_execution_plans[index]
            if index < len(previous_execution_plans)
            else None
            for index, task in enumerate(current_plan)
            if task.get("status") in ("pending", "running")
        ]
        state["execution_plans"] = preserved_execution_plans + execution_plans
        state["plan"] = old_unfinished + new_plan
        state["current_task_index"] = 0
        print(
            f"  🔄 重新规划，共 {len(old_unfinished + new_plan)} 个任务"
            f"（保留 {len(preserved_facts)} 个 Facts，"
            f"跳过 {len(skipped_verified_effects)} 个已验证副作用）"
        )
        self._orch._timings["replan_llm"] = round(time.perf_counter() - t_replan, 3)
        return state, "EXECUTE"

    # ── 辅助 ──

    def _print_plan(self, tasks: list) -> None:
        print("\n" + "=" * 60)
        print("📋 执行计划（Task 分解）")
        print("=" * 60)
        for task in tasks:
            tid = task.get("id", "?")
            verb = task.get("verb", "")
            target = task.get("target", "")
            goal = task.get("goal", "?")
            if verb and target:
                label = f"{verb} {target}"
            else:
                label = goal
            dep = (
                f" (依赖: {task.get('dependencies', [])})"
                if task.get("dependencies")
                else ""
            )
            children = task.get("children", [])
            child_cnt = f" [{len(children)} 子任务]" if children else ""
            print(f"  {tid}: {label}{dep}{child_cnt}")
        print("=" * 60 + "\n")
