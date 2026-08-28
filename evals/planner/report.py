"""CLI for the v2.4A Planner Capability Dataset."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from agent.planner.evaluator import PlannerMetrics, aggregate_metrics
from agent.planner.policies import DEFAULT_ACCEPTANCE_POLICY

from .oracle import dataset_hash, evaluate_golden, evaluate_plan, golden_plan, load_dataset


def _cases_from_results(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        for key in ("cases", "records", "results"):
            values = payload.get(key)
            if isinstance(values, list):
                return [item for item in values if isinstance(item, Mapping)]
    raise ValueError("results must be a list or an object containing cases/records/results")


def acceptance_gate(metrics: PlannerMetrics) -> dict[str, bool]:
    policy = DEFAULT_ACCEPTANCE_POLICY
    return {
        "case_count": metrics.case_count >= policy.minimum_case_count,
        "schema_validity": metrics.schema_validity >= policy.minimum_schema_validity,
        "dependency_validity": metrics.dependency_validity >= policy.minimum_dependency_validity,
        "plan_validity": metrics.plan_validity >= policy.minimum_plan_validity,
        "critical_missing_task_rate": metrics.critical_missing_task_rate <= policy.maximum_critical_missing_task_rate,
        "overplanning_rate": metrics.overplanning_rate <= policy.maximum_overplanning_rate,
    }


def build_report(
    cases: Sequence[Mapping[str, Any]],
    reports: Sequence[Mapping[str, Any]],
    *,
    source: str,
) -> dict[str, Any]:
    metrics = aggregate_metrics(reports)
    gate = acceptance_gate(metrics)
    by_id = {str(report.get("case_id", "")): report for report in reports}
    failures = [
        {
            "case_id": str(case.get("id", "")),
            "errors": list(by_id.get(str(case.get("id", "")), {}).get("errors", [])),
        }
        for case in cases
        if not bool(by_id.get(str(case.get("id", "")), {}).get("passed", False))
    ]
    return {
        "version": "v2.4A",
        "dataset_version": "v2.4A-planner-v1",
        "dataset_hash": dataset_hash({"version": "v2.4A-planner-v1", "cases": list(cases)}),
        "case_count": len(cases),
        "source": source,
        "metrics": metrics.to_dict(),
        "acceptance_gate": gate,
        "status": "PASS" if all(gate.values()) and not failures else "FAIL",
        "failures": failures,
        "case_reports": list(reports),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, default=None)
    parser.add_argument("--results", type=Path, default=None)
    parser.add_argument("--self-check", action="store_true", help="evaluate generated golden plans")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args(argv)

    payload = load_dataset(args.dataset) if args.dataset is not None else load_dataset()
    cases = list(payload["cases"])
    if args.self_check or args.results is None:
        reports, _ = evaluate_golden(payload)
        source = "golden_self_check"
    else:
        result_payload = json.loads(args.results.read_text(encoding="utf-8"))
        records = _cases_from_results(result_payload)
        by_id = {str(record.get("case_id", "")): record for record in records}
        reports = [
            evaluate_plan(case, by_id[str(case["id"])])
            if str(case["id"]) in by_id
            else {
                "case_id": str(case["id"]),
                "passed": False,
                "errors": ["missing planner result"],
                "schema_validity": 0.0,
                "dependency_validity": 0.0,
                "plan_validity": 0.0,
                "dependency_accuracy": 0.0,
                "task_granularity": 0.0,
                "unnecessary_task_rate": 0.0,
                "missing_task_rate": 1.0 if case.get("goal_units") else 0.0,
                "executable_plan": 0.0,
                "overplanned": 0.0,
                "critical_missing": 1.0 if case.get("goal_units") else 0.0,
                "goal_unit_count": len(case.get("goal_units", []) or []),
                "predicted_task_count": 0,
                "unnecessary_task_count": 0,
                "missing_task_count": len(case.get("goal_units", []) or []),
            }
            for case in cases
        ]
        source = str(args.results)

    report = build_report(cases, reports, source=source)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
