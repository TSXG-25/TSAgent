"""Deterministic Oracle for the v2.4A uncertainty-policy benchmark."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import hashlib
import json
from pathlib import Path
from typing import Any


DATASET_PATH = Path(__file__).with_name("dataset.json")
DATASET_VERSION = "v2.4A-uncertainty-v1"


def load_dataset(path: Path = DATASET_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    errors = validate_dataset(payload)
    if errors:
        raise ValueError("invalid uncertainty dataset: " + "; ".join(errors))
    return payload


def validate_dataset(payload: Mapping[str, Any]) -> tuple[str, ...]:
    errors: list[str] = []
    if payload.get("version") != DATASET_VERSION:
        errors.append(f"version must be {DATASET_VERSION!r}")
    cases = payload.get("cases")
    if not isinstance(cases, list) or not 20 <= len(cases) <= 30:
        errors.append("uncertainty dataset must contain 20-30 cases")
        return tuple(errors)
    ids: set[str] = set()
    pair_counts: dict[str, int] = {}
    for case in cases:
        if not isinstance(case, Mapping):
            errors.append("case must be an object")
            continue
        case_id = str(case.get("id", ""))
        if not case_id or case_id in ids:
            errors.append(f"invalid or duplicate case id: {case_id!r}")
        ids.add(case_id)
        if not str(case.get("input", "")).strip():
            errors.append(f"{case_id} input is required")
        if case.get("context") not in {"none", "valid_continuation", "grounding_candidate", "repo_context"}:
            errors.append(f"{case_id} has invalid context")
        if not isinstance(case.get("expected_abstain"), bool):
            errors.append(f"{case_id} expected_abstain must be boolean")
        pair_id = str(case.get("pair_id", ""))
        if not pair_id:
            errors.append(f"{case_id} pair_id is required")
        pair_counts[pair_id] = pair_counts.get(pair_id, 0) + 1
    if any(count < 2 for count in pair_counts.values()):
        errors.append("every uncertainty pair must contain at least two cases")
    if sum(case.get("expected_abstain") is True for case in cases if isinstance(case, Mapping)) == 0:
        errors.append("dataset needs positive abstain cases")
    if sum(case.get("expected_abstain") is False for case in cases if isinstance(case, Mapping)) == 0:
        errors.append("dataset needs proceed cases")
    return tuple(errors)


def dataset_hash(payload: Mapping[str, Any] | None = None) -> str:
    value = payload if payload is not None else load_dataset()
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def evaluate_decision(case: Mapping[str, Any], actual_abstain: bool) -> dict[str, Any]:
    expected = bool(case.get("expected_abstain", False))
    actual = bool(actual_abstain)
    return {
        "case_id": str(case.get("id", "")),
        "pair_id": str(case.get("pair_id", "")),
        "expected_abstain": expected,
        "actual_abstain": actual,
        "passed": expected == actual,
        "outcome": (
            "TRUE_ABSTAIN"
            if expected and actual
            else "MISSED_ABSTENTION"
            if expected and not actual
            else "FALSE_ABSTENTION"
            if not expected and actual
            else "TRUE_PROCEED"
        ),
    }


def aggregate_metrics(reports: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    values = list(reports)
    true_abstain = sum(
        bool(item.get("expected_abstain")) and bool(item.get("actual_abstain"))
        for item in values
    )
    false_abstain = sum(
        not bool(item.get("expected_abstain")) and bool(item.get("actual_abstain"))
        for item in values
    )
    missed = sum(
        bool(item.get("expected_abstain")) and not bool(item.get("actual_abstain"))
        for item in values
    )
    expected_abstain = sum(bool(item.get("expected_abstain")) for item in values)
    expected_proceed = len(values) - expected_abstain
    return {
        "case_count": len(values),
        "pass_count": sum(bool(item.get("passed")) for item in values),
        "true_abstain": true_abstain,
        "false_abstain": false_abstain,
        "missed_abstention": missed,
        "true_proceed": expected_proceed - false_abstain,
        "abstain_precision": true_abstain / (true_abstain + false_abstain) if true_abstain + false_abstain else 0.0,
        "abstain_recall": true_abstain / expected_abstain if expected_abstain else 0.0,
        "false_abstention_rate": false_abstain / expected_proceed if expected_proceed else 0.0,
        "missed_abstention_rate": missed / expected_abstain if expected_abstain else 0.0,
    }


__all__ = [
    "DATASET_PATH",
    "DATASET_VERSION",
    "aggregate_metrics",
    "dataset_hash",
    "evaluate_decision",
    "load_dataset",
    "validate_dataset",
]
