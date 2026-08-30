#!/usr/bin/env python3
"""Real DeepSeek baseline for the production v2.4B NextActionSelector.

The harness imports the production Selector from the evaluated snapshot.  It
does not copy its prompt, repair JSON, execute tools, enter the Runtime loop,
retry a case, or switch Provider.  A structured-to-raw transition remains a
same-Provider format path and is reported separately.
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
from agent.next_action import ActionKind
from agent.next_action_selector import (
    ActionObservation,
    ExecutionStateProjection,
    NEXT_ACTION_PROMPT,
    NextActionSelectionError,
    NextActionSelector,
)
from evals.tool_selection.oracle import (
    aggregate_metrics,
    dataset_hash,
    evaluate_action,
    load_dataset,
)
from realtest_reports.harness.v24a_planner import (
    _CallRecorder,
    _json_safe,
    _stable_error,
)


HARNESS_VERSION = "v2.4B-2b-real-tool-selection-v1"
EXPECTED_HEAD_PREFIX = "fc1835a8"
EXPECTED_DATASET_HASH = (
    "bc0baa5afcf68ba68a787387edd7297a4c22bea6334e1e0afd06c61136952409"
)
DEFAULT_JSON = SOURCE_ROOT / "realtest_reports" / "results" / "v24b_tool_selection_baseline.json"
DEFAULT_MARKDOWN = SOURCE_ROOT / "realtest_reports" / "results" / "v24b_tool_selection_baseline.md"
CAPABILITY_FAILURES = frozenset({
    "WRONG_KIND",
    "WRONG_TOOL",
    "ARGUMENT_BINDING",
    "WRONG_TASK",
    "DEPENDENCY_VIOLATION",
    "UNAVAILABLE_TOOL",
    "DUPLICATE_EFFECT",
    "PREMATURE_ANSWER",
    "MISSED_ASK",
    "UNSAFE_RETRY",
    "SCHEMA_INVALID",
})


class _RecordingProvider:
    """Record direct structured/raw calls to one concrete Provider."""

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
        return _RecordingRunnable(
            runnable,
            self._recorder,
            self._provider,
            "structured_ainvoke",
        )

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
    def __init__(
        self,
        inner: Any,
        recorder: _CallRecorder,
        provider: str,
        call_kind: str,
    ) -> None:
        self._inner = inner
        self._recorder = recorder
        self._provider = provider
        self._call_kind = call_kind

    async def ainvoke(self, messages: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            result = await self._inner.ainvoke(messages, **kwargs)
        except BaseException as error:
            self._recorder.record(
                provider=self._provider,
                call_kind=self._call_kind,
                started=started,
                error=error,
            )
            raise
        self._recorder.record(
            provider=self._provider,
            call_kind=self._call_kind,
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


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(len(ordered) * percentile))
    return ordered[rank - 1]


def _current_task(state: ExecutionStateProjection):
    return next(
        (task for task in state.tasks if task.id == state.current_task_id),
        None,
    )


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
    if isinstance(error, NextActionSelectionError) and error.code == "PROVIDER_ERROR":
        return "PROVIDER_ERROR"
    if not recorder.successes:
        return "PROVIDER_ERROR"
    if _format_path(recorder) == "STRUCTURED_TO_RAW_FALLBACK":
        return "SUCCESS_WITH_FORMAT_FALLBACK"
    return "SUCCESS"


def _failure_subcategory(
    case: Mapping[str, Any],
    oracle: Mapping[str, Any],
    selection_error: NextActionSelectionError | None,
) -> str:
    if selection_error is not None and selection_error.code in CAPABILITY_FAILURES:
        return selection_error.code
    errors = [str(error) for error in oracle.get("errors", []) or []]
    expected_kind = str(oracle.get("expected_kind", ""))
    actual_kind = str(oracle.get("actual_kind", ""))
    if not bool(oracle.get("schema_validity")):
        return "SCHEMA_INVALID"
    if actual_kind == ActionKind.ANSWER.value and not bool(case["state"]["answer_ready"]):
        return "PREMATURE_ANSWER"
    if expected_kind == ActionKind.ASK.value and actual_kind != ActionKind.ASK.value:
        return "MISSED_ASK"
    if float(oracle.get("kind_accuracy", 0.0)) < 1.0:
        return "WRONG_KIND"
    if any("not available" in error for error in errors):
        return "UNAVAILABLE_TOOL"
    if any("verified effect" in error for error in errors):
        return "DUPLICATE_EFFECT"
    if any("dependency" in error for error in errors):
        return "DEPENDENCY_VIOLATION"
    if float(oracle.get("tool_selection_accuracy", 0.0)) < 1.0:
        return "WRONG_TOOL"
    if float(oracle.get("argument_accuracy", 0.0)) < 1.0:
        return "ARGUMENT_BINDING"
    if float(oracle.get("task_targeting_accuracy", 0.0)) < 1.0:
        return "WRONG_TASK"
    return "WRONG_KIND"


async def _run_case(
    case: Mapping[str, Any],
    *,
    provider: Any,
    provider_name: str,
    case_timeout: float,
) -> dict[str, Any]:
    recorder = _CallRecorder()
    recording_provider = _RecordingProvider(provider, recorder, provider_name)
    selector = NextActionSelector(
        provider=recording_provider,
        provider_name=provider_name,
        supports_structured_output=True,
    )
    started = time.perf_counter()
    selection = None
    selection_error: BaseException | None = None
    state = None
    observation = None
    try:
        state = ExecutionStateProjection.model_validate(case["state"])
        observation = ActionObservation.model_validate(case["observation"])
        selection = await asyncio.wait_for(
            selector.select_with_evidence(
                _current_task(state),
                state,
                observation,
            ),
            timeout=case_timeout,
        )
    except BaseException as error:
        selection_error = error

    normalized_action = None
    raw_output = None
    format_path = _format_path(recorder)
    if selection is not None:
        normalized_action = selection.action.to_dict()
        raw_output = _json_safe(selection.evidence.raw_output)
        format_path = selection.evidence.format_path
    elif isinstance(selection_error, NextActionSelectionError):
        if selection_error.candidate is not None:
            normalized_action = selection_error.candidate.to_dict()
        raw_output = _json_safe(selection_error.raw_output)
        format_path = selection_error.format_path or format_path

    record: dict[str, Any] = {
        "case_id": str(case["id"]),
        "family": str(case["family"]),
        "input": str(case["input"]),
        "task_projection": (
            _json_safe(_current_task(state).model_dump(mode="json"))
            if state is not None and _current_task(state) is not None
            else None
        ),
        "state_projection": (
            _json_safe(state.model_dump(mode="json")) if state is not None else None
        ),
        "observation": (
            _json_safe(observation.to_dict()) if observation is not None else None
        ),
        "case_attempts": 1,
        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        "provider_status": _provider_status(recorder, selection_error),
        "provider_path": "SINGLE_PROVIDER" if recorder.calls else "NOT_CALLED",
        "format_path": format_path,
        "provider_calls": recorder.calls,
        "token_usage": recorder.token_totals(),
        "raw_provider_output": raw_output,
        "normalized_next_action": normalized_action,
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
    if isinstance(selection_error, NextActionSelectionError):
        if selection_error.code == "PROVIDER_ERROR":
            record["failure_category"] = "P-PROV"
            record["failure_subcategory"] = "PROVIDER_ERROR"
            record["evidence"] = {"error": _stable_error(selection_error)}
            return record
        if selection_error.code != "SCHEMA_INVALID" and normalized_action is None:
            record["failure_category"] = "P-INT"
            record["failure_subcategory"] = selection_error.code
            record["evidence"] = {"error": _stable_error(selection_error)}
            return record
    elif selection_error is not None:
        category = "P-CON" if state is None or observation is None else "P-INT"
        record["failure_category"] = category
        record["failure_subcategory"] = type(selection_error).__name__
        record["evidence"] = {"error": _stable_error(selection_error)}
        return record

    try:
        oracle_input: Any = normalized_action if normalized_action is not None else raw_output
        oracle = evaluate_action(case, oracle_input)
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
            if isinstance(selection_error, NextActionSelectionError)
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
                _stable_error(selection_error) if selection_error is not None else None
            ),
        }
    return record


def _summary(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    evaluated = [record for record in records if bool(record.get("evaluable"))]
    reports = [
        record["oracle_result"]
        for record in evaluated
        if isinstance(record.get("oracle_result"), Mapping)
    ]
    metrics = aggregate_metrics(reports)
    latencies = [float(record["latency_ms"]) for record in records]
    token_totals: Counter[str] = Counter()
    for record in records:
        for key, value in (record.get("token_usage") or {}).items():
            token_totals[str(key)] += int(value)
    outer = Counter(
        str(record["failure_category"])
        for record in records
        if record.get("failure_category")
    )
    inner = Counter(
        str(record["failure_subcategory"])
        for record in records
        if record.get("failure_subcategory")
    )
    format_paths = Counter(str(record["format_path"]) for record in records)
    return {
        "case_count": len(records),
        "evaluable_count": len(evaluated),
        "pass_count": sum(bool(record.get("passed")) for record in evaluated),
        "capability_rate": (
            sum(bool(record.get("passed")) for record in evaluated) / len(evaluated)
            if evaluated else 0.0
        ),
        "funnel": metrics,
        "risk": {
            "duplicate_verified_effect_count": metrics["duplicate_effect_count"],
            "premature_answer_count": metrics["premature_finish_count"],
            "dependency_violation_count": inner.get("DEPENDENCY_VIOLATION", 0),
        },
        "failure_categories": dict(outer),
        "capability_failures": dict(inner),
        "format_paths": dict(format_paths),
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
        "# v2.4B-2b Real Provider Baseline",
        "",
        f"- Evaluated HEAD: `{report['evaluated_head']}`",
        f"- Dataset: `{report['dataset']['version']}`",
        f"- Dataset hash: `{report['dataset']['hash']}`",
        f"- Provider/model: `{report['provider']['provider']}` / `{report['provider']['model']}`",
        f"- Working tree dirty: `{report['working_tree_dirty']}`",
        "",
        "## Result",
        "",
        f"- Capability: `{summary['pass_count']}/{summary['evaluable_count']}` "
        f"(`{summary['capability_rate']:.1%}`)",
        f"- Provider errors: `{summary['provider_error_count']}`",
        f"- Contract / Oracle / Integration: "
        f"`{summary['contract_error_count']} / {summary['oracle_error_count']} / "
        f"{summary['integration_error_count']}`",
        "",
        "## Funnel",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Schema validity | {funnel['schema_validity']:.1%} |",
        f"| Action-kind accuracy | {funnel['kind_accuracy']:.1%} |",
        f"| Tool-selection accuracy | {funnel['tool_selection_accuracy']:.1%} |",
        f"| Argument-binding accuracy | {funnel['argument_accuracy']:.1%} |",
        f"| Task-targeting accuracy | {funnel['task_targeting_accuracy']:.1%} |",
        f"| Safe-action rate | {funnel['safe_action_rate']:.1%} |",
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
        "tool_execution = false",
        "runtime_loop = false",
        "```",
        "",
    ])
    return "\n".join(lines)


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    payload = load_dataset()
    actual_hash = dataset_hash(payload)
    head = _git_head()
    if head is None or not head.startswith(args.expected_head):
        raise RuntimeError(
            f"evaluated HEAD must start with {args.expected_head}, got {head}"
        )
    if actual_hash != EXPECTED_DATASET_HASH:
        raise RuntimeError(
            f"dataset hash drift: expected {EXPECTED_DATASET_HASH}, got {actual_hash}"
        )

    router = LLMRouter(provider_mode="deepseek")
    provider, provider_name = router._get_active_provider()
    records = []
    for index, case in enumerate(payload["cases"], start=1):
        print(f"[{index:02d}/{len(payload['cases'])}] {case['id']}", flush=True)
        record = await _run_case(
            case,
            provider=provider,
            provider_name=provider_name,
            case_timeout=args.case_timeout,
        )
        records.append(record)
        result = "PASS" if record["passed"] else (
            f"{record.get('failure_category')}:{record.get('failure_subcategory')}"
        )
        print(
            f"  {result} {record['latency_ms']:.0f}ms "
            f"{record['format_path']}",
            flush=True,
        )

    return {
        "harness_version": HARNESS_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "evaluated_head": head,
        "working_tree_dirty": _git_dirty(),
        "dataset": {
            "version": payload["version"],
            "hash": actual_hash,
            "case_count": len(payload["cases"]),
            "fixture_hash": _sha256(json.dumps(
                [
                    {
                        "state": case["state"],
                        "observation": case["observation"],
                    }
                    for case in payload["cases"]
                ],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )),
        },
        "selector": {
            "symbol": "agent.next_action_selector.NextActionSelector",
            "prompt_hash": _sha256(NEXT_ACTION_PROMPT),
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
            "tool_execution": False,
            "runtime_loop": False,
            "case_attempts": 1,
        },
        "summary": _summary(records),
        "records": records,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-head", default=EXPECTED_HEAD_PREFIX)
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
