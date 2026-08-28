"""Machine-only oracle for H8 results."""

from __future__ import annotations

from typing import Any, Mapping

from .cases import CASES


def validate_record(record: Mapping[str, Any]) -> tuple[bool, str]:
    case_id = str(record.get("case_id", ""))
    expected = next((case.oracle for case in CASES if case.case_id == case_id), None)
    if expected is None:
        return False, f"unknown case: {case_id}"
    if bool(record.get("passed", False)):
        return True, ""
    return False, str(record.get("detail", "") or f"oracle={expected}")


def validate_records(records: list[Mapping[str, Any]]) -> dict[str, Any]:
    by_id = {str(record.get("case_id", "")): record for record in records}
    missing = [case.case_id for case in CASES if case.case_id not in by_id]
    failures = []
    for case in CASES:
        record = by_id.get(case.case_id)
        if record is None:
            continue
        passed, detail = validate_record(record)
        if not passed:
            failures.append({"case_id": case.case_id, "detail": detail})
    return {
        "dataset_version": "h8-single-runtime-spine-v1",
        "total": len(CASES),
        "passed": len(CASES) - len(missing) - len(failures),
        "missing": missing,
        "failures": failures,
        "status": "PASS" if not missing and not failures else "FAIL",
    }


__all__ = ["validate_record", "validate_records"]
