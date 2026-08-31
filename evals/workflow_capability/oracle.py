"""Deterministic Oracle for v2.4C Workflow capability decisions."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from agent.workflow_decision import WorkflowDecision, WorkflowDecisionKind


DATASET_PATH = Path(__file__).with_name("dataset.json")
DATASET_VERSION = "v2.4C-workflow-capability-v1"
VALID_FAMILIES = {
    "CLEAR_MATCH",
    "FALSE_MATCH_GUARD",
    "PARAMETER_BINDING",
    "SIMPLE_TASK_DECLINE",
    "CONTINUATION",
    "RUNTIME_BOUNDARY",
}
_DECISION_FIELDS = frozenset({"kind", "workflow_id", "bindings", "reason"})


def load_dataset(path: Path = DATASET_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    errors = validate_dataset(payload)
    if errors:
        raise ValueError("invalid Workflow capability dataset: " + "; ".join(errors))
    return payload


def validate_dataset(payload: Mapping[str, Any]) -> tuple[str, ...]:
    errors: list[str] = []
    if payload.get("version") != DATASET_VERSION:
        errors.append(f"version must be {DATASET_VERSION!r}")
    if payload.get("scope") != "workflow_decision":
        errors.append("scope must be workflow_decision")
    if payload.get("contract") != (
        "Goal + Context + AvailableWorkflows -> WorkflowDecision"
    ):
        errors.append("contract declaration is invalid")

    catalog = payload.get("workflow_catalog")
    if not isinstance(catalog, Mapping) or not catalog:
        errors.append("workflow_catalog must be a non-empty object")
        return tuple(errors)
    for workflow_id, workflow in catalog.items():
        _validate_workflow(str(workflow_id), workflow, errors)

    cases = payload.get("cases")
    if not isinstance(cases, list) or len(cases) != 24:
        errors.append("Workflow capability dataset requires exactly 24 cases")
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
            errors.append(f"{prefix} has invalid or duplicate id")
        ids.add(case_id)
        family = str(case.get("family", ""))
        families.add(family)
        if family not in VALID_FAMILIES:
            errors.append(f"{case_id} has invalid family: {family}")
        if not str(case.get("goal", "")).strip():
            errors.append(f"{case_id} goal is required")
        state = case.get("state")
        if not isinstance(state, Mapping):
            errors.append(f"{case_id} state must be an object")
            continue
        _validate_state(case_id, state, catalog, errors)
        expected = case.get("expected")
        if not isinstance(expected, Mapping):
            errors.append(f"{case_id} expected must be an object")
            continue
        _validate_expected(case_id, expected, state, catalog, errors)

    if families != VALID_FAMILIES:
        errors.append("dataset must cover all six Workflow families")
    return tuple(errors)


def _validate_workflow(
    workflow_id: str,
    value: Any,
    errors: list[str],
) -> None:
    if not workflow_id or not isinstance(value, Mapping):
        errors.append(f"invalid workflow catalog entry: {workflow_id!r}")
        return
    for field in (
        "version",
        "description",
        "required_bindings",
        "defaults",
        "required_artifacts",
        "required_capabilities",
        "output_types",
    ):
        if field not in value:
            errors.append(f"workflow {workflow_id} missing {field}")
    for field in (
        "required_bindings",
        "required_artifacts",
        "required_capabilities",
        "output_types",
    ):
        if not isinstance(value.get(field), list):
            errors.append(f"workflow {workflow_id}.{field} must be a list")
    if not isinstance(value.get("defaults"), Mapping):
        errors.append(f"workflow {workflow_id}.defaults must be an object")


def _validate_state(
    case_id: str,
    state: Mapping[str, Any],
    catalog: Mapping[str, Any],
    errors: list[str],
) -> None:
    for field in (
        "available_workflows",
        "artifacts",
        "capabilities",
        "facts",
        "active_workflow",
    ):
        if field not in state:
            errors.append(f"{case_id} state.{field} is required")
    available = state.get("available_workflows")
    if not isinstance(available, list) or any(
        item not in catalog for item in available
    ):
        errors.append(f"{case_id} has unknown available_workflows")
    if not isinstance(state.get("artifacts"), Mapping):
        errors.append(f"{case_id} state.artifacts must be an object")
    if not isinstance(state.get("capabilities"), list):
        errors.append(f"{case_id} state.capabilities must be a list")
    if not isinstance(state.get("facts"), Mapping):
        errors.append(f"{case_id} state.facts must be an object")
    active = state.get("active_workflow")
    if active is not None:
        if not isinstance(active, Mapping):
            errors.append(f"{case_id} active_workflow must be null or object")
        else:
            workflow_id = str(active.get("workflow_id", ""))
            if workflow_id not in catalog:
                errors.append(f"{case_id} active workflow is unknown")
            if not isinstance(active.get("reuse_allowed"), bool):
                errors.append(f"{case_id} active workflow requires reuse_allowed")


def _validate_expected(
    case_id: str,
    expected: Mapping[str, Any],
    state: Mapping[str, Any],
    catalog: Mapping[str, Any],
    errors: list[str],
) -> None:
    try:
        decision = WorkflowDecision.model_validate({
            **dict(expected),
            "reason": str(expected.get("reason", "dataset self-check")),
        })
    except ValidationError as error:
        errors.append(f"{case_id} expected decision invalid: {error.errors()[0]['msg']}")
        return
    if decision.kind is WorkflowDecisionKind.INSTANTIATE:
        if decision.workflow_id not in state.get("available_workflows", []):
            errors.append(f"{case_id} expected workflow is unavailable")
        if decision.workflow_id not in catalog:
            return
        required = set(catalog[decision.workflow_id]["required_bindings"])
        if not required.issubset(decision.bindings):
            errors.append(f"{case_id} expected bindings are incomplete")
    if decision.kind is WorkflowDecisionKind.REUSE:
        active = state.get("active_workflow") or {}
        if active.get("workflow_id") != decision.workflow_id:
            errors.append(f"{case_id} reuse must target the active workflow")
        if active.get("reuse_allowed") is not True:
            errors.append(f"{case_id} expected unsafe workflow reuse")


def dataset_hash(payload: Mapping[str, Any] | None = None) -> str:
    value = payload if payload is not None else load_dataset()
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def golden_decision(case: Mapping[str, Any]) -> dict[str, Any]:
    expected = case["expected"]
    return {
        "kind": str(expected["kind"]),
        "workflow_id": str(expected.get("workflow_id", "")),
        "bindings": dict(expected.get("bindings") or {}),
        "reason": "dataset self-check",
    }


def _normalize_decision(
    value: Any,
) -> tuple[dict[str, Any] | None, tuple[str, ...]]:
    if isinstance(value, WorkflowDecision):
        raw = value.to_dict()
    elif isinstance(value, Mapping):
        raw = dict(value)
    else:
        return None, ("decision must be an object",)
    errors: list[str] = []
    if set(raw) != _DECISION_FIELDS:
        errors.append("decision fields must be kind, workflow_id, bindings, reason")
    try:
        decision = WorkflowDecision.model_validate(raw)
    except ValidationError as error:
        errors.extend(item["msg"] for item in error.errors())
        return None, tuple(errors)
    return decision.to_dict(), tuple(errors)


def evaluate_decision(
    dataset: Mapping[str, Any],
    case: Mapping[str, Any],
    decision: Any,
) -> dict[str, Any]:
    normalized, schema_errors = _normalize_decision(decision)
    expected = case["expected"]
    state = case["state"]
    catalog = dataset["workflow_catalog"]
    if normalized is None:
        normalized = {"kind": "", "workflow_id": "", "bindings": {}, "reason": ""}

    kind_accuracy = normalized["kind"] == expected["kind"]
    workflow_accuracy = normalized["workflow_id"] == expected["workflow_id"]
    binding_accuracy = normalized["bindings"] == expected["bindings"]
    safe, safety_errors = _safe_decision(normalized, state, catalog)
    expected_kind = str(expected["kind"])
    actual_kind = str(normalized["kind"])
    false_workflow_selection = int(
        expected_kind in {"decline", "ask"}
        and actual_kind in {"instantiate", "reuse"}
    )
    missed_workflow = int(
        expected_kind in {"instantiate", "reuse"}
        and actual_kind in {"decline", "ask"}
    )
    unsafe_reuse = int(actual_kind == "reuse" and not safe)
    passed = bool(
        not schema_errors
        and kind_accuracy
        and workflow_accuracy
        and binding_accuracy
        and safe
    )
    return {
        "passed": passed,
        "schema_validity": not schema_errors,
        "kind_accuracy": kind_accuracy,
        "workflow_accuracy": workflow_accuracy,
        "binding_accuracy": binding_accuracy,
        "safe_decision": safe,
        "false_workflow_selection": false_workflow_selection,
        "missed_workflow": missed_workflow,
        "unsafe_reuse": unsafe_reuse,
        "errors": [*schema_errors, *safety_errors],
        "normalized_decision": normalized,
    }


def _safe_decision(
    decision: Mapping[str, Any],
    state: Mapping[str, Any],
    catalog: Mapping[str, Any],
) -> tuple[bool, tuple[str, ...]]:
    errors: list[str] = []
    kind = str(decision.get("kind", ""))
    workflow_id = str(decision.get("workflow_id", ""))
    if kind == "instantiate":
        if workflow_id not in state.get("available_workflows", []):
            errors.append("selected workflow is unavailable")
            return False, tuple(errors)
        workflow = catalog[workflow_id]
        bindings = dict(decision.get("bindings") or {})
        required_bindings = set(workflow["required_bindings"])
        if not required_bindings.issubset(bindings):
            errors.append("required workflow binding is missing")
        capabilities = set(state.get("capabilities") or [])
        if not set(workflow["required_capabilities"]).issubset(capabilities):
            errors.append("required workflow capability is unavailable")
        artifacts = set((state.get("artifacts") or {}).keys())
        if not set(workflow["required_artifacts"]).issubset(artifacts):
            errors.append("required workflow artifact is unavailable")
    elif kind == "reuse":
        active = state.get("active_workflow") or {}
        if active.get("workflow_id") != workflow_id:
            errors.append("reuse does not target the active workflow")
        if active.get("status") != "active" or active.get("reuse_allowed") is not True:
            errors.append("Runtime projection does not allow workflow reuse")
    return not errors, tuple(errors)


def golden_self_check(dataset: Mapping[str, Any] | None = None) -> dict[str, Any]:
    value = dataset if dataset is not None else load_dataset()
    results = [
        evaluate_decision(value, case, golden_decision(case))
        for case in value["cases"]
    ]
    return {
        "total": len(results),
        "passed": sum(int(result["passed"]) for result in results),
        "false_workflow_selection": sum(
            result["false_workflow_selection"] for result in results
        ),
        "unsafe_reuse": sum(result["unsafe_reuse"] for result in results),
    }


__all__ = [
    "DATASET_VERSION",
    "dataset_hash",
    "evaluate_decision",
    "golden_decision",
    "golden_self_check",
    "load_dataset",
    "validate_dataset",
]
