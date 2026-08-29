"""Deterministic ownership oracle for cases moved out of Planner scope."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


DATASET_PATH = Path(__file__).with_name("dataset.json")
DATASET_VERSION = "v2.4A-routing-v1"


def load_dataset(path: Path = DATASET_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    errors = validate_dataset(payload)
    if errors:
        raise ValueError("invalid routing dataset: " + "; ".join(errors))
    return payload


def validate_dataset(payload: Mapping[str, Any]) -> tuple[str, ...]:
    errors: list[str] = []
    if payload.get("version") != DATASET_VERSION:
        errors.append(f"version must be {DATASET_VERSION!r}")
    cases = payload.get("cases")
    if not isinstance(cases, list) or len(cases) != 4:
        errors.append("routing dataset requires exactly four cases")
        return tuple(errors)
    ids: set[str] = set()
    for case in cases:
        if not isinstance(case, Mapping):
            errors.append("case must be an object")
            continue
        case_id = str(case.get("id", ""))
        if not case_id or case_id in ids:
            errors.append(f"invalid or duplicate case id: {case_id!r}")
        ids.add(case_id)
        if case.get("expected_route") != "CHAT":
            errors.append(f"{case_id} expected_route must be CHAT")
        if case.get("planner_must_not_be_called") is not True:
            errors.append(f"{case_id} must forbid Planner invocation")
        if case.get("execution_required") is not False:
            errors.append(f"{case_id} must not require execution")
    return tuple(errors)


def dataset_hash(payload: Mapping[str, Any] | None = None) -> str:
    value = payload if payload is not None else load_dataset()
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def golden_route(case: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "case_id": str(case.get("id", "")),
        "route": str(case.get("expected_route", "")),
        "planner_called": False,
        "execution_required": bool(case.get("execution_required", False)),
    }


def evaluate_route(case: Mapping[str, Any], observation: Mapping[str, Any]) -> dict[str, Any]:
    expected = golden_route(case)
    actual_route = str(observation.get("route", ""))
    planner_called = bool(observation.get("planner_called", False))
    execution_required = bool(observation.get("execution_required", False))
    passed = (
        actual_route == expected["route"]
        and planner_called is False
        and execution_required is False
    )
    return {
        "case_id": expected["case_id"],
        "expected_route": expected["route"],
        "actual_route": actual_route,
        "planner_called": planner_called,
        "execution_required": execution_required,
        "passed": passed,
    }


__all__ = [
    "DATASET_PATH",
    "DATASET_VERSION",
    "dataset_hash",
    "evaluate_route",
    "golden_route",
    "load_dataset",
    "validate_dataset",
]
