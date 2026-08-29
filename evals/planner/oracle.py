"""Deterministic Oracle for the v2.4A Planner Capability Dataset.

The Oracle evaluates plan structure and explicit goal-unit coverage.  It does
not ask an LLM whether two tasks are "similar" and it never executes a task.
That keeps benchmark failures attributable to planning rather than Runtime or
Provider behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from agent.planner.evaluator import PlannerMetrics, aggregate_metrics
from agent.task import Task, Verb


DATASET_PATH = Path(__file__).with_name("dataset.json")
DATASET_VERSION = "v2.4A-planner-v1"
VALID_FAMILIES = {f"P{number}" for number in range(1, 13)}
VALID_MODES = {"chat", "plan", "abstain"}
_SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class DatasetValidation:
    valid: bool
    errors: tuple[str, ...]


def load_dataset(path: Path = DATASET_PATH) -> dict[str, Any]:
    """Load and validate the frozen dataset envelope."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    validation = validate_dataset(payload)
    if not validation.valid:
        raise ValueError("invalid Planner dataset: " + "; ".join(validation.errors))
    return payload


def validate_dataset(payload: Mapping[str, Any]) -> DatasetValidation:
    """Validate dataset shape, coverage, and goal-unit references."""

    errors: list[str] = []
    if payload.get("version") != DATASET_VERSION:
        errors.append(f"version must be {DATASET_VERSION!r}")
    cases = payload.get("cases")
    if not isinstance(cases, list):
        return DatasetValidation(False, ("cases must be a list",))
    if len(cases) < 50:
        errors.append(f"dataset requires at least 50 cases, got {len(cases)}")

    ids: set[str] = set()
    families: set[str] = set()
    for index, case in enumerate(cases):
        prefix = f"cases[{index}]"
        if not isinstance(case, Mapping):
            errors.append(f"{prefix} must be an object")
            continue
        case_id = str(case.get("id", ""))
        if not case_id:
            errors.append(f"{prefix}.id is required")
        elif case_id in ids:
            errors.append(f"duplicate case id: {case_id}")
        ids.add(case_id)

        family = str(case.get("family", ""))
        families.add(family)
        if family not in VALID_FAMILIES:
            errors.append(f"{prefix}.family is invalid: {family!r}")
        if not str(case.get("input", "")).strip():
            errors.append(f"{prefix}.input is required")
        mode = str(case.get("expected_mode", ""))
        if mode not in VALID_MODES:
            errors.append(f"{prefix}.expected_mode is invalid: {mode!r}")

        goal_units = case.get("goal_units")
        if not isinstance(goal_units, list):
            errors.append(f"{prefix}.goal_units must be a list")
            continue
        unit_ids: set[str] = set()
        for unit_index, unit in enumerate(goal_units):
            unit_prefix = f"{prefix}.goal_units[{unit_index}]"
            if not isinstance(unit, Mapping):
                errors.append(f"{unit_prefix} must be an object")
                continue
            unit_id = str(unit.get("id", ""))
            previous_unit_ids = {
                str(item.get("id", ""))
                for item in goal_units[:unit_index]
                if isinstance(item, Mapping)
            }
            if not unit_id:
                errors.append(f"{unit_prefix}.id is required")
            elif unit_id in unit_ids:
                errors.append(f"{prefix} duplicate goal unit id: {unit_id}")
            unit_ids.add(unit_id)
            verbs = unit.get("verbs")
            if not isinstance(verbs, list) or not verbs:
                errors.append(f"{unit_prefix}.verbs must be a non-empty list")
            else:
                invalid_verbs = [value for value in verbs if value not in {verb.value for verb in Verb}]
                if invalid_verbs:
                    errors.append(f"{unit_prefix} invalid verbs: {invalid_verbs}")
            target_type = str(unit.get("target_type", ""))
            if target_type not in {"file", "symbol", "text", "none"}:
                errors.append(f"{unit_prefix}.target_type is invalid: {target_type!r}")
            if target_type in {"file", "symbol"} and not str(unit.get("target", "")).strip():
                errors.append(f"{unit_prefix}.target is required for {target_type}")
            dependencies = unit.get("depends_on", [])
            if not isinstance(dependencies, list):
                errors.append(f"{unit_prefix}.depends_on must be a list")
            elif any(str(value) not in previous_unit_ids for value in dependencies):
                errors.append(f"{unit_prefix}.depends_on references an unknown unit")

        min_tasks = case.get("min_tasks")
        max_tasks = case.get("max_tasks")
        if not isinstance(min_tasks, int) or not isinstance(max_tasks, int) or min_tasks < 0 or max_tasks < min_tasks:
            errors.append(f"{prefix} has invalid min_tasks/max_tasks")
        completed = case.get("completed_units", [])
        if not isinstance(completed, list) or any(str(value) not in unit_ids for value in completed):
            errors.append(f"{prefix}.completed_units references an unknown unit")
        if family == "P12" and not completed:
            errors.append(f"{prefix} P12 case must contain completed_units")
        constraints = case.get("constraints", {})
        if not isinstance(constraints, Mapping):
            errors.append(f"{prefix}.constraints must be an object")

    missing_families = sorted(VALID_FAMILIES - families)
    if missing_families:
        errors.append("missing families: " + ", ".join(missing_families))
    return DatasetValidation(not errors, tuple(errors))


def dataset_hash(payload: Mapping[str, Any] | None = None) -> str:
    """Return a stable hash for the versioned dataset envelope."""

    value = payload if payload is not None else load_dataset()
    canonical = json.dumps(
        {"version": value["version"], "cases": value["cases"]},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _active_units(case: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    completed = {str(value) for value in case.get("completed_units", []) or []}
    return [
        unit for unit in case.get("goal_units", []) or []
        if str(unit.get("id", "")) not in completed
    ]


def golden_plan(case: Mapping[str, Any]) -> dict[str, Any]:
    """Build a canonical, intentionally minimal plan for self-checking."""

    mode = str(case.get("expected_mode", ""))
    if mode != "plan":
        return {"tasks": [], "abstain": mode == "abstain", "metadata": {"mode": mode}}

    active = _active_units(case)
    task_ids = {str(unit["id"]): f"task-{index + 1}" for index, unit in enumerate(active)}
    completed = {str(value) for value in case.get("completed_units", []) or []}
    tasks: list[dict[str, Any]] = []
    for index, unit in enumerate(active):
        dependencies = [
            task_ids[str(dependency)]
            for dependency in unit.get("depends_on", []) or []
            if str(dependency) not in completed and str(dependency) in task_ids
        ]
        target = str(unit.get("target", ""))
        tasks.append({
            "id": f"task-{index + 1}",
            "verb": str(unit["verbs"][0]),
            "target": target,
            "target_type": str(unit["target_type"]),
            "goal": f"完成：{target or unit['id']}",
            "description": str(case["input"]),
            "success_condition": f"{target or unit['id']} 已完成并可验证",
            "dependencies": dependencies,
            "children": [],
        })
    return {
        "tasks": tasks,
        "abstain": False,
        "metadata": {"mode": mode, "completed_units": sorted(completed)},
    }


def _norm(value: object) -> str:
    return _SPACE_RE.sub("", str(value or "")).lower()


def _target_matches(unit: Mapping[str, Any], task: Mapping[str, Any]) -> bool:
    actual = _norm(task.get("target", ""))
    if str(unit.get("target_type", "")) != str(task.get("target_type", "")):
        return False
    if str(unit.get("target_type", "")) == "text":
        targets = [unit.get("target", "")]
        targets.extend(unit.get("target_aliases", []) or [])
        return any(
            bool(_norm(target))
            and (_norm(target) in actual or actual in _norm(target))
            for target in targets
        )
    return _norm(unit.get("target", "")) == actual


def _structural(tasks: object) -> tuple[bool, bool, list[dict[str, Any]], list[str]]:
    """Return schema validity, dependency validity, parsed tasks, errors."""

    if not isinstance(tasks, list):
        return False, False, [], ["tasks must be a list"]
    parsed: list[dict[str, Any]] = []
    errors: list[str] = []
    schema_valid = True
    for index, raw in enumerate(tasks):
        if not isinstance(raw, Mapping):
            schema_valid = False
            errors.append(f"task[{index}] must be an object")
            continue
        try:
            task = Task.from_dict(dict(raw))
        except Exception as exc:
            schema_valid = False
            errors.append(f"task[{index}] schema error: {type(exc).__name__}")
            continue
        parsed.append(task.to_dict())

    ids = [str(task.get("id", "")) for task in parsed]
    if len(ids) != len(set(ids)) or any(not value for value in ids):
        schema_valid = False
        errors.append("task ids must be unique and non-empty")
    id_set = set(ids)
    dependency_valid = schema_valid
    positions = {task_id: index for index, task_id in enumerate(ids)}
    graph: dict[str, list[str]] = {}
    for task in parsed:
        task_id = str(task.get("id", ""))
        dependencies = [str(value) for value in task.get("dependencies", []) or []]
        graph[task_id] = dependencies
        for dependency in dependencies:
            if dependency not in id_set or positions[dependency] >= positions[task_id]:
                dependency_valid = False
                errors.append(f"invalid dependency {task_id} -> {dependency}")

    colors = {task_id: 0 for task_id in ids}

    def visit(task_id: str) -> bool:
        colors[task_id] = 1
        for dependency in graph.get(task_id, []):
            if dependency not in colors:
                continue
            if colors[dependency] == 1 or (colors[dependency] == 0 and visit(dependency)):
                return True
        colors[task_id] = 2
        return False

    for task_id in ids:
        if colors[task_id] == 0 and visit(task_id):
            dependency_valid = False
            errors.append("dependency graph contains a cycle")
            break
    return schema_valid, dependency_valid, parsed, errors


def _match_units(
    units: Sequence[Mapping[str, Any]],
    tasks: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, str], list[str], list[str]]:
    matched: dict[str, str] = {}
    used: set[str] = set()
    for unit in units:
        unit_id = str(unit["id"])
        acceptable = {str(value) for value in unit.get("verbs", []) or []}
        for task in tasks:
            task_id = str(task.get("id", ""))
            if task_id in used:
                continue
            if str(task.get("verb", "")) not in acceptable:
                continue
            if not _target_matches(unit, task):
                continue
            matched[unit_id] = task_id
            used.add(task_id)
            break
    missing = [str(unit["id"]) for unit in units if str(unit["id"]) not in matched]
    unexpected = [str(task.get("id", "")) for task in tasks if str(task.get("id", "")) not in used]
    return matched, missing, unexpected


def _dependency_accuracy(
    case: Mapping[str, Any],
    tasks: Sequence[Mapping[str, Any]],
    matched: Mapping[str, str],
) -> float:
    active = {str(unit["id"]): unit for unit in _active_units(case)}
    expected: set[tuple[str, str]] = set()
    task_by_id = {str(task.get("id", "")): task for task in tasks}
    for unit_id, task_id in matched.items():
        for dependency in active[unit_id].get("depends_on", []) or []:
            dependency_id = str(dependency)
            if dependency_id in matched:
                expected.add((task_id, matched[dependency_id]))
    actual = {
        (task_id, str(dependency))
        for task_id in matched.values()
        for dependency in task_by_id[task_id].get("dependencies", []) or []
    }
    if not expected and not actual:
        return 1.0
    return 1.0 if expected == actual else len(expected & actual) / len(expected | actual)


def _constraints_ok(
    case: Mapping[str, Any],
    tasks: Sequence[Mapping[str, Any]],
    matched: Mapping[str, str],
) -> bool:
    constraints = case.get("constraints", {}) or {}
    verbs = {str(task.get("verb", "")) for task in tasks}
    if verbs.intersection({str(value) for value in constraints.get("forbidden_verbs", []) or []}):
        return False
    if not {str(value) for value in constraints.get("required_verbs", []) or []}.issubset(verbs):
        return False

    task_by_id = {str(task.get("id", "")): task for task in tasks}
    graph = {
        task_id: {str(value) for value in task.get("dependencies", []) or []}
        for task_id, task in task_by_id.items()
    }

    def depends_on(start: str, target: str, seen: set[str] | None = None) -> bool:
        visited = seen or set()
        if start in visited:
            return False
        visited.add(start)
        if target in graph.get(start, set()):
            return True
        return any(depends_on(dep, target, visited) for dep in graph.get(start, set()))

    for group in constraints.get("parallel_groups", []) or []:
        group_ids = [
            task_by_id.get(matched.get(str(unit_id), str(unit_id)), {}).get("id")
            for unit_id in group
        ]
        if any(value is None for value in group_ids):
            return False
        for index, first in enumerate(group_ids):
            for second in group_ids[index + 1:]:
                if depends_on(str(first), str(second)) or depends_on(str(second), str(first)):
                    return False
    return True


def evaluate_plan(case: Mapping[str, Any], plan: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate one plan against one frozen case."""

    mode = str(case.get("expected_mode", ""))
    tasks = plan.get("tasks") if isinstance(plan, Mapping) else None
    abstain = bool(plan.get("abstain", False)) if isinstance(plan, Mapping) else False
    structural_schema, dependency_valid, parsed, structural_errors = _structural(tasks)
    active = _active_units(case)
    mode_correct = (
        (mode == "chat" and not parsed and not abstain)
        or (mode == "abstain" and not parsed and abstain)
        or (mode == "plan" and bool(parsed) and not abstain)
    )
    schema_validity = 1.0 if mode in {"chat", "abstain"} and not parsed and mode_correct else float(structural_schema and mode_correct)
    dependency_validity_value = 1.0 if not parsed and mode in {"chat", "abstain"} and mode_correct else float(dependency_valid and mode_correct)

    matched, missing, unexpected = _match_units(active, parsed)
    critical_missing = any(
        bool(unit.get("critical", False)) and str(unit["id"]) in missing
        for unit in active
    )
    min_tasks = int(case.get("min_tasks", 0))
    max_tasks = int(case.get("max_tasks", 0))
    task_count = len(parsed)
    granularity = float(min_tasks <= task_count <= max_tasks)
    overplanned = float(task_count > max_tasks or bool(unexpected))
    constraints_ok = _constraints_ok(case, parsed, matched)
    all_critical_present = not critical_missing
    executable = float(
        mode_correct
        and structural_schema
        and dependency_valid
        and constraints_ok
        and all_critical_present
    )
    plan_validity = float(mode_correct and structural_schema and dependency_valid)
    missing_rate = len(missing) / len(active) if active else 0.0
    unnecessary_rate = len(unexpected) / task_count if task_count else 0.0
    return {
        "case_id": str(case.get("id", "")),
        "family": str(case.get("family", "")),
        "expected_mode": mode,
        "schema_validity": schema_validity,
        "dependency_validity": dependency_validity_value,
        "plan_validity": plan_validity,
        "dependency_accuracy": _dependency_accuracy(case, parsed, matched) if mode == "plan" else float(mode_correct),
        "task_granularity": granularity,
        "unnecessary_task_rate": unnecessary_rate,
        "missing_task_rate": missing_rate,
        "executable_plan": executable,
        "overplanned": overplanned,
        "critical_missing": float(critical_missing),
        "goal_unit_count": len(active),
        "predicted_task_count": task_count,
        "unnecessary_task_count": len(unexpected),
        "missing_task_count": len(missing),
        "matched_goal_units": matched,
        "missing_goal_units": missing,
        "unexpected_task_ids": unexpected,
        "errors": structural_errors,
        "passed": bool(executable if mode == "plan" else mode_correct),
    }


def evaluate_golden(payload: Mapping[str, Any] | None = None) -> tuple[list[dict[str, Any]], PlannerMetrics]:
    """Run the Oracle against every generated golden plan."""

    value = payload if payload is not None else load_dataset()
    reports = [
        evaluate_plan(case, golden_plan(case))
        for case in value["cases"]
    ]
    return reports, aggregate_metrics(reports)


__all__ = [
    "DATASET_PATH",
    "DATASET_VERSION",
    "DatasetValidation",
    "dataset_hash",
    "evaluate_golden",
    "evaluate_plan",
    "golden_plan",
    "load_dataset",
    "validate_dataset",
]
