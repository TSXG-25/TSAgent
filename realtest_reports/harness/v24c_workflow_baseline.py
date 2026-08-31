#!/usr/bin/env python3
"""Real DeepSeek baseline for the production WorkflowDecisionSelector.

The harness imports the production Selector. It does not copy its prompt,
repair JSON, execute or resume a Workflow, mutate Runtime state, retry a case,
or switch Provider.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence

from dotenv import load_dotenv


SOURCE_ROOT = Path(__file__).resolve().parents[2]
ROOT = Path(os.environ.get("TSAGENT_SNAPSHOT", SOURCE_ROOT)).resolve()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env")

from agent.llm import LLMRouter
from agent.workflow_selector import (
    ActiveWorkflowProjection,
    WORKFLOW_SELECTION_PROMPT,
    WorkflowContextProjection,
    WorkflowDecisionSelector,
    WorkflowDefinitionProjection,
    WorkflowSelectionError,
    workflow_projection_hash,
)
from evals.workflow_capability.oracle import (
    dataset_hash,
    evaluate_decision,
    load_dataset,
)
from realtest_reports.harness.v24a_planner import (
    _CallRecorder,
    _json_safe,
    _stable_error,
)


HARNESS_VERSION = "v2.4C-2b-real-workflow-baseline-v1"
EXPECTED_SELECTOR_COMMIT = "bc15e33a"
EXPECTED_DATASET_HASH = (
    "43338803cbe9192c19a2957887a8013c17058a6dbea9e7bb6cb66c06d60fbd69"
)
DEFAULT_JSON = SOURCE_ROOT / "realtest_reports" / "results" / "v24c_workflow_baseline.json"
DEFAULT_MARKDOWN = SOURCE_ROOT / "realtest_reports" / "results" / "v24c_workflow_baseline.md"
CAPABILITY_ERRORS = frozenset({
    "UNAVAILABLE_WORKFLOW",
    "BINDINGS_INVALID",
    "REQUIRED_ARTIFACT_UNAVAILABLE",
    "REQUIRED_CAPABILITY_UNAVAILABLE",
    "UNSAFE_REUSE",
    "SCHEMA_INVALID",
})


class _RecordingProvider:
    def __init__(self, inner: Any, recorder: _CallRecorder, provider: str) -> None:
        self._inner = inner
        self._recorder = recorder
        self._provider = provider

    def with_structured_output(self, *args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            runnable = self._inner.with_structured_output(*args, **kwargs)
        except BaseException as error:
            self._recorder.record(
                provider=self._provider,
                call_kind="structured_bind",
                started=started,
                error=error,
            )
            raise
        self._recorder.record(
            provider=self._provider,
            call_kind="structured_bind",
            started=started,
        )
        return _RecordingRunnable(runnable, self._recorder, self._provider)

    async def ainvoke(self, messages: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            result = await self._inner.ainvoke(messages, **kwargs)
        except BaseException as error:
            self._recorder.record(
                provider=self._provider,
                call_kind="raw_ainvoke",
                started=started,
                error=error,
            )
            raise
        self._recorder.record(
            provider=self._provider,
            call_kind="raw_ainvoke",
            started=started,
            result=result,
        )
        return result


class _RecordingRunnable:
    def __init__(self, inner: Any, recorder: _CallRecorder, provider: str) -> None:
        self._inner = inner
        self._recorder = recorder
        self._provider = provider

    async def ainvoke(self, messages: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            result = await self._inner.ainvoke(messages, **kwargs)
        except BaseException as error:
            self._recorder.record(
                provider=self._provider,
                call_kind="structured_ainvoke",
                started=started,
                error=error,
            )
            raise
        self._recorder.record(
            provider=self._provider,
            call_kind="structured_ainvoke",
            started=started,
            result=result,
        )
        return result


def _git_head() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _git_dirty() -> bool | None:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return bool(result.stdout.strip())


def _selector_matches(commit: str) -> bool:
    result = subprocess.run(
        [
            "git", "diff", "--quiet", commit, "--",
            "agent/workflow_selector.py", "agent/workflow_decision.py",
        ],
        cwd=ROOT,
        check=False,
    )
    return result.returncode == 0


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(len(ordered) * percentile))
    return ordered[rank - 1]


def _format_path(recorder: _CallRecorder) -> str:
    kinds = {str(call.get("call_kind", "")) for call in recorder.calls}
    has_structured = bool(kinds & {"structured_bind", "structured_ainvoke"})
    has_raw = "raw_ainvoke" in kinds
    if has_structured and has_raw:
        return "STRUCTURED_TO_RAW_FALLBACK"
    if has_structured:
        return "STRUCTURED_ONLY"
    if has_raw:
        return "RAW_ONLY"
    return "NOT_CALLED"


def _provider_status(
    recorder: _CallRecorder,
    error: BaseException | None,
) -> str:
    if not recorder.calls:
        return "NOT_CALLED"
    if isinstance(error, WorkflowSelectionError) and error.code == "PROVIDER_ERROR":
        return "PROVIDER_ERROR"
    if not recorder.successes:
        return "PROVIDER_ERROR"
    if _format_path(recorder) == "STRUCTURED_TO_RAW_FALLBACK":
        return "SUCCESS_WITH_FORMAT_FALLBACK"
    return "SUCCESS"


def _projections(
    dataset: Mapping[str, Any],
    case: Mapping[str, Any],
) -> tuple[WorkflowContextProjection, tuple[WorkflowDefinitionProjection, ...]]:
    state = case["state"]
    active_value = state.get("active_workflow")
    active = None
    if active_value is not None:
        active = ActiveWorkflowProjection(
            workflow_id=active_value["workflow_id"],
            status=active_value["status"],
            reuse_allowed=active_value["reuse_allowed"],
        )
    context = WorkflowContextProjection(
        artifacts=dict(state["artifacts"]),
        capabilities=tuple(state["capabilities"]),
        facts=dict(state["facts"]),
        active_workflow=active,
    )
    catalog = dataset["workflow_catalog"]
    available = tuple(
        WorkflowDefinitionProjection(id=workflow_id, **catalog[workflow_id])
        for workflow_id in state["available_workflows"]
    )
    return context, available


def _failure_subcategory(
    case: Mapping[str, Any],
    oracle: Mapping[str, Any],
    selection_error: WorkflowSelectionError | None,
) -> str:
    if selection_error is not None and selection_error.code in CAPABILITY_ERRORS:
        return selection_error.code
    expected = case["expected"]
    actual = oracle["normalized_decision"]
    expected_kind = str(expected["kind"])
    actual_kind = str(actual["kind"])
    if not bool(oracle["schema_validity"]):
        return "SCHEMA_INVALID"
    if (
        expected_kind in {"decline", "ask"}
        and actual_kind in {"instantiate", "reuse"}
    ):
        return "FALSE_WORKFLOW_SELECTION"
    if expected_kind == "ask" and actual_kind != "ask":
        return "MISSED_ASK"
    if (
        expected_kind in {"instantiate", "reuse"}
        and actual_kind in {"decline", "ask"}
    ):
        return "MISSED_WORKFLOW"
    if not bool(oracle["kind_accuracy"]):
        return "WRONG_KIND"
    if not bool(oracle["workflow_accuracy"]):
        return "WRONG_WORKFLOW"
    if not bool(oracle["binding_accuracy"]):
        return "ARGUMENT_BINDING"
    if not bool(oracle["safe_decision"]):
        return "UNSAFE_REUSE" if actual_kind == "reuse" else "UNSAFE_SELECTION"
    return "WRONG_KIND"


async def _run_case(
    dataset: Mapping[str, Any],
    case: Mapping[str, Any],
    *,
    provider: Any,
    provider_name: str,
    case_timeout: float,
) -> dict[str, Any]:
    recorder = _CallRecorder()
    selector = WorkflowDecisionSelector(
        provider=_RecordingProvider(provider, recorder, provider_name),
        provider_name=provider_name,
        supports_structured_output=True,
    )
    started = time.perf_counter()
    selection = None
    selection_error: BaseException | None = None
    context = None
    available = None
    try:
        context, available = _projections(dataset, case)
        selection = await asyncio.wait_for(
            selector.select_with_evidence(case["goal"], context, available),
            timeout=case_timeout,
        )
    except BaseException as error:
        selection_error = error

    normalized = None
    raw_output = None
    format_path = _format_path(recorder)
    if selection is not None:
        normalized = selection.decision.to_dict()
        raw_output = _json_safe(selection.evidence.raw_output)
        format_path = selection.evidence.format_path
    elif isinstance(selection_error, WorkflowSelectionError):
        if selection_error.candidate is not None:
            normalized = selection_error.candidate.to_dict()
        raw_output = _json_safe(selection_error.raw_output)
        format_path = selection_error.format_path or format_path

    record: dict[str, Any] = {
        "case_id": str(case["id"]),
        "family": str(case["family"]),
        "goal": str(case["goal"]),
        "context_projection": (
            _json_safe(context.model_dump(mode="json")) if context else None
        ),
        "available_workflows": (
            [_json_safe(item.model_dump(mode="json")) for item in available]
            if available is not None else None
        ),
        "case_attempts": 1,
        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        "provider_status": _provider_status(recorder, selection_error),
        "provider_path": "SINGLE_PROVIDER" if recorder.calls else "NOT_CALLED",
        "format_path": format_path,
        "provider_calls": recorder.calls,
        "token_usage": recorder.token_totals(),
        "raw_provider_output": raw_output,
        "normalized_workflow_decision": normalized,
        "oracle_result": None,
        "evaluable": False,
        "passed": False,
        "failure_category": None,
        "failure_subcategory": None,
        "evidence": {},
    }

    if isinstance(selection_error, (asyncio.TimeoutError, TimeoutError)):
        record["provider_status"] = "PROVIDER_ERROR"
        record["failure_category"] = "P-PROV"
        record["failure_subcategory"] = "TIMEOUT"
        record["evidence"] = {"error": _stable_error(selection_error)}
        return record
    if isinstance(selection_error, WorkflowSelectionError):
        if selection_error.code == "PROVIDER_ERROR":
            record["failure_category"] = "P-PROV"
            record["failure_subcategory"] = "PROVIDER_ERROR"
            record["evidence"] = {"error": _stable_error(selection_error)}
            return record
    elif selection_error is not None:
        record["failure_category"] = "P-INT"
        record["failure_subcategory"] = type(selection_error).__name__
        record["evidence"] = {"error": _stable_error(selection_error)}
        return record

    try:
        oracle = evaluate_decision(
            dataset,
            case,
            normalized if normalized is not None else raw_output,
        )
    except BaseException as error:
        record["failure_category"] = "P-ORACLE"
        record["failure_subcategory"] = type(error).__name__
        record["evidence"] = {"error": _stable_error(error)}
        return record

    record["oracle_result"] = _json_safe(oracle)
    record["evaluable"] = True
    record["passed"] = bool(oracle["passed"]) and selection_error is None
    if not record["passed"]:
        capability_error = (
            selection_error
            if isinstance(selection_error, WorkflowSelectionError)
            else None
        )
        record["failure_category"] = "P-CAP"
        record["failure_subcategory"] = _failure_subcategory(
            case,
            oracle,
            capability_error,
        )
        record["evidence"] = {
            "oracle_errors": list(oracle.get("errors", []) or []),
            "selection_error": (
                _stable_error(selection_error) if selection_error else None
            ),
        }
    return record


def _summary(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    evaluated = [record for record in records if bool(record.get("evaluable"))]
    oracle_results = [record["oracle_result"] for record in evaluated]
    total = len(oracle_results)

    def rate(field: str) -> float:
        return sum(bool(item[field]) for item in oracle_results) / total if total else 0.0

    latencies = [float(record["latency_ms"]) for record in records]
    outer = Counter(
        str(record["failure_category"])
        for record in records if record.get("failure_category")
    )
    inner = Counter(
        str(record["failure_subcategory"])
        for record in records if record.get("failure_subcategory")
    )
    token_totals: Counter[str] = Counter()
    for record in records:
        for key, value in (record.get("token_usage") or {}).items():
            token_totals[str(key)] += int(value)
    return {
        "case_count": len(records),
        "evaluable_count": len(evaluated),
        "pass_count": sum(bool(record["passed"]) for record in evaluated),
        "capability_rate": (
            sum(bool(record["passed"]) for record in evaluated) / len(evaluated)
            if evaluated else 0.0
        ),
        "funnel": {
            "schema_validity": rate("schema_validity"),
            "decision_kind_accuracy": rate("kind_accuracy"),
            "workflow_accuracy": rate("workflow_accuracy"),
            "binding_accuracy": rate("binding_accuracy"),
            "safe_decision_rate": rate("safe_decision"),
        },
        "risk": {
            "false_workflow_selection": sum(
                int(item["false_workflow_selection"]) for item in oracle_results
            ),
            "unsafe_reuse": sum(int(item["unsafe_reuse"]) for item in oracle_results),
            "missed_workflow": sum(
                int(item["missed_workflow"]) for item in oracle_results
            ),
        },
        "failure_categories": dict(outer),
        "capability_failures": dict(inner),
        "format_paths": dict(Counter(str(record["format_path"]) for record in records)),
        "provider_error_count": outer.get("P-PROV", 0),
        "contract_error_count": outer.get("P-CON", 0),
        "oracle_error_count": outer.get("P-ORACLE", 0),
        "integration_error_count": outer.get("P-INT", 0),
        "latency_ms": {
            "mean": round(statistics.fmean(latencies), 3) if latencies else None,
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "max": max(latencies) if latencies else None,
        },
        "token_usage": dict(token_totals),
    }


def _markdown(report: Mapping[str, Any]) -> str:
    summary = report["summary"]
    funnel = summary["funnel"]
    lines = [
        "# v2.4C-2b Real Provider Workflow Baseline",
        "",
        f"- Evaluated HEAD: `{report['evaluated_head']}`",
        f"- Production Selector baseline: `{report['selector']['baseline_commit']}`",
        f"- Dataset hash: `{report['dataset']['hash']}`",
        f"- Provider/model: `{report['provider']['provider']}` / `{report['provider']['model']}`",
        f"- Working tree dirty: `{report['working_tree_dirty']}`",
        "",
        "## Result",
        "",
        f"- Capability: `{summary['pass_count']}/{summary['evaluable_count']}` "
        f"(`{summary['capability_rate']:.1%}`)",
        f"- Provider errors: `{summary['provider_error_count']}`",
        f"- Contract / Oracle / Integration: `{summary['contract_error_count']} / "
        f"{summary['oracle_error_count']} / {summary['integration_error_count']}`",
        "",
        "## Funnel",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Schema validity | {funnel['schema_validity']:.1%} |",
        f"| Decision-kind accuracy | {funnel['decision_kind_accuracy']:.1%} |",
        f"| Workflow accuracy | {funnel['workflow_accuracy']:.1%} |",
        f"| Binding accuracy | {funnel['binding_accuracy']:.1%} |",
        f"| Safe-decision rate | {funnel['safe_decision_rate']:.1%} |",
        "",
        "## Cases",
        "",
        "| Case | Result | Provider | Format | Attribution |",
        "| --- | --- | --- | --- | --- |",
    ]
    for record in report["records"]:
        attribution = "PASS" if record["passed"] else (
            f"{record.get('failure_category')}:{record.get('failure_subcategory')}"
        )
        lines.append(
            f"| {record['case_id']} | {'PASS' if record['passed'] else 'FAIL'} | "
            f"{record['provider_status']} | {record['format_path']} | {attribution} |"
        )
    lines.extend([
        "",
        "## Hard controls",
        "",
        "```text",
        "automatic_retry = false",
        "provider_fallback = false",
        "golden_repair = false",
        "json_repair = false",
        "workflow_execution = false",
        "runtime_mutation = false",
        "```",
        "",
    ])
    return "\n".join(lines)


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    dataset = load_dataset()
    actual_hash = dataset_hash(dataset)
    head = _git_head()
    if head is None:
        raise RuntimeError("evaluated HEAD is unavailable")
    if not _selector_matches(args.expected_selector_commit):
        raise RuntimeError("production Workflow Selector drifted from the frozen baseline")
    if actual_hash != EXPECTED_DATASET_HASH:
        raise RuntimeError(
            f"dataset hash drift: expected {EXPECTED_DATASET_HASH}, got {actual_hash}"
        )

    router = LLMRouter(provider_mode="deepseek")
    provider, provider_name = router._get_active_provider()
    records = []
    for index, case in enumerate(dataset["cases"], start=1):
        print(f"[{index:02d}/{len(dataset['cases'])}] {case['id']}", flush=True)
        record = await _run_case(
            dataset,
            case,
            provider=provider,
            provider_name=provider_name,
            case_timeout=args.case_timeout,
        )
        records.append(record)
        result = "PASS" if record["passed"] else (
            f"{record.get('failure_category')}:{record.get('failure_subcategory')}"
        )
        print(f"  {result} {record['latency_ms']:.0f}ms {record['format_path']}", flush=True)

    return {
        "harness_version": HARNESS_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evaluated_head": head,
        "working_tree_dirty": _git_dirty(),
        "dataset": {
            "version": dataset["version"],
            "hash": actual_hash,
            "case_count": len(dataset["cases"]),
        },
        "selector": {
            "symbol": "agent.workflow_selector.WorkflowDecisionSelector",
            "baseline_commit": args.expected_selector_commit,
            "projection_hash": workflow_projection_hash(),
            "prompt_hash": _sha256(WORKFLOW_SELECTION_PROMPT),
        },
        "provider": {
            "provider": "deepseek",
            "model": os.environ.get("MODEL_NAME", "deepseek-v4-flash"),
            "base_url": "https://api.deepseek.com/v1",
            "fallback": False,
        },
        "controls": {
            "automatic_retry": False,
            "provider_fallback": False,
            "golden_repair": False,
            "json_repair": False,
            "workflow_execution": False,
            "runtime_mutation": False,
            "case_attempts": 1,
        },
        "summary": _summary(records),
        "records": records,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-selector-commit", default=EXPECTED_SELECTOR_COMMIT)
    parser.add_argument("--case-timeout", type=float, default=100.0)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    args = parser.parse_args(argv)
    report = asyncio.run(_run(args))
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    args.markdown.write_text(_markdown(report), encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
