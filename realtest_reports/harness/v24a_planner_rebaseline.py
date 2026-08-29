#!/usr/bin/env python3
"""Real-provider v2.4A-2d Planner re-baseline on the calibrated v1.1 view.

This harness reuses the production Planner entry point and the existing v1
call recorder.  It selects only the 46 Planner-owned cases from the v1.1
calibration view; the four historical chat cases are evaluated by the routing
benchmark instead.  Each selected case is submitted once with no case retry,
golden-plan fallback, Provider fallback, or output repair.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[2]
HARNESS_DIR = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(HARNESS_DIR) not in sys.path:
    sys.path.insert(0, str(HARNESS_DIR))

DEFAULT_JSON = ROOT / "realtest_reports" / "results" / "v24a_planner_rebaseline.json"
DEFAULT_MARKDOWN = ROOT / "realtest_reports" / "results" / "v24a_planner_rebaseline.md"
HARNESS_VERSION = "v2.4A-2d-real-planner-rebaseline-v2"
DATASET_HASH = "8c268b5855d109c7a2be940257ae0acf7edc877793dd5914cc020ae380aae023"
ROUTING_CASE_IDS = frozenset({"PA001", "PA002", "PA003", "PA004"})


def _p12_planning_context(case: Mapping[str, Any]) -> Any | None:
    """Build the same narrow projection a resumed Runtime would expose.

    The Dataset remains unchanged.  P12 metadata supplies a deterministic
    durable-state fixture; only task descriptors are passed to production
    Planner code, never the golden plan or raw checkpoint-like payload.
    """

    if str(case.get("family", "")) != "P12":
        return None
    units = {
        str(unit.get("id", "")): unit
        for unit in case.get("goal_units", []) or []
        if isinstance(unit, Mapping)
    }
    completed_ids = {
        str(value) for value in case.get("completed_units", []) or []
    }
    active_ids = set(units) - completed_ids

    def descriptor(unit: Mapping[str, Any], status: str, dependencies: list[str]) -> dict[str, Any]:
        verbs = unit.get("verbs", []) or []
        return {
            "id": str(unit.get("id", "")),
            "verb": str(verbs[0]) if verbs else "",
            "target": str(unit.get("target", "") or ""),
            "target_type": str(unit.get("target_type", "") or ""),
            "status": status,
            "dependencies": dependencies,
        }

    completed_tasks = tuple(
        descriptor(
            units[unit_id],
            "succeeded",
            [str(value) for value in units[unit_id].get("depends_on", []) or []],
        )
        for unit_id in case.get("completed_units", []) or []
        if str(unit_id) in units
    )
    continuation_scope = tuple(
        descriptor(
            unit,
            "pending",
            [
                str(value)
                for value in unit.get("depends_on", []) or []
                if str(value) in active_ids
            ],
        )
        for unit_id, unit in units.items()
        if unit_id in active_ids
    )
    from agent.cognition.cognitive_context import PlannerContext

    return PlannerContext(
        query=str(case.get("input", "")),
        completed_tasks=completed_tasks,
        continuation_scope=continuation_scope,
    )


def _build_attribution(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    categories = Counter(
        str(record["failure_category"])
        for record in records
        if record.get("failure_category")
    )
    subcategories = Counter(
        str(record["failure_subcategory"])
        for record in records
        if record.get("failure_subcategory")
    )
    return {
        "category_counts": dict(sorted(categories.items())),
        "subcategory_counts": dict(sorted(subcategories.items())),
        "p_cap_case_ids": [
            str(record["case_id"])
            for record in records
            if record.get("failure_category") == "P-CAP"
        ],
        "provider_error_case_ids": [
            str(record["case_id"])
            for record in records
            if record.get("failure_category") == "P-PROV"
        ],
        "runtime_integration_failure_case_ids": [
            str(record["case_id"])
            for record in records
            if record.get("failure_category") == "P-INT"
        ],
        "contract_or_oracle_failure_case_ids": [
            str(record["case_id"])
            for record in records
            if record.get("failure_category") == "P-CON"
        ],
        "uncertainty_policy_case_ids": [
            str(record["case_id"])
            for record in records
            if record.get("failure_category") == "P-UNCERTAINTY"
        ],
        "uncertainty_policy_included": False,
        "uncertainty_policy_dataset": "evals/uncertainty/dataset.json",
    }


def _reclassify_uncertainty_records(
    records: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Remove deterministic pre-Planner abstentions from capability scoring.

    The calibrated contract assigns uncertainty handling to the routing/policy
    boundary.  A case that never calls the Planner and is deterministically
    abstained there must therefore not be reported as a Planner failure.
    """

    calibrated: list[dict[str, Any]] = []
    reclassified_case_ids: list[str] = []
    for original in records:
        record = deepcopy(dict(original))
        planner_output = record.get("planner_output") or {}
        oracle = record.get("oracle_result") or {}
        pre_planner_abstention = (
            record.get("provider_status") == "NOT_CALLED"
            and not record.get("provider_calls")
            and str(record.get("expected_mode")) == "plan"
            and bool(planner_output.get("abstain"))
            and not bool(oracle.get("passed"))
        )
        if pre_planner_abstention:
            record["failure_category"] = "P-UNCERTAINTY"
            record["failure_subcategory"] = "FALSE_ABSTENTION"
            record["failure_categories"] = ["FALSE_ABSTENTION"]
            record["evaluable"] = False
            record["excluded_from_capability"] = True
            evidence = dict(record.get("evidence") or {})
            evidence["attribution"] = (
                "Deterministic pre-Planner abstention is scored by the "
                "Uncertainty policy benchmark, not Planner capability."
            )
            record["evidence"] = evidence
            reclassified_case_ids.append(str(record["case_id"]))
        calibrated.append(record)
    return calibrated, reclassified_case_ids


def _calibrate_raw_report(
    raw_report: Mapping[str, Any],
    raw_path: Path,
) -> dict[str, Any]:
    """Derive calibrated attribution without mutating the raw evidence."""

    import v24a_planner as base_harness

    records, reclassified_case_ids = _reclassify_uncertainty_records(
        list(raw_report.get("cases", []))
    )
    try:
        public_raw_path = raw_path.relative_to(ROOT).as_posix()
    except ValueError:
        public_raw_path = raw_path.name
    report = deepcopy(dict(raw_report))
    report["status"] = "CALIBRATED_FROM_RAW"
    report["source_raw_report"] = public_raw_path
    report["summary"] = base_harness._build_summary(records)
    attribution = _build_attribution(records)
    attribution["post_run_reclassification"] = True
    attribution["reclassified_case_ids"] = reclassified_case_ids
    report["attribution"] = attribution
    controls = dict(report.get("controls") or {})
    controls["provider_calls_during_calibration"] = 0
    controls["raw_report_overwritten"] = False
    controls["uncertainty_policy_excluded_from_capability"] = True
    report["controls"] = controls
    report["cases"] = records
    return report


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    os.chdir(ROOT)
    load_dotenv(args.dotenv if args.dotenv else ROOT / ".env")
    os.environ["TSAGENT_LLM_PROVIDER"] = args.provider
    os.environ.setdefault("TRANSFORMERS_NO_TF", "1")
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

    from evals.planner.dataset_v1_1 import (
        dataset_hash_v1_1,
        load_dataset_v1_1,
        planner_cases_v1_1,
        validate_dataset_v1_1,
    )

    payload = load_dataset_v1_1()
    valid, validation_errors = validate_dataset_v1_1(payload)
    if not valid:
        return {
            "harness_version": HARNESS_VERSION,
            "status": "INVALID_DATASET",
            "dataset_errors": list(validation_errors),
        }
    actual_hash = dataset_hash_v1_1(payload)
    if actual_hash != DATASET_HASH:
        return {
            "harness_version": HARNESS_VERSION,
            "status": "DATASET_HASH_MISMATCH",
            "dataset_hash": actual_hash,
            "expected_dataset_hash": DATASET_HASH,
        }

    cases = list(planner_cases_v1_1(payload))
    selected_ids = (
        {value.strip() for value in args.ids.split(",") if value.strip()}
        if args.ids
        else None
    )
    case_ids = {str(case["id"]) for case in cases}
    if selected_ids is not None:
        unknown_ids = selected_ids - case_ids
        if unknown_ids:
            raise ValueError(
                "--ids contains non-Planner-owned or unknown cases: "
                + ", ".join(sorted(unknown_ids))
            )
        cases = [case for case in cases if str(case["id"]) in selected_ids]

    import importlib

    from agent.llm import llm as production_llm

    planner_module = importlib.import_module("agent.planner.planner")
    import v24a_planner as base_harness

    original_planner_llm = planner_module.llm
    records: list[dict[str, Any]] = []
    try:
        for index, case in enumerate(cases, start=1):
            print(
                f"[{index}/{len(cases)}] {case['id']} "
                f"{str(case.get('name', ''))[:48]}",
                flush=True,
            )
            record = await base_harness._run_case(
                case=case,
                planner_module=planner_module,
                production_llm=production_llm,
                case_timeout=float(args.case_timeout),
                planning_context=_p12_planning_context(case),
            )
            record["ownership"] = "planner"
            records.append(record)
            print(
                f"  -> {record['provider_status']} "
                f"{'PASS' if record['passed'] else record.get('failure_category', 'FAIL')} "
                f"{record['latency_ms']:.0f}ms",
                flush=True,
            )
    finally:
        planner_module.llm = original_planner_llm

    summary = base_harness._build_summary(records)
    return {
        "harness_version": HARNESS_VERSION,
        "status": "COMPLETED",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_head": base_harness._git_head(),
        "working_tree_dirty": base_harness._git_dirty(),
        "dataset_path": "evals/planner/dataset_v1_1.py",
        "dataset_version": str(payload["version"]),
        "dataset_hash": actual_hash,
        "planner_case_count": len(cases),
        "planner_prompt_hash": base_harness._sha256_text(
            str(getattr(planner_module, "PLANNER_PROMPT", ""))
        ),
        "provider": base_harness._provider_public_config(args.provider),
        "provider_mode": args.provider,
        "controls": {
            "automatic_case_retry": False,
            "golden_plan_fallback": False,
            "provider_fallback": False,
            "case_attempts": 1,
            "case_timeout_seconds": float(args.case_timeout),
            "uncertainty_policy_in_planner_score": False,
            "routing_cases_excluded": sorted(ROUTING_CASE_IDS),
            "p12_context_projection": "Runtime PlannerContext projection enabled for P12 cases",
        },
        "case_selection": {
            "requested_ids": sorted(selected_ids) if selected_ids is not None else None,
            "submitted_ids": [str(case["id"]) for case in cases],
            "excluded_routing_ids": sorted(ROUTING_CASE_IDS),
        },
        "summary": summary,
        "attribution": _build_attribution(records),
        "cases": records,
    }


def _markdown(report: Mapping[str, Any]) -> str:
    summary = report.get("summary", {})
    attribution = report.get("attribution", {})
    lines = [
        "# v2.4A-2d Real Planner Re-baseline",
        "",
        "本报告只测 v1.1 calibrated view 中的 Planner-owned cases。Chat/Routing 与 Uncertainty 独立统计，不进入 Planner capability 分母。",
        "",
        f"- Harness: `{report.get('harness_version', '—')}`",
        f"- HEAD: `{report.get('git_head', '—')}`",
        f"- Dataset: `{report.get('dataset_version', '—')}`",
        f"- Dataset hash: `{report.get('dataset_hash', '—')}`",
        f"- Planner cases: **{report.get('planner_case_count', 0)}**",
        f"- Provider: `{(report.get('provider') or {}).get('provider', '—')}` / `{(report.get('provider') or {}).get('model', '—')}`",
        "- Automatic case retry: **false**",
        "- Provider fallback: **false**",
        f"- Source raw report: `{report.get('source_raw_report', 'this report')}`",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Cases submitted | {summary.get('case_count', 0)} |",
        f"| Evaluable cases | {summary.get('evaluable_case_count', 0)} |",
        f"| Capability pass | {summary.get('capability_pass_count', 0)}/{summary.get('evaluable_case_count', 0)} ({float(summary.get('capability_pass_rate', 0.0)):.1%}) |",
        f"| Raw case pass | {summary.get('capability_pass_count', 0)}/{summary.get('case_count', 0)} ({float(summary.get('raw_case_pass_rate', 0.0)):.1%}) |",
        f"| Schema validity | {float(summary.get('schema_validity', 0.0)):.1%} |",
        f"| Dependency validity | {float(summary.get('dependency_validity', 0.0)):.1%} |",
        f"| Executable plan rate | {float(summary.get('executable_plan_rate', 0.0)):.1%} |",
        f"| Missing task rate | {float(summary.get('missing_task_rate', 0.0)):.1%} |",
        f"| Overplanning rate | {float(summary.get('overplanning_rate', 0.0)):.1%} |",
        f"| Clarification accuracy | {float(summary.get('clarification_accuracy', 0.0)):.1%} |",
        f"| Unnecessary planning rate | {float(summary.get('unnecessary_planning_rate', 0.0)):.1%} |",
        f"| Average / P95 tasks | {summary.get('average_task_count', 0.0):.2f} / {summary.get('p95_task_count', '—')} |",
        f"| Average / P95 latency | {float(summary.get('average_latency_ms', 0.0)):.0f}ms / {summary.get('p95_latency_ms', '—')} |",
        "",
        "## Provider and format paths",
        "",
        f"- Provider path counts: `{json.dumps(summary.get('provider_path_counts', {}), ensure_ascii=False, sort_keys=True)}`",
        f"- Format path counts: `{json.dumps(summary.get('format_path_counts', {}), ensure_ascii=False, sort_keys=True)}`",
        f"- Cross-provider fallback cases: `{', '.join(summary.get('provider_fallback_case_ids', [])) or 'none'}`",
        f"- Structured-to-raw cases: `{', '.join(summary.get('format_fallback_case_ids', [])) or 'none'}`",
        "",
        "## Continuation context",
        "",
        "P12 cases receive a narrow `PlannerContext` projection derived from the durable-state fixture; the raw Dataset case and golden plan are not passed to production Planner.",
        f"- Projection cases: `{', '.join(record.get('case_id', '') for record in report.get('cases', []) if (record.get('planning_context') or {}).get('provided')) or 'none'}`",
        "",
        "## Failure separation",
        "",
        f"- Provider/API failures: **{summary.get('provider_error_count', 0)}**",
        f"- Contract/Oracle failures: **{summary.get('contract_or_oracle_failure_count', 0)}**",
        f"- Runtime/integration failures: **{summary.get('runtime_integration_failure_count', 0)}**",
        "",
        "| Attribution | Cases |",
        "| --- | ---: |",
    ]
    for category, count in sorted((attribution.get("category_counts") or {}).items()):
        lines.append(f"| {category} | {count} |")
    if not attribution.get("category_counts"):
        lines.append("| — | 0 |")
    lines.extend(
        [
            "",
            "## Failure clusters",
            "",
            "| Cluster | Count |",
            "| --- | ---: |",
        ]
    )
    clusters = summary.get("failure_clusters") or {}
    lines.extend(
        [f"| {key} | {value} |" for key, value in sorted(clusters.items())]
        or ["| — | 0 |"]
    )
    lines.extend(
        [
            "",
            "## Case results",
            "",
            "| Case | Mode | Provider status | Provider path | Format path | Context | Pass | Tasks | Latency | Failure |",
            "| --- | --- | --- | --- | --- | :---: | :---: | ---: | ---: | --- |",
        ]
    )
    for record in report.get("cases", []):
        oracle = record.get("oracle_result") or {}
        failure = record.get("failure_category") or "—"
        if record.get("failure_subcategory"):
            failure += f":{record['failure_subcategory']}"
        lines.append(
            f"| {record.get('case_id', '—')} | {record.get('expected_mode', '—')} | "
            f"{record.get('provider_status', '—')} | "
            f"{record.get('provider_path', '—')} | {record.get('format_path', '—')} | "
            f"{'yes' if (record.get('planning_context') or {}).get('provided') else 'no'} | "
            f"{'✅' if record.get('passed') else '❌'} | "
            f"{oracle.get('predicted_task_count', '—')} | "
            f"{float(record.get('latency_ms', 0.0)):.0f}ms | {failure} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation rules",
            "",
            "- Provider/API, contract/oracle, and runtime failures are reported separately from Planner capability.",
            "- Each selected case is submitted once; internal production fallback calls, if any, are recorded as evidence and are not altered.",
            "- Provider selection and response-format fallback are reported as separate evidence dimensions.",
            "- P12 continuation cases receive only the Runtime-projected completed/remaining task scope.",
            "- Deterministic pre-Planner abstentions are classified as P-UNCERTAINTY and excluded from the Planner capability denominator.",
            "- No golden plan, case-specific correction, or JSON repair is used in the Planner score.",
            "- A new capability score requires this real run; v1.0 results are not re-scored in place.",
        ]
    )
    return "\n".join(lines) + "\n"


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("deepseek", "ollama"), default="deepseek")
    parser.add_argument("--ids", default="", help="Comma-separated Planner-owned case IDs")
    parser.add_argument("--dotenv", type=Path, default=None)
    parser.add_argument("--case-timeout", type=float, default=180.0)
    parser.add_argument(
        "--raw-input",
        type=Path,
        default=None,
        help="Derive a calibrated attribution report from an existing raw JSON report",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown-output", type=Path, default=DEFAULT_MARKDOWN)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.raw_input is not None:
        raw_path = args.raw_input.resolve()
        raw_report = json.loads(raw_path.read_text(encoding="utf-8"))
        report = _calibrate_raw_report(raw_report, raw_path)
    else:
        report = asyncio.run(_run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.write_text(_markdown(report), encoding="utf-8")
    print(f"JSON report: {args.output}")
    print(f"Markdown report: {args.markdown_output}")
    if report.get("status") not in {"COMPLETED", "CALIBRATED_FROM_RAW"}:
        print("Re-baseline did not run: " + str(report.get("status")))
        return 2
    summary = report["summary"]
    print(
        "Re-baseline: "
        f"{summary['capability_pass_count']}/{summary['evaluable_case_count']} "
        f"evaluable capability cases; "
        f"Provider errors={summary['provider_error_count']}; "
        f"P-CAP={len(report['attribution']['p_cap_case_ids'])}"
    )
    if report.get("status") == "CALIBRATED_FROM_RAW":
        print(
            "Calibration only: raw report preserved; "
            f"P-UNCERTAINTY={len(report['attribution'].get('uncertainty_policy_case_ids', []))}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
