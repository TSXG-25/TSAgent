#!/usr/bin/env python3
"""Attribute an existing v2.4D-3b Provider baseline.

This is a measurement-only audit.  It consumes a frozen baseline, never calls
a Provider, never repairs the Provider response, and never overwrites the
baseline's strict Oracle result.  The semantic view is diagnostic only.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals.memory_learning.oracle import dataset_hash, load_dataset  # noqa: E402


AUDIT_VERSION = "v2.4D-3b-attribution-audit-v1"
DEFAULT_BASELINE = Path("/private/tmp/v24d_d3b_deepseek.json")
DEFAULT_JSON = Path("/private/tmp/v24d_d3b_deepseek_attribution.json")
DEFAULT_MARKDOWN = Path("/private/tmp/v24d_d3b_deepseek_attribution.md")
SEMANTIC_FIELDS = (
    "action",
    "memory_type",
    "scope",
    "canonical_key",
    "value",
    "provenance",
)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _same_semantics(
    decision: Mapping[str, Any],
    expected: Mapping[str, Any],
) -> bool:
    return all(decision.get(field) == expected.get(field) for field in SEMANTIC_FIELDS)


def _raw_object(raw: Any) -> Mapping[str, Any] | None:
    if not isinstance(raw, str):
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, Mapping) else None


def _review(
    record: Mapping[str, Any],
    dataset_case: Mapping[str, Any] | None,
) -> dict[str, Any]:
    status = str(record.get("provider_status", ""))
    oracle = record.get("oracle_result")
    decision = record.get("normalized_decision")
    expected = dataset_case.get("expected", {}) if dataset_case else {}

    if status == "PROVIDER_ERROR":
        return {
            "category": "P-PROV",
            "subcategory": "PROVIDER_ERROR",
            "semantic_match_without_reason": False,
            "note": "Provider infrastructure failure; exclude from capability denominator.",
        }
    if status == "INTEGRATION_ERROR":
        return {
            "category": "P-INT",
            "subcategory": "INTEGRATION_ERROR",
            "semantic_match_without_reason": False,
            "note": "Production boundary integration failure; exclude from capability denominator.",
        }
    if status == "SCHEMA_INVALID" or not isinstance(decision, Mapping):
        raw = _raw_object(record.get("raw_provider_output"))
        if (
            isinstance(raw, Mapping)
            and raw.get("action") == "IGNORE"
            and raw.get("provenance") == ""
        ):
            subcategory = "IGNORE_PROVENANCE_SHAPE"
            note = (
                "Provider selected the safe IGNORE action but emitted provenance "
                "as an empty string instead of the canonical empty object."
            )
        else:
            subcategory = "SCHEMA_INVALID"
            note = "Provider response could not be represented as the canonical decision."
        return {
            "category": "P-CAP",
            "subcategory": subcategory,
            "semantic_match_without_reason": False,
            "note": note,
        }

    safe = bool(oracle.get("safe_decision")) if isinstance(oracle, Mapping) else False
    semantic_match = _same_semantics(decision, expected)
    reason_match = decision.get("reason_code") == expected.get("reason_code")
    if not safe:
        return {
            "category": "P-CAP",
            "subcategory": "UNSAFE_DECISION",
            "semantic_match_without_reason": semantic_match,
            "note": (
                "Provider proposed a write where deterministic policy expects "
                "no durable write; this is a safety failure."
            ),
        }
    if semantic_match and not reason_match:
        return {
            "category": "P-MEASUREMENT",
            "subcategory": "REASON_CODE_VOCABULARY",
            "semantic_match_without_reason": True,
            "note": (
                "Action, memory fields, scope, key, value, and provenance match; "
                "only the free-form reason_code differs from the frozen Oracle."
            ),
        }
    if semantic_match and reason_match:
        return {
            "category": "PASS",
            "subcategory": "EXACT_DECISION",
            "semantic_match_without_reason": True,
            "note": "Provider decision matches the frozen Oracle and safety policy.",
        }
    return {
        "category": "P-CAP",
        "subcategory": "DECISION_MISMATCH",
        "semantic_match_without_reason": semantic_match,
        "note": "Safe, schema-valid proposal differs from the expected decision fields.",
    }


def build_audit(baseline: Mapping[str, Any], baseline_path: Path) -> dict[str, Any]:
    dataset = load_dataset()
    cases_by_id = {str(case["id"]): case for case in dataset["cases"]}
    records: list[dict[str, Any]] = []
    for record in baseline.get("cases", []):
        case_id = str(record.get("case_id", ""))
        review = _review(record, cases_by_id.get(case_id))
        records.append({
            "case_id": case_id,
            "provider_status": record.get("provider_status"),
            "format_path": record.get("format_path"),
            "mechanical_pass": bool(
                isinstance(record.get("oracle_result"), Mapping)
                and record["oracle_result"].get("passed")
            ),
            "schema_valid": bool(
                isinstance(record.get("normalized_decision"), Mapping)
            ),
            **review,
        })

    category_counts = Counter(item["category"] for item in records)
    subcategory_counts = Counter(item["subcategory"] for item in records)
    status_counts = Counter(str(item.get("provider_status", "")) for item in records)
    parsed = sum(item["schema_valid"] for item in records)
    semantic_matches = sum(item["semantic_match_without_reason"] for item in records)
    strict_pass = sum(item["mechanical_pass"] for item in records)
    return {
        "audit_version": AUDIT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_baseline": {
            "path": str(baseline_path),
            "sha256": _sha256(baseline_path),
            "git_head": baseline.get("git_head"),
            "provider": baseline.get("provider"),
        },
        "dataset": {
            "version": dataset.get("version"),
            "hash": dataset_hash(dataset),
            "case_count": len(dataset.get("cases", [])),
        },
        "configuration": {
            "provider_calls_in_audit": 0,
            "baseline_unchanged": True,
            "oracle_or_dataset_modified": False,
            "json_repair": False,
        },
        "summary": {
            "case_count": len(records),
            "provider_status_counts": dict(status_counts),
            "strict_oracle_pass_count": strict_pass,
            "strict_oracle_pass_rate": round(strict_pass / len(records) * 100, 1)
            if records else None,
            "schema_valid_count": parsed,
            "schema_valid_rate_over_all_cases": round(parsed / len(records) * 100, 1)
            if records else None,
            "schema_valid_rate_over_parsed_cases": 100.0 if parsed else None,
            "semantic_match_without_reason_count": semantic_matches,
            "semantic_match_without_reason_rate_over_all_cases": round(
                semantic_matches / len(records) * 100, 1
            ) if records else None,
            "attribution_category_counts": dict(category_counts),
            "attribution_subcategory_counts": dict(subcategory_counts),
            "structured_to_raw_fallback_count": sum(
                item.get("format_path") == "STRUCTURED_TO_RAW_FALLBACK"
                for item in records
            ),
            "memory_writes": 0,
        },
        "cases": records,
        "notes": [
            "Strict Oracle results remain those from the original baseline; this audit does not rewrite them.",
            "P-MEASUREMENT is a diagnostic category for semantically matching decisions rejected only by reason_code vocabulary.",
            "P-CAP:UNSAFE_DECISION is retained as a real safety failure even when other fields are plausible.",
            "A schema-valid Provider response is not evidence of durable Memory commit; this baseline performed zero writes.",
        ],
    }


def _markdown(report: Mapping[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# v2.4D-3b Memory Learning Attribution Audit",
        "",
        f"- Source baseline: `{report['source_baseline']['path']}`",
        f"- Source SHA-256: `{report['source_baseline']['sha256']}`",
        f"- HEAD: `{report['source_baseline']['git_head']}`",
        f"- Dataset: `{report['dataset']['version']}` / `{report['dataset']['hash']}`",
        f"- Provider calls in audit: **{report['configuration']['provider_calls_in_audit']}**",
        "",
        "## Summary",
        "",
        f"- Strict frozen Oracle: **{summary['strict_oracle_pass_count']}/{summary['case_count']}**",
        f"- Canonical schema-valid responses: **{summary['schema_valid_count']}/{summary['case_count']}**",
        f"- Semantic match excluding reason_code: **{summary['semantic_match_without_reason_count']}/{summary['case_count']}**",
        f"- Attribution categories: `{json.dumps(summary['attribution_category_counts'], ensure_ascii=False, sort_keys=True)}`",
        f"- Attribution subcategories: `{json.dumps(summary['attribution_subcategory_counts'], ensure_ascii=False, sort_keys=True)}`",
        f"- Format fallback path: `{summary['structured_to_raw_fallback_count']}/{summary['case_count']}` same-provider structured→raw",
        "",
        "The original baseline remains authoritative for the strict score. This report only adds a diagnostic attribution view.",
        "",
        "## Cases",
        "",
        "| Case | Status | Attribution | Semantic sans reason | Note |",
        "| --- | --- | --- | :---: | --- |",
    ]
    for case in report["cases"]:
        note = str(case["note"]).replace("|", "\\|")
        lines.append(
            f"| {case['case_id']} | {case['provider_status']} | "
            f"{case['category']}:{case['subcategory']} | "
            f"{'yes' if case['semantic_match_without_reason'] else 'no'} | {note} |"
        )
    lines.extend(["", "## Notes", "", *[f"- {note}" for note in report["notes"]], ""])
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--output", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    args = parser.parse_args(argv)
    baseline = _read_json(args.baseline)
    report = build_audit(baseline, args.baseline)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text(_markdown(report), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"JSON: {args.output}")
    print(f"Markdown: {args.markdown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
