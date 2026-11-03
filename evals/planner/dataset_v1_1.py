"""Calibrated v1.1 view of the frozen v2.4A Planner Dataset.

The v1 Dataset remains immutable.  v1.1 is a derived, versioned view that
adds a small deterministic alias catalog and explicit ownership metadata:
the four historical chat cases belong to routing acceptance, not Planner
decomposition acceptance.
"""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Mapping

from .oracle import DATASET_PATH, dataset_hash, load_dataset, validate_dataset


DATASET_VERSION = "v2.4A-planner-v1.1"
ALIAS_CATALOG_PATH = Path(__file__).with_name("target_aliases_v1_1.json")
ROUTING_CASE_IDS = frozenset({"PA001", "PA002", "PA003", "PA004"})


def _load_alias_catalog(path: Path = ALIAS_CATALOG_PATH) -> dict[tuple[str, str], tuple[str, ...]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != "v2.4A-planner-aliases-v1.1":
        raise ValueError("invalid Planner v1.1 alias catalog version")
    catalog: dict[tuple[str, str], tuple[str, ...]] = {}
    for rule in payload.get("rules", []):
        case_id = str(rule.get("case_id", ""))
        unit_id = str(rule.get("unit_id", ""))
        aliases = tuple(str(value).strip() for value in rule.get("aliases", []) or [])
        if not case_id or not unit_id or not aliases or any(not value for value in aliases):
            raise ValueError("invalid Planner v1.1 alias rule")
        key = (case_id, unit_id)
        if key in catalog:
            raise ValueError(f"duplicate Planner v1.1 alias rule: {key}")
        if len(set(aliases)) != len(aliases):
            raise ValueError(f"duplicate aliases in Planner v1.1 rule: {key}")
        catalog[key] = aliases
    return catalog


def load_dataset_v1_1(path: Path = DATASET_PATH) -> dict[str, Any]:
    """Return the immutable v1 cases with the v1.1 calibration view applied."""

    base = load_dataset(path)
    catalog = _load_alias_catalog()
    value = deepcopy(base)
    value["version"] = DATASET_VERSION
    for case in value["cases"]:
        case_id = str(case["id"])
        case["ownership"] = "routing" if case_id in ROUTING_CASE_IDS else "planner"
        for unit in case.get("goal_units", []) or []:
            aliases = catalog.get((case_id, str(unit["id"])))
            if aliases:
                if str(unit.get("target_type", "")) != "text":
                    raise ValueError(f"aliases require text target: {case_id}/{unit['id']}")
                unit["target_aliases"] = list(aliases)
    return value


def validate_dataset_v1_1(payload: Mapping[str, Any]) -> tuple[bool, tuple[str, ...]]:
    """Validate v1.1 without weakening the frozen v1 structural validator."""

    errors: list[str] = []
    if payload.get("version") != DATASET_VERSION:
        errors.append(f"version must be {DATASET_VERSION!r}")
    cases = payload.get("cases")
    if not isinstance(cases, list):
        return False, ("cases must be a list",)

    base_view = deepcopy(dict(payload))
    base_view["version"] = "v2.4A-planner-v1"
    base_validation = validate_dataset(base_view)
    errors.extend(base_validation.errors)
    if len(cases) != 50:
        errors.append(f"v1.1 requires 50 cases, got {len(cases)}")
    routing_ids = {
        str(case.get("id", ""))
        for case in cases
        if isinstance(case, Mapping) and case.get("ownership") == "routing"
    }
    if routing_ids != ROUTING_CASE_IDS:
        errors.append("v1.1 routing ownership must contain exactly PA001-PA004")
    if any(
        not isinstance(case, Mapping)
        or case.get("ownership") not in {"planner", "routing"}
        for case in cases
    ):
        errors.append("every v1.1 case must declare planner or routing ownership")

    catalog = _load_alias_catalog()
    case_by_id = {str(case.get("id", "")): case for case in cases if isinstance(case, Mapping)}
    for (case_id, unit_id), aliases in catalog.items():
        case = case_by_id.get(case_id)
        if case is None:
            errors.append(f"alias references unknown case: {case_id}")
            continue
        unit = next(
            (item for item in case.get("goal_units", []) or [] if str(item.get("id", "")) == unit_id),
            None,
        )
        if unit is None:
            errors.append(f"alias references unknown unit: {case_id}/{unit_id}")
            continue
        if tuple(unit.get("target_aliases", []) or []) != aliases:
            errors.append(f"alias materialization mismatch: {case_id}/{unit_id}")
    materialized = {
        (str(case.get("id", "")), str(unit.get("id", "")))
        for case in cases
        if isinstance(case, Mapping)
        for unit in case.get("goal_units", []) or []
        if unit.get("target_aliases")
    }
    if materialized != set(catalog):
        errors.append("v1.1 contains aliases outside the frozen alias catalog")
    return not errors, tuple(errors)


def planner_cases_v1_1(payload: Mapping[str, Any] | None = None) -> tuple[Mapping[str, Any], ...]:
    value = payload if payload is not None else load_dataset_v1_1()
    return tuple(case for case in value["cases"] if case.get("ownership") == "planner")


def routing_cases_v1_1(payload: Mapping[str, Any] | None = None) -> tuple[Mapping[str, Any], ...]:
    value = payload if payload is not None else load_dataset_v1_1()
    return tuple(case for case in value["cases"] if case.get("ownership") == "routing")


def dataset_hash_v1_1(payload: Mapping[str, Any] | None = None) -> str:
    value = payload if payload is not None else load_dataset_v1_1()
    return dataset_hash(value)


__all__ = [
    "ALIAS_CATALOG_PATH",
    "DATASET_VERSION",
    "ROUTING_CASE_IDS",
    "dataset_hash_v1_1",
    "load_dataset_v1_1",
    "planner_cases_v1_1",
    "routing_cases_v1_1",
    "validate_dataset_v1_1",
]
