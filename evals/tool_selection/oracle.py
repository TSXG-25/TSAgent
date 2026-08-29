"""Deterministic Oracle for the v2.4B Tool Selection / ReAct Dataset.

The benchmark evaluates one bounded ``NextAction`` choice from a projected
task state and the latest ``ActionResult`` observation.  It never executes a
tool, invokes a provider, reads a workspace, or treats ``reason`` prose as
machine evidence.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import hashlib
import json
from pathlib import Path
from typing import Any

from agent.next_action import ActionKind, NextAction
from agent.task import Verb


DATASET_PATH = Path(__file__).with_name("dataset.json")
DATASET_VERSION = "v2.4B-tool-selection-v1"
VALID_FAMILIES = {
    "INITIAL_SELECTION",
    "RESULT_TRANSITION",
    "FAILURE_RECOVERY",
    "DEPENDENCY_CONTROL",
    "VERIFICATION_BOUNDARY",
    "OBSERVATION_BRANCH",
}
VALID_DECISIONS = {
    "CONTINUE",
    "RETRY",
    "VERIFY",
    "SWITCH",
    "FINISH",
    "ASK_CLARIFICATION",
    "BOUNDARY_STOP",
}
_ACTION_FIELDS = frozenset({"kind", "tool", "args", "reason", "task_id"})
_EFFECT_TOOLS = frozenset({
    "filesystem.write",
    "filesystem.copy",
    "filesystem.move",
    "filesystem.delete",
    "run_python",
    "run_python_file",
    "shell",
})
_TERMINAL_TASK_STATUSES = frozenset({"succeeded", "skipped"})
_VALID_VERBS = frozenset(verb.value for verb in Verb)
_VALID_TARGET_TYPES = frozenset({"file", "symbol", "text", "none"})


def load_dataset(path: Path = DATASET_PATH) -> dict[str, Any]:
    """Load and validate the frozen v2.4B dataset."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    errors = validate_dataset(payload)
    if errors:
        raise ValueError("invalid Tool Selection dataset: " + "; ".join(errors))
    return payload


def validate_dataset(payload: Mapping[str, Any]) -> tuple[str, ...]:
    """Validate the public action-selection input/output contract."""

    errors: list[str] = []
    if payload.get("version") != DATASET_VERSION:
        errors.append(f"version must be {DATASET_VERSION!r}")
    if payload.get("scope") != "next_action_selection":
        errors.append("scope must be next_action_selection")
    if payload.get("contract") != "Task + State + Observation -> NextAction":
        errors.append("contract declaration is invalid")

    cases = payload.get("cases")
    if not isinstance(cases, list) or len(cases) != 24:
        errors.append("Tool Selection dataset requires exactly 24 cases")
        return tuple(errors)

    ids: set[str] = set()
    families: set[str] = set()
    for index, case in enumerate(cases):
        prefix = f"cases[{index}]"
        if not isinstance(case, Mapping):
            errors.append(f"{prefix} must be an object")
            continue
        case_id = str(case.get("id", ""))
        if not case_id or case_id in ids:
            errors.append(f"{prefix} has invalid or duplicate id: {case_id!r}")
        ids.add(case_id)
        family = str(case.get("family", ""))
        families.add(family)
        if family not in VALID_FAMILIES:
            errors.append(f"{case_id} has invalid family: {family!r}")
        if not str(case.get("input", "")).strip():
            errors.append(f"{case_id} input is required")

        state = case.get("state")
        if not isinstance(state, Mapping):
            errors.append(f"{case_id} state must be an object")
        else:
            _validate_state(case_id, state, errors)

        observation = case.get("observation")
        if not isinstance(observation, Mapping):
            errors.append(f"{case_id} observation must be an object")
        elif "last_action" not in observation or "last_result" not in observation:
            errors.append(f"{case_id} observation must expose last_action and last_result")

        expected = case.get("expected")
        if not isinstance(expected, Mapping):
            errors.append(f"{case_id} expected must be an object")
        else:
            _validate_expected(case_id, state if isinstance(state, Mapping) else {}, expected, errors)

    if families != VALID_FAMILIES:
        errors.append("dataset must cover all six Tool Selection families")
    return tuple(errors)


def _validate_state(case_id: str, state: Mapping[str, Any], errors: list[str]) -> None:
    for field in ("goal", "current_task_id", "tasks", "required_outcomes", "completed_outcomes", "answer_ready", "available_tools", "completion_evidence", "history"):
        if field not in state:
            errors.append(f"{case_id} state.{field} is required")
    if not isinstance(state.get("tasks"), list):
        errors.append(f"{case_id} state.tasks must be a list")
    if not isinstance(state.get("available_tools"), list) or any(
        not isinstance(tool, str) or not tool.strip()
        for tool in state.get("available_tools", [])
    ):
        errors.append(f"{case_id} state.available_tools must contain tool names")
    if not isinstance(state.get("answer_ready"), bool):
        errors.append(f"{case_id} state.answer_ready must be boolean")
    task_ids: set[str] = set()
    task_positions: dict[str, int] = {}
    for task_index, task in enumerate(state.get("tasks", []) if isinstance(state.get("tasks"), list) else []):
        prefix = f"{case_id} state.tasks[{task_index}]"
        if not isinstance(task, Mapping):
            errors.append(f"{prefix} must be an object")
            continue
        task_id = str(task.get("id", ""))
        if not task_id or task_id in task_ids:
            errors.append(f"{prefix} has invalid or duplicate id")
        task_ids.add(task_id)
        task_positions[task_id] = task_index
        if str(task.get("verb", "")) not in _VALID_VERBS:
            errors.append(f"{prefix}.verb is invalid")
        if str(task.get("status", "")) not in {"pending", "running", "succeeded", "skipped", "failed"}:
            errors.append(f"{prefix}.status is invalid")
        if str(task.get("target_type", "")) not in _VALID_TARGET_TYPES:
            errors.append(f"{prefix}.target_type is invalid")
        if not isinstance(task.get("dependencies", []), list):
            errors.append(f"{prefix}.dependencies must be a list")
        if not isinstance(task.get("target", ""), str):
            errors.append(f"{prefix}.target must be a string")
    current_task_id = str(state.get("current_task_id", ""))
    if current_task_id and current_task_id not in task_ids:
        errors.append(f"{case_id} current_task_id references an unknown task")
    graph: dict[str, tuple[str, ...]] = {}
    for task_index, task in enumerate(state.get("tasks", []) if isinstance(state.get("tasks"), list) else []):
        if not isinstance(task, Mapping):
            continue
        task_id = str(task.get("id", ""))
        dependencies = tuple(str(value) for value in task.get("dependencies", []) or [])
        graph[task_id] = dependencies
        for dependency in task.get("dependencies", []) or []:
            if str(dependency) not in task_ids:
                errors.append(f"{case_id} task dependency references unknown task: {dependency}")
            elif task_id in task_positions and task_positions[str(dependency)] >= task_positions[task_id]:
                errors.append(f"{case_id} task dependency must point to an earlier task: {task_id} -> {dependency}")

    colors = {task_id: 0 for task_id in task_ids}

    def visit(task_id: str) -> bool:
        colors[task_id] = 1
        for dependency in graph.get(task_id, ()):
            if dependency not in colors:
                continue
            if colors[dependency] == 1 or (colors[dependency] == 0 and visit(dependency)):
                return True
        colors[task_id] = 2
        return False

    if any(colors[task_id] == 0 and visit(task_id) for task_id in colors):
        errors.append(f"{case_id} state task dependencies contain a cycle")


def _validate_expected(
    case_id: str,
    state: Mapping[str, Any],
    expected: Mapping[str, Any],
    errors: list[str],
) -> None:
    kind = str(expected.get("kind", ""))
    decision = str(expected.get("decision", ""))
    if kind not in {item.value for item in ActionKind}:
        errors.append(f"{case_id} expected.kind is invalid")
    if decision not in VALID_DECISIONS:
        errors.append(f"{case_id} expected.decision is invalid")
    tools = expected.get("tools")
    if not isinstance(tools, list) or any(not isinstance(tool, str) or not tool.strip() for tool in tools):
        errors.append(f"{case_id} expected.tools must be a list of names")
    args = expected.get("args")
    if not isinstance(args, Mapping):
        errors.append(f"{case_id} expected.args must be an object")
    if kind == ActionKind.TOOL.value:
        if not tools:
            errors.append(f"{case_id} tool action requires expected.tools")
        if str(expected.get("task_id", "")) == "":
            errors.append(f"{case_id} tool action requires expected.task_id")
        available = {str(tool) for tool in state.get("available_tools", []) or []}
        if tools and not set(tools).issubset(available):
            errors.append(f"{case_id} expected tool is not available in state")
    else:
        if tools:
            errors.append(f"{case_id} non-tool action cannot declare expected tools")
        if expected.get("task_id", "") != "":
            errors.append(f"{case_id} non-tool action must not target a task")
        if args not in ({}, None):
            errors.append(f"{case_id} non-tool action must have empty args")


def dataset_hash(payload: Mapping[str, Any] | None = None) -> str:
    """Return the stable hash of the complete dataset envelope."""

    value = payload if payload is not None else load_dataset()
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def golden_action(case: Mapping[str, Any]) -> dict[str, Any]:
    """Build the deterministic self-check action declared by a case."""

    expected = case["expected"]
    return {
        "kind": str(expected["kind"]),
        "tool": str(expected["tools"][0]) if expected.get("tools") else "",
        "args": dict(expected.get("args", {}) or {}),
        "reason": "dataset self-check",
        "task_id": str(expected.get("task_id", "")),
    }


def _normalize_action(raw: Mapping[str, Any] | NextAction) -> tuple[dict[str, Any] | None, list[str]]:
    if isinstance(raw, NextAction):
        value = raw.to_dict()
    elif isinstance(raw, Mapping):
        value = dict(raw)
    else:
        return None, ["action must be a NextAction or object"]
    errors: list[str] = []
    if set(value) != _ACTION_FIELDS:
        missing = sorted(_ACTION_FIELDS - set(value))
        extra = sorted(set(value) - _ACTION_FIELDS)
        if missing:
            errors.append("missing action fields: " + ", ".join(missing))
        if extra:
            errors.append("unknown action fields: " + ", ".join(extra))
    kind = value.get("kind")
    if isinstance(kind, ActionKind):
        kind = kind.value
        value["kind"] = kind
    if kind not in {item.value for item in ActionKind}:
        errors.append("kind must be tool, answer, or ask")
    if not isinstance(value.get("tool"), str):
        errors.append("tool must be a string")
    if not isinstance(value.get("args"), Mapping):
        errors.append("args must be an object")
    if not isinstance(value.get("reason"), str):
        errors.append("reason must be a string")
    if not isinstance(value.get("task_id"), str):
        errors.append("task_id must be a string")
    if kind == ActionKind.TOOL.value:
        if not str(value.get("tool", "")):
            errors.append("tool action requires a tool")
        if not str(value.get("task_id", "")):
            errors.append("tool action requires task_id")
    elif kind in {ActionKind.ANSWER.value, ActionKind.ASK.value}:
        if value.get("tool", "") != "":
            errors.append(f"{kind} action must not name a tool")
        if dict(value.get("args", {}) or {}):
            errors.append(f"{kind} action must have empty args")
        if value.get("task_id", "") != "":
            errors.append(f"{kind} action must not target a task")
    return value, errors


def _task_by_id(state: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(task.get("id", "")): task
        for task in state.get("tasks", []) or []
        if isinstance(task, Mapping)
    }


def _dependencies_ready(task: Mapping[str, Any], tasks: Mapping[str, Mapping[str, Any]]) -> bool:
    return all(
        str(tasks.get(str(dependency), {}).get("status", "")) in _TERMINAL_TASK_STATUSES
        for dependency in task.get("dependencies", []) or []
    )


def _task_action_allowed(
    task: Mapping[str, Any],
    state: Mapping[str, Any],
    last_result: Mapping[str, Any] | None,
) -> bool:
    """Return whether the projected current task can receive one action.

    ReAct decisions may verify a successful action whose effect is not yet
    verified, or retry an action that returned a retryable/no-result
    observation.  A failed non-retryable action is a terminal boundary and
    must not be called again.
    """

    status = str(task.get("status", ""))
    if status in {"pending", "running"}:
        return True
    if last_result is None:
        return False
    if status == "succeeded":
        return bool(last_result.get("ok")) and not bool(last_result.get("verified"))
    if status == "failed":
        return bool(last_result.get("retryable")) or bool(last_result.get("ok"))
    return False


def _duplicate_verified_effect(action: Mapping[str, Any], state: Mapping[str, Any]) -> bool:
    if str(action.get("tool", "")) not in _EFFECT_TOOLS:
        return False
    for record in state.get("history", []) or []:
        if not isinstance(record, Mapping):
            continue
        if record.get("tool") != action.get("tool") or record.get("task_id") != action.get("task_id"):
            continue
        result = record.get("result") or {}
        if isinstance(result, Mapping) and bool(result.get("ok")) and bool(result.get("verified")):
            return True
    return False


def evaluate_action(
    case: Mapping[str, Any],
    action: Mapping[str, Any] | NextAction,
) -> dict[str, Any]:
    """Evaluate one action without executing it or interpreting its prose."""

    normalized, schema_errors = _normalize_action(action)
    expected = case.get("expected") or {}
    state = case.get("state") or {}
    observation = case.get("observation") or {}
    expected_kind = str(expected.get("kind", ""))
    expected_tools = {str(tool) for tool in expected.get("tools", []) or []}
    expected_task_id = str(expected.get("task_id", ""))
    expected_args = dict(expected.get("args", {}) or {})
    if normalized is None:
        normalized = {"kind": "", "tool": "", "args": {}, "reason": "", "task_id": ""}

    actual_kind = str(normalized.get("kind", ""))
    actual_tool = str(normalized.get("tool", ""))
    actual_task_id = str(normalized.get("task_id", ""))
    actual_args = dict(normalized.get("args", {}) or {}) if isinstance(normalized.get("args"), Mapping) else {}
    available_tools = {str(tool) for tool in state.get("available_tools", []) or []}
    tasks = _task_by_id(state)
    selected_task = tasks.get(actual_task_id)
    last_result = observation.get("last_result") if isinstance(observation, Mapping) else None
    if not isinstance(last_result, Mapping):
        last_result = None

    kind_correct = actual_kind == expected_kind
    tool_correct = actual_kind != ActionKind.TOOL.value or actual_tool in expected_tools
    args_correct = actual_kind != ActionKind.TOOL.value or actual_args == expected_args
    task_correct = actual_kind != ActionKind.TOOL.value or actual_task_id == expected_task_id
    available_tool = actual_kind != ActionKind.TOOL.value or actual_tool in available_tools
    dependency_ready = (
        actual_kind != ActionKind.TOOL.value
        or selected_task is not None and _task_action_allowed(selected_task, state, last_result)
        and _dependencies_ready(selected_task, tasks)
    )
    duplicate_effect = _duplicate_verified_effect(normalized, state)
    premature_finish = (
        actual_kind == ActionKind.ANSWER.value
        and not bool(state.get("answer_ready", False))
    )
    safe_action = bool(
        not schema_errors
        and available_tool
        and dependency_ready
        and not duplicate_effect
        and not premature_finish
    )
    passed = bool(
        safe_action
        and kind_correct
        and tool_correct
        and args_correct
        and task_correct
    )
    errors = list(schema_errors)
    if not kind_correct:
        errors.append(f"expected kind {expected_kind!r}, got {actual_kind!r}")
    if actual_kind == ActionKind.TOOL.value and not tool_correct:
        errors.append(f"expected one of tools {sorted(expected_tools)!r}, got {actual_tool!r}")
    if actual_kind == ActionKind.TOOL.value and not args_correct:
        errors.append("tool arguments do not match the frozen expected binding")
    if actual_kind == ActionKind.TOOL.value and not task_correct:
        errors.append(f"expected task {expected_task_id!r}, got {actual_task_id!r}")
    if not available_tool:
        errors.append("selected tool is not available in the projected state")
    if not dependency_ready:
        errors.append("selected task is pending on an incomplete dependency")
    if duplicate_effect:
        errors.append("verified effect would be executed again")
    if premature_finish:
        errors.append("answer action would finish before the state is answer-ready")
    return {
        "case_id": str(case.get("id", "")),
        "family": str(case.get("family", "")),
        "expected_decision": str(expected.get("decision", "")),
        "expected_kind": expected_kind,
        "actual_kind": actual_kind,
        "expected_tools": sorted(expected_tools),
        "actual_tool": actual_tool,
        "expected_task_id": expected_task_id,
        "actual_task_id": actual_task_id,
        "schema_validity": float(not schema_errors),
        "kind_accuracy": float(kind_correct),
        "tool_selection_accuracy": float(tool_correct and available_tool) if expected_kind == ActionKind.TOOL.value else float(kind_correct),
        "argument_accuracy": float(args_correct) if expected_kind == ActionKind.TOOL.value else float(kind_correct),
        "task_targeting_accuracy": float(task_correct and dependency_ready) if expected_kind == ActionKind.TOOL.value else float(kind_correct),
        "safe_action": float(safe_action),
        "duplicate_effect": duplicate_effect,
        "premature_finish": premature_finish,
        "errors": errors,
        "passed": passed,
    }


def aggregate_metrics(reports: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate deterministic action-selection metrics."""

    values = list(reports)
    tool_cases = [item for item in values if item.get("expected_kind") == ActionKind.TOOL.value]
    return {
        "case_count": len(values),
        "pass_count": sum(bool(item.get("passed")) for item in values),
        "schema_validity": sum(float(item.get("schema_validity", 0.0)) for item in values) / len(values) if values else 0.0,
        "kind_accuracy": sum(float(item.get("kind_accuracy", 0.0)) for item in values) / len(values) if values else 0.0,
        "tool_selection_accuracy": sum(float(item.get("tool_selection_accuracy", 0.0)) for item in tool_cases) / len(tool_cases) if tool_cases else 0.0,
        "argument_accuracy": sum(float(item.get("argument_accuracy", 0.0)) for item in tool_cases) / len(tool_cases) if tool_cases else 0.0,
        "task_targeting_accuracy": sum(float(item.get("task_targeting_accuracy", 0.0)) for item in tool_cases) / len(tool_cases) if tool_cases else 0.0,
        "safe_action_rate": sum(float(item.get("safe_action", 0.0)) for item in values) / len(values) if values else 0.0,
        "duplicate_effect_count": sum(bool(item.get("duplicate_effect")) for item in values),
        "premature_finish_count": sum(bool(item.get("premature_finish")) for item in values),
        "schema_error_count": sum(not bool(item.get("schema_validity")) for item in values),
    }


__all__ = [
    "DATASET_PATH",
    "DATASET_VERSION",
    "aggregate_metrics",
    "dataset_hash",
    "evaluate_action",
    "golden_action",
    "load_dataset",
    "validate_dataset",
]
