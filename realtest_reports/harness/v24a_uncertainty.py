#!/usr/bin/env python3
"""Deterministic baseline harness for the v2.4A uncertainty policy.

The harness evaluates the production ``detect_abstention`` rule against the
calibrated Dataset.  It does not call a Provider, alter the policy, or repair
the observed decision.  A failing case is evidence about the current policy,
not an invitation to change the Dataset or to treat the result as Planner
capability evidence.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_JSON = ROOT / "realtest_reports" / "results" / "v24a_uncertainty_baseline.json"
DEFAULT_MARKDOWN = ROOT / "realtest_reports" / "results" / "v24a_uncertainty_baseline.md"
HARNESS_VERSION = "v2.4A-2c-uncertainty-baseline-v1"


@dataclass(frozen=True)
class _Grounding:
    """Small contract-shaped grounding object accepted by the policy rule."""

    candidates: tuple[str, ...]


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


def _git_dirty() -> bool | None:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return bool(result.stdout.strip())


def _context_inputs(case: Mapping[str, Any]) -> tuple[_Grounding | None, str, dict[str, Any]]:
    context = str(case.get("context", "none"))
    if context == "valid_continuation":
        return (
            _Grounding(("valid continuation",)),
            "",
            {"type": "grounding_candidates", "candidates": ["valid continuation"]},
        )
    if context == "grounding_candidate":
        return (
            _Grounding(("grounding candidate",)),
            "",
            {"type": "grounding_candidates", "candidates": ["grounding candidate"]},
        )
    if context == "repo_context":
        return (
            None,
            "repository context for the referenced issue",
            {"type": "repository_context", "present": True},
        )
    return None, "", {"type": "none"}


def run_policy_baseline(payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Evaluate the current production abstention detector once per case."""

    from agent.planner.constraint_extractor import detect_abstention
    from evals.uncertainty.oracle import (
        aggregate_metrics,
        dataset_hash,
        evaluate_decision,
        load_dataset,
        validate_dataset,
    )

    value = dict(payload) if payload is not None else load_dataset()
    errors = validate_dataset(value)
    if errors:
        return {
            "harness_version": HARNESS_VERSION,
            "status": "INVALID_DATASET",
            "dataset_errors": list(errors),
        }

    records: list[dict[str, Any]] = []
    for case in value["cases"]:
        grounding, repo_context, context_evidence = _context_inputs(case)
        actual = bool(
            detect_abstention(
                str(case["input"]),
                grounding=grounding,
                repo_context=repo_context,
            )
        )
        oracle = evaluate_decision(case, actual)
        failure_category = None
        if not oracle["passed"]:
            failure_category = "P-UNCERTAINTY"
        records.append(
            {
                "case_id": str(case["id"]),
                "pair_id": str(case["pair_id"]),
                "input": str(case["input"]),
                "context": str(case["context"]),
                "provider_status": "NOT_APPLICABLE",
                "actual_abstain": actual,
                "oracle_result": oracle,
                "failure_category": failure_category,
                "failure_subcategory": oracle["outcome"] if failure_category else None,
                "evidence": {
                    "production_rule": "agent.planner.constraint_extractor.detect_abstention",
                    "context": context_evidence,
                },
            }
        )

    oracle_records = [record["oracle_result"] for record in records]
    metrics = aggregate_metrics(oracle_records)
    return {
        "harness_version": HARNESS_VERSION,
        "status": "DETERMINISTIC_POLICY_BASELINE",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_head": _git_head(),
        "working_tree_dirty": _git_dirty(),
        "dataset_version": str(value["version"]),
        "dataset_hash": dataset_hash(value),
        "provider": "none",
        "controls": {
            "provider_calls": 0,
            "automatic_case_retry": False,
            "decision_repair": False,
            "golden_decision_fallback": False,
            "cases_per_run": 1,
        },
        "summary": {
            **metrics,
            "policy_status": "PASS" if metrics["pass_count"] == metrics["case_count"] else "NEEDS_POLICY_WORK",
        },
        "cases": records,
    }


def _markdown(report: Mapping[str, Any]) -> str:
    summary = report.get("summary", {})
    lines = [
        "# v2.4A-2c Uncertainty Policy Baseline",
        "",
        "> This is a deterministic policy baseline, not a Provider or Planner acceptance run.",
        "",
        f"- Status: **{report.get('status', 'UNKNOWN')}**",
        f"- Dataset: `{report.get('dataset_version', '—')}`",
        f"- Dataset hash: `{report.get('dataset_hash', '—')}`",
        f"- Production rule: `agent.planner.constraint_extractor.detect_abstention`",
        f"- Provider calls: **0**",
        "",
        "## Metrics",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Cases | {summary.get('case_count', 0)} |",
        f"| Exact decisions | {summary.get('pass_count', 0)}/{summary.get('case_count', 0)} |",
        f"| True abstain | {summary.get('true_abstain', 0)} |",
        f"| False abstain | {summary.get('false_abstain', 0)} |",
        f"| Missed abstention | {summary.get('missed_abstention', 0)} |",
        f"| Abstain precision | {float(summary.get('abstain_precision', 0.0)):.1%} |",
        f"| Abstain recall | {float(summary.get('abstain_recall', 0.0)):.1%} |",
        f"| False abstention rate | {float(summary.get('false_abstention_rate', 0.0)):.1%} |",
        f"| Missed abstention rate | {float(summary.get('missed_abstention_rate', 0.0)):.1%} |",
        "",
        "## Case evidence",
        "",
        "| Case | Pair | Context | Expected | Actual | Outcome |",
        "| --- | --- | --- | ---: | ---: | --- |",
    ]
    for record in report.get("cases", []):
        oracle = record.get("oracle_result", {})
        lines.append(
            f"| {record.get('case_id', '—')} | {record.get('pair_id', '—')} | "
            f"{record.get('context', '—')} | {str(bool(oracle.get('expected_abstain')))} | "
            f"{str(bool(oracle.get('actual_abstain')))} | {oracle.get('outcome', '—')} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `P-UNCERTAINTY` records a current production-policy mismatch; this harness does not modify the policy.",
            "- Context signals are supplied through the detector's existing `grounding` / `repo_context` contract.",
            "- No Provider, Planner prompt, golden decision, or automatic retry is used.",
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
    report = run_policy_baseline()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.write_text(_markdown(report), encoding="utf-8")
    print(f"JSON report: {args.output}")
    print(f"Markdown report: {args.markdown_output}")
    print(
        "Uncertainty baseline: "
        f"{report.get('summary', {}).get('pass_count', 0)}/"
        f"{report.get('summary', {}).get('case_count', 0)} exact decisions; "
        f"status={report.get('summary', {}).get('policy_status', 'UNKNOWN')}"
    )
    return 0 if report.get("status") != "INVALID_DATASET" else 2


if __name__ == "__main__":
    raise SystemExit(main())
