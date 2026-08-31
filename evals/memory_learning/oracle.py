"""Deterministic contract Oracle for v2.4D Memory Learning.

This module intentionally does not import production Memory stores.  It checks
the proposed evidence/decision contract and provides a golden self-check for
the dataset; it is not a Memory Learner implementation.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


DATASET_PATH = Path(__file__).with_name("dataset.json")
DATASET_VERSION = "v2.4D-memory-learning-v1"
VALID_FAMILIES = {
    "ELIGIBILITY",
    "SOURCE_AUTHORITY",
    "SCOPE",
    "DEDUP_CONFLICT",
    "SENSITIVITY_VOLATILITY",
    "LIFECYCLE_BOUNDARY",
}
VALID_ACTIONS = {"STORE", "UPDATE", "IGNORE"}
VALID_SCOPES = {"session", "user", "repository"}
VALID_MEMORY_TYPES = {"fact", "preference", "summary", "resolution"}
PERSISTABLE_SOURCES = {"user_statement", "user_confirmed_resolution"}
DECISION_FIELDS = frozenset({
    "action",
    "memory_type",
    "scope",
    "canonical_key",
    "value",
    "provenance",
    "reason_code",
})
PROVENANCE_FIELDS = frozenset({"evidence_id", "source_kind", "source_ref"})


def load_dataset(path: Path = DATASET_PATH) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    errors = validate_dataset(payload)
    if errors:
        raise ValueError("invalid memory learning dataset: " + "; ".join(errors))
    return payload


def validate_dataset(payload: Mapping[str, Any]) -> tuple[str, ...]:
    errors: list[str] = []
    if payload.get("version") != DATASET_VERSION:
        errors.append(f"version must be {DATASET_VERSION!r}")
    if payload.get("scope") != "memory_learning_decision":
        errors.append("scope must be memory_learning_decision")
    if payload.get("contract") != (
        "InteractionEvidence + MemoryPolicyProjection -> MemoryLearningDecision"
    ):
        errors.append("contract declaration is invalid")

    policy = payload.get("policy")
    if not isinstance(policy, Mapping):
        errors.append("policy must be an object")
    else:
        if set(policy.get("actions", [])) != VALID_ACTIONS:
            errors.append("policy actions must be STORE, UPDATE, IGNORE")
        if set(policy.get("scopes", [])) != VALID_SCOPES:
            errors.append("policy scopes are invalid")
        if set(policy.get("memory_types", [])) != VALID_MEMORY_TYPES:
            errors.append("policy memory_types are invalid")
        for flag in (
            "sensitive_requires_explicit_persist",
            "secrets_never_store",
            "volatile_observations_default_ignore",
        ):
            if not isinstance(policy.get(flag), bool):
                errors.append(f"policy.{flag} must be boolean")

    cases = payload.get("cases")
    if not isinstance(cases, list) or len(cases) != 24:
        errors.append("memory learning dataset requires exactly 24 cases")
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
        evidence = case.get("evidence")
        if not isinstance(evidence, Mapping):
            errors.append(f"{case_id} evidence must be an object")
        else:
            _validate_evidence(case_id, evidence, errors)
        expected = case.get("expected")
        if not isinstance(expected, Mapping):
            errors.append(f"{case_id} expected must be an object")
        else:
            _validate_expected(case_id, expected, evidence, errors)

    if families != VALID_FAMILIES:
        errors.append("dataset must cover all six Memory Learning families")
    if len(ids) != 24:
        errors.append("dataset case ids must be unique")
    return tuple(errors)


def _validate_evidence(
    case_id: str,
    evidence: Mapping[str, Any],
    errors: list[str],
) -> None:
    for field in (
        "id", "source_kind", "source_ref", "text", "memory_type",
        "requested_scope", "canonical_key", "value", "explicit_persist",
        "sensitive", "secret", "volatile", "existing",
    ):
        if field not in evidence:
            errors.append(f"{case_id} evidence.{field} is required")
    for field in ("id", "source_kind", "source_ref", "text", "memory_type", "requested_scope"):
        if field in evidence and not str(evidence.get(field, "")).strip():
            errors.append(f"{case_id} evidence.{field} must be non-empty")
    if evidence.get("memory_type") not in VALID_MEMORY_TYPES:
        errors.append(f"{case_id} evidence memory_type is invalid")
    if evidence.get("requested_scope") not in VALID_SCOPES:
        errors.append(f"{case_id} evidence requested_scope is invalid")
    for field in ("explicit_persist", "sensitive", "secret", "volatile"):
        if not isinstance(evidence.get(field), bool):
            errors.append(f"{case_id} evidence.{field} must be boolean")
    existing = evidence.get("existing")
    if existing is not None:
        if not isinstance(existing, Mapping):
            errors.append(f"{case_id} evidence.existing must be null or object")
        else:
            for field in ("scope", "canonical_key", "value"):
                if not str(existing.get(field, "")).strip():
                    errors.append(f"{case_id} existing.{field} must be non-empty")


def _validate_expected(
    case_id: str,
    expected: Mapping[str, Any],
    evidence: Mapping[str, Any] | None,
    errors: list[str],
) -> None:
    if set(expected) != DECISION_FIELDS:
        errors.append(f"{case_id} expected fields must be {sorted(DECISION_FIELDS)}")
        return
    action = expected.get("action")
    if action not in VALID_ACTIONS:
        errors.append(f"{case_id} expected action is invalid")
        return
    if action == "IGNORE":
        if any(expected.get(field) not in ("", {}) for field in (
            "memory_type", "scope", "canonical_key", "value", "provenance",
        )):
            errors.append(f"{case_id} IGNORE must not carry write fields")
        return
    if expected.get("memory_type") not in VALID_MEMORY_TYPES:
        errors.append(f"{case_id} expected memory_type is invalid")
    if expected.get("scope") not in VALID_SCOPES:
        errors.append(f"{case_id} expected scope is invalid")
    for field in ("canonical_key", "value", "reason_code"):
        if not str(expected.get(field, "")).strip():
            errors.append(f"{case_id} expected {field} is required")
    provenance = expected.get("provenance")
    if not isinstance(provenance, Mapping) or set(provenance) != PROVENANCE_FIELDS:
        errors.append(f"{case_id} expected provenance fields are invalid")
    if isinstance(evidence, Mapping) and isinstance(provenance, Mapping):
        if provenance.get("evidence_id") != evidence.get("id"):
            errors.append(f"{case_id} provenance evidence_id must match evidence.id")
        if provenance.get("source_kind") != evidence.get("source_kind"):
            errors.append(f"{case_id} provenance source_kind must match evidence")
        if provenance.get("source_ref") != evidence.get("source_ref"):
            errors.append(f"{case_id} provenance source_ref must match evidence")


def dataset_hash(payload: Mapping[str, Any] | None = None) -> str:
    value = payload if payload is not None else load_dataset()
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def golden_decision(case: Mapping[str, Any]) -> dict[str, Any]:
    return dict(case["expected"])


def _normalize_decision(value: Any) -> tuple[dict[str, Any] | None, tuple[str, ...]]:
    if not isinstance(value, Mapping):
        return None, ("decision must be an object",)
    raw = dict(value)
    errors: list[str] = []
    if set(raw) != DECISION_FIELDS:
        errors.append("decision fields are invalid")
        return None, tuple(errors)
    if raw.get("action") not in VALID_ACTIONS:
        errors.append("decision action is invalid")
        return None, tuple(errors)
    action = raw["action"]
    if action == "IGNORE":
        if any(raw.get(field) not in ("", {}) for field in (
            "memory_type", "scope", "canonical_key", "value", "provenance",
        )):
            errors.append("IGNORE cannot carry write fields")
    else:
        for field in ("memory_type", "scope", "canonical_key", "value", "reason_code"):
            if not str(raw.get(field, "")).strip():
                errors.append(f"{field} is required for {action}")
        if not isinstance(raw.get("provenance"), Mapping):
            errors.append("write decision provenance must be an object")
        elif set(raw["provenance"]) != PROVENANCE_FIELDS:
            errors.append("write decision provenance fields are invalid")
    return raw, tuple(errors)


def _safety_flags(
    case: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> tuple[bool, tuple[str, ...], dict[str, int]]:
    evidence = case["evidence"]
    action = str(decision.get("action", ""))
    errors: list[str] = []
    flags = {
        "false_memory_write": 0,
        "unprovenanced_write": 0,
        "scope_violation": 0,
        "sensitive_write": 0,
        "volatile_write": 0,
        "duplicate_write": 0,
    }
    if action == "IGNORE":
        return True, (), flags
    if action not in {"STORE", "UPDATE"}:
        return False, ("unknown write action",), flags
    if action in {"STORE", "UPDATE"} and str(case["expected"].get("action")) == "IGNORE":
        flags["false_memory_write"] = 1
    if evidence.get("source_kind") not in PERSISTABLE_SOURCES:
        flags["unprovenanced_write"] = 1
        errors.append("source is not authorized for a Memory write")
    provenance = decision.get("provenance")
    if not isinstance(provenance, Mapping):
        flags["unprovenanced_write"] = 1
        errors.append("write has no provenance")
    else:
        if provenance.get("evidence_id") != evidence.get("id"):
            flags["unprovenanced_write"] = 1
            errors.append("provenance evidence_id does not match")
        if provenance.get("source_kind") != evidence.get("source_kind"):
            flags["unprovenanced_write"] = 1
            errors.append("provenance source_kind does not match")
        if provenance.get("source_ref") != evidence.get("source_ref"):
            flags["unprovenanced_write"] = 1
            errors.append("provenance source_ref does not match")
    if decision.get("scope") != evidence.get("requested_scope"):
        flags["scope_violation"] = 1
        errors.append("decision widens or changes requested scope")
    if decision.get("memory_type") != evidence.get("memory_type"):
        errors.append("decision changes memory type")
    existing = evidence.get("existing")
    if action == "STORE" and existing is not None:
        if existing.get("canonical_key") == decision.get("canonical_key"):
            flags["duplicate_write"] = 1
            errors.append("STORE is invalid when the canonical key already exists")
    if action == "UPDATE":
        if not isinstance(existing, Mapping):
            errors.append("UPDATE requires an existing scoped fact")
        elif (
            existing.get("scope") != decision.get("scope")
            or existing.get("canonical_key") != decision.get("canonical_key")
        ):
            flags["scope_violation"] = 1
            errors.append("UPDATE target is outside the existing scoped key")
    if evidence.get("secret"):
        flags["sensitive_write"] = 1
        errors.append("secret evidence must never be stored")
    elif evidence.get("sensitive") and not evidence.get("explicit_persist"):
        flags["sensitive_write"] = 1
        errors.append("sensitive evidence lacks explicit persistence")
    if evidence.get("volatile"):
        flags["volatile_write"] = 1
        errors.append("volatile evidence cannot be stored as durable Memory")
    return not errors, tuple(errors), flags


def evaluate_decision(
    case: Mapping[str, Any],
    decision: Any,
) -> dict[str, Any]:
    normalized, schema_errors = _normalize_decision(decision)
    if normalized is None:
        normalized = {
            "action": "",
            "memory_type": "",
            "scope": "",
            "canonical_key": "",
            "value": "",
            "provenance": {},
            "reason_code": "",
        }
    safe, safety_errors, flags = _safety_flags(case, normalized)
    expected = case["expected"]
    exact = all(normalized.get(field) == expected.get(field) for field in DECISION_FIELDS)
    passed = bool(not schema_errors and exact and safe)
    return {
        "case_id": str(case.get("id", "")),
        "passed": passed,
        "schema_validity": not schema_errors,
        "decision_accuracy": exact,
        "safe_decision": safe,
        "errors": [*schema_errors, *safety_errors],
        "normalized_decision": normalized,
        **flags,
    }


def aggregate_metrics(reports: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    values = list(reports)
    return {
        "case_count": len(values),
        "pass_count": sum(bool(item.get("passed")) for item in values),
        "schema_validity": sum(bool(item.get("schema_validity")) for item in values),
        "decision_accuracy": sum(bool(item.get("decision_accuracy")) for item in values),
        "safe_decision_rate": sum(bool(item.get("safe_decision")) for item in values),
        "false_memory_write_count": sum(int(item.get("false_memory_write", 0)) for item in values),
        "unprovenanced_write_count": sum(int(item.get("unprovenanced_write", 0)) for item in values),
        "scope_violation_count": sum(int(item.get("scope_violation", 0)) for item in values),
        "sensitive_write_count": sum(int(item.get("sensitive_write", 0)) for item in values),
        "volatile_write_count": sum(int(item.get("volatile_write", 0)) for item in values),
        "duplicate_write_count": sum(int(item.get("duplicate_write", 0)) for item in values),
    }


def golden_self_check(dataset: Mapping[str, Any] | None = None) -> dict[str, Any]:
    value = dataset if dataset is not None else load_dataset()
    reports = [
        evaluate_decision(case, golden_decision(case))
        for case in value["cases"]
    ]
    return aggregate_metrics(reports)


__all__ = [
    "DATASET_PATH",
    "DATASET_VERSION",
    "aggregate_metrics",
    "dataset_hash",
    "evaluate_decision",
    "golden_decision",
    "golden_self_check",
    "load_dataset",
    "validate_dataset",
]
