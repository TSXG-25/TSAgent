#!/usr/bin/env python3
"""One-pass real Provider baseline for v2.4D Memory Learning.

This harness calls the production ``MemoryLearningProvider`` exactly once per
case.  It never imports a Memory store, commits a decision, repairs JSON,
retries a case, or falls back to a golden decision.  The deterministic D-2
policy is intentionally not applied here: this is an isolated Provider
proposal measurement.  A later integration test may apply the authorization
handoff and persistence boundary separately.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.memory.learning import (  # noqa: E402
    ExistingMemory,
    InteractionEvidence,
    MemoryPolicyProjection,
    ResolutionEvidence,
)
from agent.memory.learning_provider import (  # noqa: E402
    MemoryLearningProvider,
    MemoryLearningSelectionError,
)
from evals.memory_learning.oracle import (  # noqa: E402
    dataset_hash,
    evaluate_decision,
    load_dataset,
)
HARNESS_VERSION = "v2.4D-3b-real-memory-learning-baseline-v1"
DEFAULT_JSON = ROOT / "realtest_reports" / "results" / "v24d_memory_learning_baseline.json"
DEFAULT_MARKDOWN = ROOT / "realtest_reports" / "results" / "v24d_memory_learning_baseline.md"


def _git_head() -> str | None:
    import subprocess

    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _existing(value: Mapping[str, Any] | None) -> ExistingMemory | None:
    if value is None:
        return None
    return ExistingMemory(
        scope=str(value["scope"]),
        canonical_key=str(value["canonical_key"]),
        value=str(value["value"]),
    )


def _evidence(case: Mapping[str, Any]) -> InteractionEvidence:
    raw = case["evidence"]
    raw_resolution = raw.get("resolution")
    resolution = (
        ResolutionEvidence(
            utterance=str(raw_resolution["utterance"]),
            kind=str(raw_resolution["kind"]),
            metadata=dict(raw_resolution["metadata"]),
        )
        if isinstance(raw_resolution, Mapping)
        else None
    )
    return InteractionEvidence(
        evidence_id=str(raw["id"]),
        source_kind=str(raw["source_kind"]),
        source_ref=str(raw["source_ref"]),
        text=str(raw["text"]),
        memory_type=str(raw["memory_type"]),
        requested_scope=str(raw["requested_scope"]),
        canonical_key=str(raw["canonical_key"]),
        value=str(raw["value"]),
        explicit_persist=bool(raw["explicit_persist"]),
        sensitive=bool(raw["sensitive"]),
        secret=bool(raw["secret"]),
        volatile=bool(raw["volatile"]),
        existing=_existing(raw.get("existing")),
        resolution=resolution,
    )


def _policy(evidence: InteractionEvidence, case_id: str) -> MemoryPolicyProjection:
    return MemoryPolicyProjection(
        scope=evidence.requested_scope,
        namespace=f"d3-baseline-{case_id.lower()}",
    )


def _error_record(error: MemoryLearningSelectionError) -> dict[str, Any]:
    return {
        "code": error.code,
        "message": str(error),
        "provider": error.provider,
        "format_path": error.format_path,
        "raw_output": error.raw_output,
        "candidate": (
            error.candidate.to_dict() if error.candidate is not None else None
        ),
    }


async def _run_case(
    case: Mapping[str, Any],
    *,
    provider_name: str,
) -> dict[str, Any]:
    case_id = str(case["id"])
    evidence = _evidence(case)
    existing = evidence.existing
    policy = _policy(evidence, case_id)
    selector = MemoryLearningProvider()
    started = time.perf_counter()
    try:
        selection = await selector.select_with_evidence(
            evidence,
            existing,
            policy,
        )
    except MemoryLearningSelectionError as error:
        elapsed_ms = (time.perf_counter() - started) * 1000
        error_record = _error_record(error)
        provider_status = (
            "PROVIDER_ERROR"
            if error.code == "PROVIDER_ERROR"
            else "SCHEMA_INVALID"
        )
        return {
            "case_id": case_id,
            "input": case["evidence"],
            "provider_status": provider_status,
            "provider": error.provider or provider_name,
            "provider_path": "SINGLE_PROVIDER",
            "format_path": error.format_path,
            "latency_ms": round(elapsed_ms, 3),
            "token_usage": None,
            "raw_provider_output": error.raw_output,
            "normalized_decision": None,
            "oracle_result": None,
            "failure_category": "P-PROV" if provider_status == "PROVIDER_ERROR" else "P-INT",
            "failure_subcategory": error.code,
            "evidence": error_record,
        }
    except Exception as error:
        elapsed_ms = (time.perf_counter() - started) * 1000
        return {
            "case_id": case_id,
            "input": case["evidence"],
            "provider_status": "INTEGRATION_ERROR",
            "provider": provider_name,
            "provider_path": "SINGLE_PROVIDER",
            "format_path": "NOT_RECORDED",
            "latency_ms": round(elapsed_ms, 3),
            "token_usage": None,
            "raw_provider_output": None,
            "normalized_decision": None,
            "oracle_result": None,
            "failure_category": "P-INT",
            "failure_subcategory": type(error).__name__,
            "evidence": {"message": f"{type(error).__name__}: {error}"},
        }

    elapsed_ms = (time.perf_counter() - started) * 1000
    decision = selection.decision
    oracle = evaluate_decision(case, decision.to_dict())
    passed = bool(oracle["passed"])
    if passed:
        failure_category = None
        failure_subcategory = None
    elif not oracle["safe_decision"]:
        failure_category = "P-CAP"
        failure_subcategory = "UNSAFE_DECISION"
    else:
        failure_category = "P-CAP"
        failure_subcategory = "DECISION_MISMATCH"
    return {
        "case_id": case_id,
        "input": case["evidence"],
        "provider_status": "SUCCESS",
        "provider": selection.evidence.provider or provider_name,
        "provider_path": selection.evidence.provider_path,
        "format_path": selection.evidence.format_path,
        "latency_ms": round(elapsed_ms, 3),
        "token_usage": None,
        "raw_provider_output": selection.evidence.raw_output,
        "normalized_decision": decision.to_dict(),
        "oracle_result": oracle,
        "failure_category": failure_category,
        "failure_subcategory": failure_subcategory,
        "evidence": {
            "structured_error": selection.evidence.structured_error,
            "proposal_is_uncommitted": True,
            "memory_write_performed": False,
        },
    }


def _percent(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator * 100, 1)


def _latency_summary(records: Sequence[Mapping[str, Any]]) -> dict[str, float | None]:
    values = sorted(
        float(record["latency_ms"])
        for record in records
        if record.get("latency_ms") is not None
    )
    if not values:
        return {"mean_ms": None, "p50_ms": None, "p95_ms": None, "max_ms": None}
    return {
        "mean_ms": round(statistics.mean(values), 3),
        "p50_ms": round(statistics.median(values), 3),
        "p95_ms": round(values[min(len(values) - 1, int(len(values) * 0.95))], 3),
        "max_ms": round(max(values), 3),
    }


def build_report(
    records: list[dict[str, Any]],
    *,
    dataset: Mapping[str, Any],
    provider_name: str,
) -> dict[str, Any]:
    counts = Counter(str(record["provider_status"]) for record in records)
    failures = Counter(
        str(record["failure_category"])
        for record in records
        if record.get("failure_category")
    )
    subcategories = Counter(
        str(record["failure_subcategory"])
        for record in records
        if record.get("failure_subcategory")
    )
    oracle_records = [
        record["oracle_result"]
        for record in records
        if record.get("oracle_result") is not None
    ]
    pass_count = sum(bool(result.get("passed")) for result in oracle_records)
    schema_count = sum(bool(result.get("schema_validity")) for result in oracle_records)
    accuracy_count = sum(bool(result.get("decision_accuracy")) for result in oracle_records)
    safe_count = sum(bool(result.get("safe_decision")) for result in oracle_records)
    return {
        "harness_version": HARNESS_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_head": _git_head(),
        "provider": provider_name,
        "configuration": {
            "automatic_retry": False,
            "provider_fallback": False,
            "golden_repair": False,
            "json_repair": False,
            "memory_writes": 0,
            "persistence_calls": 0,
            "tool_execution": False,
        },
        "dataset": {
            "path": "evals/memory_learning/dataset.json",
            "version": dataset.get("version"),
            "case_count": len(dataset.get("cases", [])),
            "hash": dataset_hash(dataset),
        },
        "summary": {
            "case_count": len(records),
            "provider_status_counts": dict(counts),
            "failure_category_counts": dict(failures),
            "failure_subcategory_counts": dict(subcategories),
            "evaluated_count": len(oracle_records),
            "pass_count": pass_count,
            "pass_rate": _percent(pass_count, len(oracle_records)),
            "schema_validity_count": schema_count,
            "schema_validity_rate": _percent(schema_count, len(oracle_records)),
            "decision_accuracy_count": accuracy_count,
            "decision_accuracy_rate": _percent(accuracy_count, len(oracle_records)),
            "safe_decision_count": safe_count,
            "safe_decision_rate": _percent(safe_count, len(oracle_records)),
            "false_memory_write_count": sum(
                int(result.get("false_memory_write", 0)) for result in oracle_records
            ),
            "unprovenanced_write_count": sum(
                int(result.get("unprovenanced_write", 0)) for result in oracle_records
            ),
            "scope_violation_count": sum(
                int(result.get("scope_violation", 0)) for result in oracle_records
            ),
            "sensitive_write_count": sum(
                int(result.get("sensitive_write", 0)) for result in oracle_records
            ),
            "volatile_write_count": sum(
                int(result.get("volatile_write", 0)) for result in oracle_records
            ),
            "duplicate_write_count": sum(
                int(result.get("duplicate_write", 0)) for result in oracle_records
            ),
            "latency_ms": _latency_summary(records),
        },
        "cases": records,
        "notes": [
            "One production Provider selection attempt per case; no automatic retry.",
            "Structured-to-raw is recorded as a same-provider format path, not Provider fallback.",
            "This report measures uncommitted Provider proposals; it does not prove durable Memory commit.",
            "token_usage is null because the current production selection evidence does not expose provider usage metadata.",
        ],
    }


def _markdown(report: Mapping[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# v2.4D-3b Real Memory Learning Provider Baseline",
        "",
        f"- HEAD: `{report.get('git_head')}`",
        f"- Provider: `{report.get('provider')}`",
        f"- Dataset: `{report['dataset']['version']}` / `{report['dataset']['hash']}`",
        f"- Cases: {summary['case_count']}",
        "- Automatic retry: `false`; Provider fallback: `false`; Memory writes: `0`",
        "",
        "## Summary",
        "",
        f"- Evaluated: {summary['evaluated_count']}",
        f"- Capability pass: {summary['pass_count']} ({summary['pass_rate']}%)",
        f"- Schema validity: {summary['schema_validity_count']} ({summary['schema_validity_rate']}%)",
        f"- Decision accuracy: {summary['decision_accuracy_count']} ({summary['decision_accuracy_rate']}%)",
        f"- Safe decision: {summary['safe_decision_count']} ({summary['safe_decision_rate']}%)",
        f"- Provider statuses: `{json.dumps(summary['provider_status_counts'], ensure_ascii=False, sort_keys=True)}`",
        f"- Failure categories: `{json.dumps(summary['failure_category_counts'], ensure_ascii=False, sort_keys=True)}`",
        f"- Failure subcategories: `{json.dumps(summary['failure_subcategory_counts'], ensure_ascii=False, sort_keys=True)}`",
        "",
        "## Cases",
        "",
        "| Case | Provider | Format | Result | Category | Subcategory | Latency ms |",
        "| --- | --- | --- | --- | --- | --- | ---: |",
    ]
    for record in report["cases"]:
        result = record.get("oracle_result") or {}
        outcome = "PASS" if result.get("passed") else record["provider_status"]
        lines.append(
            f"| {record['case_id']} | {record['provider']} | {record['format_path']} | "
            f"{outcome} | {record.get('failure_category') or ''} | "
            f"{record.get('failure_subcategory') or ''} | {record['latency_ms']} |"
        )
    lines.extend([
        "",
        "## Evidence notes",
        "",
        *[f"- {note}" for note in report["notes"]],
        "",
    ])
    return "\n".join(lines)


def run_baseline(
    *,
    case_ids: set[str] | None,
    provider_name: str,
) -> dict[str, Any]:
    from realtest_reports.harness.v24d_memory_learning_provider_preflight import (
        build_preflight_report,
    )

    preflight = build_preflight_report()
    if preflight["status"] != "READY_FOR_REAL_BASELINE":
        raise RuntimeError(
            "D-3b preflight blocked: "
            + ", ".join(str(item["code"]) for item in preflight["blockers"])
        )
    dataset = load_dataset()
    cases = [
        case for case in dataset["cases"]
        if case_ids is None or str(case["id"]) in case_ids
    ]
    if case_ids is not None:
        discovered = {str(case["id"]) for case in cases}
        missing = sorted(case_ids - discovered)
        if missing:
            raise ValueError(f"unknown memory learning case ids: {', '.join(missing)}")
    records: list[dict[str, Any]] = []
    for case in cases:
        records.append(asyncio.run(_run_case(case, provider_name=provider_name)))
    report = build_report(records, dataset=dataset, provider_name=provider_name)
    report["preflight"] = {
        "status": preflight["status"],
        "head": preflight["head"],
        "harness_version": preflight["harness_version"],
        "provider_entry_count": len(
            preflight["production_entries"]["provider_learning"]
        ),
        "boundary_violations": preflight["production_entries"][
            "provider_boundary_violations"
        ],
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ids", help="comma-separated case IDs; default is all 24")
    parser.add_argument("--provider", default="deepseek")
    parser.add_argument("--output", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    args = parser.parse_args(argv)
    if args.provider != "deepseek":
        raise SystemExit("D-3b baseline is pinned to provider=deepseek")
    import os

    os.environ["TSAGENT_LLM_PROVIDER"] = args.provider
    case_ids = (
        {item.strip() for item in args.ids.split(",") if item.strip()}
        if args.ids
        else None
    )
    report = run_baseline(case_ids=case_ids, provider_name=args.provider)
    serialized = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(serialized, encoding="utf-8")
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text(_markdown(report), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    print(f"JSON: {args.output}")
    print(f"Markdown: {args.markdown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
