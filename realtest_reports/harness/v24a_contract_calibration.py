#!/usr/bin/env python3
"""Run the deterministic v2.4A-2c contract-calibration evidence.

This command validates the immutable Planner v1 baseline, the calibrated v1.1
ownership/alias view, the extracted routing Dataset, and the independent
uncertainty-policy baseline.  It never calls a Provider and never changes the
production Planner or Runtime.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_JSON = ROOT / "realtest_reports" / "results" / "v24a_contract_calibration.json"
DEFAULT_MARKDOWN = ROOT / "realtest_reports" / "results" / "v24a_contract_calibration.md"
HARNESS_VERSION = "v2.4A-2c-contract-calibration-v1"
V1_HASH = "7f5b28f608194a324f4244c860a8ed9101bcb7afa3b68e5129632ebfb0290291"


def _git_head() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def _run() -> dict[str, Any]:
    from evals.planner.dataset_v1_1 import (
        dataset_hash_v1_1,
        load_dataset_v1_1,
        planner_cases_v1_1,
        routing_cases_v1_1,
        validate_dataset_v1_1,
    )
    from evals.planner.oracle import dataset_hash, evaluate_golden, load_dataset
    from evals.routing.oracle import (
        dataset_hash as routing_hash,
        evaluate_route,
        golden_route,
        load_dataset as load_routing,
    )
    from evals.uncertainty.oracle import dataset_hash as uncertainty_hash, load_dataset as load_uncertainty

    from v24a_uncertainty import run_policy_baseline

    v1 = load_dataset()
    v1_reports, v1_metrics = evaluate_golden(v1)
    v1_validation = not [report for report in v1_reports if not report["passed"]]

    v1_1 = load_dataset_v1_1()
    v1_1_valid, v1_1_errors = validate_dataset_v1_1(v1_1)
    v1_1_reports, v1_1_metrics = evaluate_golden(v1_1)

    routing = load_routing()
    routing_reports = [evaluate_route(case, golden_route(case)) for case in routing["cases"]]

    uncertainty = load_uncertainty()
    uncertainty_report = run_policy_baseline(uncertainty)

    v1_hash = dataset_hash(v1)
    v1_1_hash = dataset_hash_v1_1(v1_1)
    route_hash = routing_hash(routing)
    uncertainty_hash_value = uncertainty_hash(uncertainty)
    planner_cases = planner_cases_v1_1(v1_1)
    routing_cases = routing_cases_v1_1(v1_1)

    return {
        "harness_version": HARNESS_VERSION,
        "status": "CALIBRATED_BASELINE",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_head": _git_head(),
        "provider_calls": 0,
        "controls": {
            "planner_changes": False,
            "runtime_changes": False,
            "provider_calls": 0,
            "v1_baseline_overwritten": False,
            "v1_1_rescore_is_new_provider_evidence": False,
        },
        "chat_ownership": {
            "decision": "CHAT_OUTSIDE_PLANNER",
            "planner_role": ["PLAN", "ABSTAIN"],
            "routing_dataset": "evals/routing/dataset.json",
            "routing_case_count": len(routing_cases),
            "planner_case_count": len(planner_cases),
            "routing_oracle_pass": all(item["passed"] for item in routing_reports),
        },
        "datasets": {
            "planner_v1": {
                "version": str(v1["version"]),
                "hash": v1_hash,
                "expected_hash": V1_HASH,
                "immutable_hash_unchanged": v1_hash == V1_HASH,
                "case_count": len(v1["cases"]),
                "golden_pass_count": sum(item["passed"] for item in v1_reports),
                "golden_metrics": v1_metrics.to_dict(),
            },
            "planner_v1_1": {
                "version": str(v1_1["version"]),
                "hash": v1_1_hash,
                "case_count": len(v1_1["cases"]),
                "planner_case_count": len(planner_cases),
                "routing_case_count": len(routing_cases),
                "alias_catalog": "evals/planner/target_aliases_v1_1.json",
                "golden_pass_count": sum(item["passed"] for item in v1_1_reports),
                "golden_metrics": v1_1_metrics.to_dict(),
                "validation": {"valid": v1_1_valid, "errors": list(v1_1_errors)},
            },
            "routing_v1": {
                "version": str(routing["version"]),
                "hash": route_hash,
                "case_count": len(routing["cases"]),
                "oracle_pass_count": sum(item["passed"] for item in routing_reports),
            },
            "uncertainty_v1": {
                "version": str(uncertainty["version"]),
                "hash": uncertainty_hash_value,
                "case_count": len(uncertainty["cases"]),
                "expected_abstain_count": sum(bool(case["expected_abstain"]) for case in uncertainty["cases"]),
                "expected_proceed_count": sum(not bool(case["expected_abstain"]) for case in uncertainty["cases"]),
            },
        },
        "checks": {
            "planner_v1_immutable": v1_hash == V1_HASH and v1_validation,
            "planner_v1_1_valid": v1_1_valid,
            "planner_v1_1_golden_self_check": all(item["passed"] for item in v1_1_reports),
            "routing_golden_self_check": all(item["passed"] for item in routing_reports),
            "uncertainty_dataset_valid": uncertainty_report.get("status") != "INVALID_DATASET",
        },
        "uncertainty_policy": {
            "status": uncertainty_report.get("summary", {}).get("policy_status", "UNKNOWN"),
            "summary": uncertainty_report.get("summary", {}),
            "baseline_report": "realtest_reports/results/v24a_uncertainty_baseline.json",
        },
        "planner_watchlist": ["PA013", "PA016"],
        "next_step": "v2.4A-2d real-provider re-baseline after calibration",
    }


def _markdown(report: Mapping[str, Any]) -> str:
    datasets = report.get("datasets", {})
    ownership = report.get("chat_ownership", {})
    checks = report.get("checks", {})
    uncertainty = report.get("uncertainty_policy", {})
    summary = uncertainty.get("summary", {})
    lines = [
        "# v2.4A-2c Contract Calibration",
        "",
        "> Calibration only: no Provider call, Planner modification, Runtime modification, or old-baseline overwrite.",
        "",
        f"- Status: **{report.get('status', 'UNKNOWN')}**",
        f"- HEAD: `{report.get('git_head', '—')}`",
        "",
        "## Chat ownership",
        "",
        f"- Decision: **{ownership.get('decision', '—')}**",
        f"- Planner-owned cases: **{ownership.get('planner_case_count', 0)}**",
        f"- Routing-owned cases: **{ownership.get('routing_case_count', 0)}**",
        f"- Routing oracle: **{'PASS' if ownership.get('routing_oracle_pass') else 'FAIL'}**",
        "",
        "## Versioned evidence",
        "",
        "| Dataset | Version | Cases | Hash | Check |",
        "| --- | --- | ---: | --- | --- |",
    ]
    for key, label in (
        ("planner_v1", "Planner v1 (immutable)"),
        ("planner_v1_1", "Planner v1.1 calibrated view"),
        ("routing_v1", "Routing v1"),
        ("uncertainty_v1", "Uncertainty v1"),
    ):
        item = datasets.get(key, {})
        if key == "planner_v1":
            check = "PASS" if item.get("immutable_hash_unchanged") else "FAIL"
        elif key == "planner_v1_1":
            check = "PASS" if item.get("validation", {}).get("valid") and item.get("golden_pass_count") == item.get("case_count") else "FAIL"
        elif key == "routing_v1":
            check = "PASS" if item.get("oracle_pass_count") == item.get("case_count") else "FAIL"
        else:
            check = "VALID" if checks.get("uncertainty_dataset_valid") else "FAIL"
        lines.append(
            f"| {label} | {item.get('version', '—')} | {item.get('case_count', 0)} | "
            f"`{item.get('hash', '—')}` | {check} |"
        )
    lines.extend(
        [
            "",
            "## Uncertainty policy baseline",
            "",
            f"- Status: **{uncertainty.get('status', 'UNKNOWN')}**",
            f"- Exact decisions: **{summary.get('pass_count', 0)}/{summary.get('case_count', 0)}**",
            f"- Abstain precision / recall: **{float(summary.get('abstain_precision', 0.0)):.1%} / {float(summary.get('abstain_recall', 0.0)):.1%}**",
            f"- False / missed abstention rate: **{float(summary.get('false_abstention_rate', 0.0)):.1%} / {float(summary.get('missed_abstention_rate', 0.0)):.1%}**",
            "",
            "This is a measured baseline for the current deterministic policy. A mismatch remains `P-UNCERTAINTY`; no policy fix is included in this calibration.",
            "",
            "## Watchlist",
            "",
            "- PA013 and PA016 remain Planner Capability Watchlist items.",
            "- v1 baseline remains bound to its original hash; v1.1 is not used to rewrite the old Provider score.",
            "- Next step: v2.4A-2d real-provider re-baseline with the calibrated contract.",
        ]
    )
    return "\n".join(lines) + "\n"


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown-output", type=Path, default=DEFAULT_MARKDOWN)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    report = _run()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.write_text(_markdown(report), encoding="utf-8")
    print(f"JSON report: {args.output}")
    print(f"Markdown report: {args.markdown_output}")
    print("v2.4A-2c calibration complete; no Provider calls made")
    return 0 if all(report["checks"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
