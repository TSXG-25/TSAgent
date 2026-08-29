#!/usr/bin/env python3
"""Real-provider acceptance harness for the v2.4A Planner.

This harness calls the production plan_with_metadata entry point. It does not
copy the Planner prompt, execute a plan, repair Provider output, or use a
golden plan as a fallback. Each Dataset case is submitted once; Provider
calls made internally by the production Planner are recorded as evidence.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter, defaultdict
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any, Mapping, Sequence

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DATASET_PATH = ROOT / "evals" / "planner" / "dataset.json"
DEFAULT_JSON = ROOT / "realtest_reports" / "results" / "v24a_planner_baseline.json"
DEFAULT_MARKDOWN = ROOT / "realtest_reports" / "results" / "v24a_planner_baseline.md"
HARNESS_VERSION = "v2.4A-2-real-planner-v2"
CAPABILITY_SUBCATEGORIES = (
    "UNDER_PLAN",
    "OVER_PLAN",
    "BAD_DEPENDENCY",
    "WRONG_GRANULARITY",
    "MISSED_CLARIFICATION",
    "FALSE_CLARIFICATION",
    "GOAL_DRIFT",
    "UNEXECUTABLE_TASK",
)


def _json_safe(value: Any) -> Any:
    """Convert Provider/Pydantic values without serializing live SDK objects."""

    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump())
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return str(value)


def _secret_values() -> tuple[str, ...]:
    values = []
    for name in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY", "ANTHROPIC_API_KEY"):
        value = os.environ.get(name, "")
        if len(value) >= 8:
            values.append(value)
    return tuple(values)


def _redact_text(value: object) -> str:
    text = str(value)
    for secret in _secret_values():
        text = text.replace(secret, "[REDACTED]")
    return text


def _stable_error(error: BaseException) -> dict[str, str]:
    message = _redact_text(error).strip()
    if not message:
        message = f"{type(error).__name__} without diagnostic text"
    return {"type": type(error).__name__, "message": message[:500]}


def _classify_provider_error(error: BaseException) -> str:
    combined = f"{type(error).__name__} {_redact_text(error)}".lower()
    if isinstance(error, (asyncio.TimeoutError, TimeoutError)) or any(
        token in combined for token in ("timeout", "timed out", "deadline")
    ):
        return "TIMEOUT"
    if any(token in combined for token in ("401", "403", "auth", "api key", "unauthorized")):
        return "AUTH"
    if any(token in combined for token in ("429", "rate limit", "ratelimit")):
        return "RATE_LIMIT"
    if any(
        token in combined
        for token in ("connection", "connecterror", "dns", "network", "name resolution")
    ):
        return "NETWORK"
    if any(
        token in combined
        for token in ("response_format", "structured output", "json_schema")
    ):
        return "STRUCTURED_OUTPUT_REJECTED"
    if any(token in combined for token in ("invalid json", "jsondecode", "malformed", "parse")):
        return "MALFORMED_RESPONSE"
    if any(token in combined for token in ("503", "502", "unavailable", "overloaded")):
        return "UNAVAILABLE"
    return "INTERNAL"


def _response_preview(result: Any) -> str | None:
    if result is None:
        return None
    content = getattr(result, "content", None)
    if isinstance(content, str):
        return _redact_text(content)[:4000]
    if content is not None:
        return _redact_text(content)[:4000]
    if hasattr(result, "model_dump"):
        return json.dumps(_json_safe(result.model_dump()), ensure_ascii=False)[:4000]
    return None


def _token_usage(result: Any) -> dict[str, int]:
    candidates = [
        getattr(result, "usage_metadata", None),
        getattr(result, "response_metadata", None),
        getattr(result, "additional_kwargs", None),
    ]
    usage: dict[str, int] = {}
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        nested = candidate.get("token_usage") or candidate.get("usage")
        source = nested if isinstance(nested, Mapping) else candidate
        aliases = {
            "prompt_tokens": ("prompt_tokens", "input_tokens"),
            "completion_tokens": ("completion_tokens", "output_tokens"),
            "total_tokens": ("total_tokens",),
        }
        for target, names in aliases.items():
            for name in names:
                value = source.get(name)
                if isinstance(value, (int, float)):
                    usage[target] = int(value)
                    break
    return usage


class _CallRecorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def record(
        self,
        *,
        provider: str,
        call_kind: str,
        started: float,
        result: Any = None,
        error: BaseException | None = None,
    ) -> None:
        item: dict[str, Any] = {
            "sequence": len(self.calls) + 1,
            "provider": provider,
            "call_kind": call_kind,
            "outcome": "ERROR" if error is not None else "SUCCESS",
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
            "token_usage": _token_usage(result),
            "response_preview": _response_preview(result),
        }
        if error is not None:
            item["error_code"] = _classify_provider_error(error)
            item["error"] = _stable_error(error)
        self.calls.append(item)

    @property
    def errors(self) -> tuple[dict[str, Any], ...]:
        return tuple(call for call in self.calls if call["outcome"] == "ERROR")

    @property
    def successes(self) -> tuple[dict[str, Any], ...]:
        return tuple(call for call in self.calls if call["outcome"] == "SUCCESS")

    def token_totals(self) -> dict[str, int]:
        totals: Counter[str] = Counter()
        for call in self.calls:
            for key, value in call.get("token_usage", {}).items():
                totals[key] += int(value)
        return dict(totals)


class _RecordingRunnable:
    def __init__(self, inner: Any, recorder: _CallRecorder, provider: str, kind: str) -> None:
        self._inner = inner
        self._recorder = recorder
        self._provider = provider
        self._kind = kind

    async def ainvoke(self, messages: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            result = await self._inner.ainvoke(messages, **kwargs)
        except BaseException as error:
            self._recorder.record(
                provider=self._provider,
                call_kind=self._kind,
                started=started,
                error=error,
            )
            raise
        self._recorder.record(
            provider=self._provider,
            call_kind=self._kind,
            started=started,
            result=result,
        )
        return result

    def invoke(self, messages: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            result = self._inner.invoke(messages, **kwargs)
        except BaseException as error:
            self._recorder.record(
                provider=self._provider,
                call_kind=self._kind,
                started=started,
                error=error,
            )
            raise
        self._recorder.record(
            provider=self._provider,
            call_kind=self._kind,
            started=started,
            result=result,
        )
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


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
        return _RecordingRunnable(
            runnable,
            self._recorder,
            self._provider,
            "structured_ainvoke",
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _RecordingRouter:
    """Transparent production LLM router wrapper used only for evidence."""

    def __init__(self, inner: Any, recorder: _CallRecorder) -> None:
        self._inner = inner
        self._recorder = recorder
        self._provider_wrappers: dict[int, _RecordingProvider] = {}

    @property
    def supports_structured_output(self) -> bool:
        return bool(self._inner.supports_structured_output)

    def disable_structured_output(self) -> None:
        self._inner.disable_structured_output()

    def _get_active_provider(self) -> tuple[Any, str]:
        provider, name = self._inner._get_active_provider()
        key = id(provider)
        wrapper = self._provider_wrappers.get(key)
        if wrapper is None:
            wrapper = _RecordingProvider(provider, self._recorder, name)
            self._provider_wrappers[key] = wrapper
        return wrapper, name

    async def ainvoke(self, messages: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            result = await self._inner.ainvoke(messages, **kwargs)
        except BaseException as error:
            self._recorder.record(
                provider=os.environ.get("TSAGENT_LLM_PROVIDER", "unknown"),
                call_kind="router_ainvoke",
                started=started,
                error=error,
            )
            raise
        self._recorder.record(
            provider=os.environ.get("TSAGENT_LLM_PROVIDER", "unknown"),
            call_kind="router_ainvoke",
            started=started,
            result=result,
        )
        return result

    def invoke(self, messages: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            result = self._inner.invoke(messages, **kwargs)
        except BaseException as error:
            self._recorder.record(
                provider=os.environ.get("TSAGENT_LLM_PROVIDER", "unknown"),
                call_kind="router_invoke",
                started=started,
                error=error,
            )
            raise
        self._recorder.record(
            provider=os.environ.get("TSAGENT_LLM_PROVIDER", "unknown"),
            call_kind="router_invoke",
            started=started,
            result=result,
        )
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


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


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(len(ordered) * percentile))
    return ordered[rank - 1]


def _planner_record(output: Any) -> dict[str, Any]:
    return {
        "tasks": _json_safe(output.tasks),
        "constraints": _json_safe(output.constraints),
        "abstain": bool(output.abstain),
        "abstain_reason": str(output.abstain_reason or ""),
        "raw": _json_safe(output.raw),
        "failure_code": str(output.failure_code or ""),
        "failure_message": _redact_text(output.failure_message or ""),
    }


def _plan_payload(output: Any) -> dict[str, Any]:
    return {
        "tasks": _json_safe(output.tasks),
        "abstain": bool(output.abstain),
        "metadata": {
            "constraints": _json_safe(output.constraints),
            "abstain_reason": str(output.abstain_reason or ""),
        },
    }


def _failure_taxonomy(
    case: Mapping[str, Any],
    output: Any,
    oracle: Mapping[str, Any],
) -> tuple[str | None, str | None, list[str], dict[str, Any]]:
    if bool(oracle.get("passed", False)):
        return None, None, [], {}

    expected_mode = str(case.get("expected_mode", ""))
    actual_abstain = bool(output.abstain)
    tasks = list(output.tasks)
    categories: list[str] = []
    errors = [str(value) for value in oracle.get("errors", [])]

    if expected_mode == "abstain" and (tasks or not actual_abstain):
        categories.append("MISSED_CLARIFICATION")
    elif expected_mode != "abstain" and actual_abstain:
        categories.append("FALSE_CLARIFICATION")

    if errors:
        if any("dependency" in error for error in errors):
            categories.append("BAD_DEPENDENCY")
        if any("schema" in error or "task[" in error for error in errors):
            categories.append("UNEXECUTABLE_TASK")

    if float(oracle.get("missing_task_rate", 0.0)) > 0 or int(
        oracle.get("missing_task_count", 0)
    ) > 0:
        categories.append("UNDER_PLAN")
    if bool(oracle.get("overplanned", 0.0)) or int(
        oracle.get("unnecessary_task_count", 0)
    ) > 0:
        categories.append("OVER_PLAN")
    if float(oracle.get("task_granularity", 1.0)) == 0.0:
        categories.append("WRONG_GRANULARITY")

    missing = int(oracle.get("missing_task_count", 0))
    unexpected = int(oracle.get("unnecessary_task_count", 0))
    if missing and unexpected:
        categories.append("GOAL_DRIFT")

    if float(oracle.get("executable_plan", 0.0)) == 0.0 and not categories:
        categories.append("UNEXECUTABLE_TASK")

    categories = list(dict.fromkeys(
        item for item in categories if item in CAPABILITY_SUBCATEGORIES
    ))
    if not categories:
        categories.append("UNEXECUTABLE_TASK")

    evidence = {
        "expected_mode": expected_mode,
        "actual_mode": "abstain" if actual_abstain else ("plan" if tasks else "chat"),
        "predicted_task_count": int(oracle.get("predicted_task_count", len(tasks))),
        "goal_unit_count": int(oracle.get("goal_unit_count", 0)),
        "missing_goal_units": _json_safe(oracle.get("missing_goal_units", [])),
        "unexpected_task_ids": _json_safe(oracle.get("unexpected_task_ids", [])),
        "oracle_errors": errors,
        "task_granularity": float(oracle.get("task_granularity", 0.0)),
        "dependency_validity": float(oracle.get("dependency_validity", 0.0)),
        "executable_plan": float(oracle.get("executable_plan", 0.0)),
    }
    return "P-CAP", categories[0], categories, evidence


def _provider_evidence(recorder: _CallRecorder) -> dict[str, Any]:
    """Classify provider selection and response-format paths independently."""

    if not recorder.calls:
        return {
            "provider_path": "NOT_CALLED",
            "format_path": "NOT_CALLED",
            "provider_sequence": [],
            "resolved_provider_sequence": [],
        }

    provider_sequence = list(dict.fromkeys(
        str(call.get("provider", "")).strip().lower()
        for call in recorder.calls
        if str(call.get("provider", "")).strip()
    ))
    unresolved_labels = {"", "unknown", "auto"}
    resolved_provider_sequence = [
        provider
        for provider in provider_sequence
        if provider not in unresolved_labels
    ]
    if len(resolved_provider_sequence) > 1:
        provider_path = "CROSS_PROVIDER_FALLBACK"
    elif len(resolved_provider_sequence) == 1 and not any(
        provider in unresolved_labels for provider in provider_sequence
    ):
        provider_path = "SINGLE_PROVIDER"
    else:
        # The explicit acceptance harness uses a concrete provider mode.  If
        # an auto router is supplied, its internal provider choice is not
        # observable through this transparent wrapper, so do not guess.
        provider_path = "UNRESOLVED"

    structured_kinds = {"structured_bind", "structured_ainvoke"}
    raw_kinds = {"router_ainvoke", "router_invoke"}
    has_structured = any(
        str(call.get("call_kind", "")) in structured_kinds
        for call in recorder.calls
    )
    has_raw = any(
        str(call.get("call_kind", "")) in raw_kinds
        for call in recorder.calls
    )
    if has_structured and has_raw:
        format_path = "STRUCTURED_TO_RAW_FALLBACK"
    elif has_structured:
        format_path = "STRUCTURED_ONLY"
    elif has_raw:
        format_path = "RAW_ONLY"
    else:
        format_path = "UNRESOLVED"
    return {
        "provider_path": provider_path,
        "format_path": format_path,
        "provider_sequence": provider_sequence,
        "resolved_provider_sequence": resolved_provider_sequence,
    }


def _provider_status(recorder: _CallRecorder, output: Any | None) -> str:
    if not recorder.calls:
        return "NOT_CALLED"
    if output is not None and str(output.failure_code or "").startswith("PROVIDER_"):
        return "PROVIDER_ERROR"
    if not recorder.successes:
        return "PROVIDER_ERROR"
    evidence = _provider_evidence(recorder)
    if evidence["provider_path"] == "CROSS_PROVIDER_FALLBACK":
        return "SUCCESS_WITH_PROVIDER_FALLBACK"
    if evidence["format_path"] == "STRUCTURED_TO_RAW_FALLBACK":
        return "SUCCESS_WITH_FORMAT_FALLBACK"
    if recorder.errors:
        return "SUCCESS_WITH_RETRY"
    return "SUCCESS"


def _planning_context_evidence(planning_context: Any | None) -> dict[str, Any]:
    if planning_context is None:
        return {"provided": False}
    return {
        "provided": True,
        "completed_tasks": _json_safe(planning_context.completed_tasks),
        "established_facts": _json_safe(planning_context.established_facts),
        "available_artifacts": _json_safe(planning_context.available_artifacts),
        "continuation_scope": _json_safe(planning_context.continuation_scope),
    }


async def _run_case(
    *,
    case: Mapping[str, Any],
    planner_module: Any,
    production_llm: Any,
    case_timeout: float,
    planning_context: Any | None = None,
) -> dict[str, Any]:
    recorder = _CallRecorder()
    original_planner_llm = planner_module.llm
    planner_module.llm = _RecordingRouter(production_llm, recorder)
    started = time.perf_counter()
    output: Any | None = None
    runtime_error: BaseException | None = None
    try:
        output = await asyncio.wait_for(
            planner_module.plan_with_metadata(
                str(case["input"]),
                memory_context="",
                repo_context="",
                skill_hint="",
                intent=None,
                grounding=None,
                planning_context=planning_context,
            ),
            timeout=case_timeout,
        )
    except BaseException as error:
        runtime_error = error
    finally:
        planner_module.llm = original_planner_llm

    provider_evidence = _provider_evidence(recorder)
    record: dict[str, Any] = {
        "case_id": str(case["id"]),
        "family": str(case.get("family", "")),
        "name": str(case.get("name", "")),
        "input": str(case.get("input", "")),
        "expected_mode": str(case.get("expected_mode", "")),
        "case_attempts": 1,
        "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        "provider_status": _provider_status(recorder, output),
        "provider_path": provider_evidence["provider_path"],
        "format_path": provider_evidence["format_path"],
        "provider_sequence": provider_evidence["provider_sequence"],
        "resolved_provider_sequence": provider_evidence["resolved_provider_sequence"],
        "provider_calls": recorder.calls,
        "token_usage": recorder.token_totals(),
        "planner_output": _planner_record(output) if output is not None else None,
        "normalized_plan": _plan_payload(output) if output is not None else None,
        "oracle_result": None,
        "evaluable": False,
        "passed": False,
        "failure_category": None,
        "failure_subcategory": None,
        "failure_categories": [],
        "evidence": {},
        "planning_context": _planning_context_evidence(planning_context),
    }

    if runtime_error is not None:
        if isinstance(runtime_error, (asyncio.TimeoutError, TimeoutError)):
            record["provider_status"] = "PROVIDER_ERROR"
            record["failure_category"] = "P-PROV"
            record["failure_subcategory"] = "TIMEOUT"
            record["failure_categories"] = ["TIMEOUT"]
        else:
            record["provider_status"] = "P-INT"
            record["failure_category"] = "P-INT"
            record["failure_subcategory"] = type(runtime_error).__name__
            record["failure_categories"] = [type(runtime_error).__name__]
        record["evidence"] = {"error": _stable_error(runtime_error)}
        return record

    if record["provider_status"] == "PROVIDER_ERROR":
        record["failure_category"] = "P-PROV"
        error_codes = [
            str(call.get("error_code", "INTERNAL"))
            for call in recorder.errors
        ]
        record["failure_subcategory"] = error_codes[0] if error_codes else "PROVIDER_ERROR"
        record["failure_categories"] = list(dict.fromkeys(error_codes or ["PROVIDER_ERROR"]))
        record["evidence"] = {
            "provider_errors": [_json_safe(call.get("error")) for call in recorder.errors],
        }
        return record

    try:
        from evals.planner.oracle import evaluate_plan

        oracle = evaluate_plan(case, _plan_payload(output))
    except BaseException as error:
        record["failure_category"] = "P-CON"
        record["failure_subcategory"] = type(error).__name__
        record["failure_categories"] = [type(error).__name__]
        record["evidence"] = {"oracle_error": _stable_error(error)}
        return record

    record["oracle_result"] = _json_safe(oracle)
    record["evaluable"] = True
    record["passed"] = bool(oracle.get("passed", False))
    if not record["passed"]:
        (
            record["failure_category"],
            record["failure_subcategory"],
            record["failure_categories"],
            record["evidence"],
        ) = _failure_taxonomy(case, output, oracle)
    return record


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


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(len(ordered) * percentile))
    return ordered[rank - 1]


def _provider_public_config(provider: str) -> dict[str, Any]:
    if provider == "ollama":
        return {
            "provider": "ollama",
            "model": os.environ.get("OLLAMA_MODEL", ""),
            "base_url": os.environ.get(
                "OLLAMA_BASE_URL",
                "http://localhost:11434/v1",
            ),
            "structured_output": False,
        }
    return {
        "provider": provider,
        "model": os.environ.get("MODEL_NAME", ""),
        "base_url": "https://api.deepseek.com/v1" if provider == "deepseek" else "",
        "structured_output": True,
    }


def _build_summary(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    evaluated = [record for record in records if bool(record.get("evaluable"))]
    oracle_reports = [
        record["oracle_result"]
        for record in evaluated
        if isinstance(record.get("oracle_result"), Mapping)
    ]
    if oracle_reports:
        from agent.planner.evaluator import aggregate_metrics

        aggregate = aggregate_metrics(oracle_reports).to_dict()
    else:
        aggregate = {
            "case_count": 0,
            "schema_validity": 0.0,
            "dependency_validity": 0.0,
            "plan_validity": 0.0,
            "dependency_accuracy": 0.0,
            "task_granularity": 0.0,
            "unnecessary_task_rate": 0.0,
            "missing_task_rate": 0.0,
            "executable_plan_rate": 0.0,
            "overplanning_rate": 0.0,
            "critical_missing_task_rate": 0.0,
        }

    pass_count = sum(bool(record.get("passed")) for record in evaluated)
    clarification_cases = [
        record
        for record in evaluated
        if str(record.get("expected_mode")) in {"chat", "abstain", "plan"}
    ]
    clarification_correct = sum(
        bool((record.get("planner_output") or {}).get("abstain"))
        == (str(record.get("expected_mode")) == "abstain")
        for record in clarification_cases
    )
    non_plan = [
        record for record in evaluated if str(record.get("expected_mode")) != "plan"
    ]
    unnecessary_planning = sum(
        bool((record.get("planner_output") or {}).get("tasks"))
        for record in non_plan
    )
    task_counts = [
        int((record.get("oracle_result") or {}).get("predicted_task_count", 0))
        for record in evaluated
    ]
    latencies = [float(record.get("latency_ms", 0.0)) for record in records]
    clusters: Counter[str] = Counter()
    family_clusters: dict[str, Counter[str]] = defaultdict(Counter)
    for record in records:
        category = record.get("failure_category")
        subcategory = record.get("failure_subcategory")
        if category:
            key = f"{category}:{subcategory}" if subcategory else str(category)
            clusters[key] += 1
            family_clusters[str(record.get("family", ""))][key] += 1

    provider_errors = sum(
        str(record.get("failure_category", "")).startswith("P-PROV")
        for record in records
    )
    runtime_errors = sum(record.get("failure_category") == "P-INT" for record in records)
    oracle_errors = sum(record.get("failure_category") == "P-CON" for record in records)
    provider_paths = Counter(str(record.get("provider_path", "UNRESOLVED")) for record in records)
    format_paths = Counter(str(record.get("format_path", "UNRESOLVED")) for record in records)
    return {
        "case_count": len(records),
        "evaluable_case_count": len(evaluated),
        "capability_pass_count": pass_count,
        "capability_pass_rate": pass_count / len(evaluated) if evaluated else 0.0,
        "raw_case_pass_rate": pass_count / len(records) if records else 0.0,
        "schema_validity": aggregate["schema_validity"],
        "dependency_validity": aggregate["dependency_validity"],
        "executable_plan_rate": aggregate["executable_plan_rate"],
        "missing_task_rate": aggregate["missing_task_rate"],
        "overplanning_rate": aggregate["overplanning_rate"],
        "clarification_accuracy": (
            clarification_correct / len(clarification_cases)
            if clarification_cases
            else 0.0
        ),
        "unnecessary_planning_rate": (
            unnecessary_planning / len(non_plan) if non_plan else 0.0
        ),
        "average_task_count": (
            sum(task_counts) / len(task_counts) if task_counts else 0.0
        ),
        "p95_task_count": _percentile(task_counts, 0.95),
        "average_latency_ms": sum(latencies) / len(latencies) if latencies else 0.0,
        "p95_latency_ms": _percentile(latencies, 0.95),
        "provider_error_count": provider_errors,
        "runtime_integration_failure_count": runtime_errors,
        "contract_or_oracle_failure_count": oracle_errors,
        "provider_path_counts": dict(sorted(provider_paths.items())),
        "format_path_counts": dict(sorted(format_paths.items())),
        "provider_fallback_case_ids": [
            str(record.get("case_id", ""))
            for record in records
            if record.get("provider_path") == "CROSS_PROVIDER_FALLBACK"
        ],
        "format_fallback_case_ids": [
            str(record.get("case_id", ""))
            for record in records
            if record.get("format_path") == "STRUCTURED_TO_RAW_FALLBACK"
        ],
        "failure_clusters": dict(clusters),
        "failure_clusters_by_family": {
            family: dict(values) for family, values in family_clusters.items()
        },
        "oracle_aggregate": aggregate,
        "metric_scopes": {
            "capability_rates": (
                "evaluable cases only; Provider/contract/runtime failures excluded"
            ),
            "raw_case_pass_rate": "all submitted cases",
            "clarification_accuracy": (
                "evaluable cases; abstain matches expected_mode=abstain"
            ),
            "unnecessary_planning_rate": (
                "evaluable non-plan cases emitting one or more tasks"
            ),
            "task_count": "evaluable cases",
        },
    }


def _markdown(report: Mapping[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# v2.4A-2 Real Planner Capability Baseline",
        "",
        f"- Harness: {report['harness_version']}",
        f"- HEAD: {report.get('git_head')}",
        f"- Dataset: {report['dataset_version']}",
        f"- Dataset hash: {report['dataset_hash']}",
        f"- Provider: {report['provider']['provider']} / {report['provider']['model']}",
        f"- Provider mode: {report['provider_mode']} (fallback disabled)",
        f"- Automatic case retry: {report['controls']['automatic_case_retry']}",
        f"- Golden-plan fallback: {report['controls']['golden_plan_fallback']}",
        "",
        "## Summary",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Cases submitted | {summary['case_count']} |",
        f"| Evaluable cases | {summary['evaluable_case_count']} |",
        f"| Capability pass | {summary['capability_pass_count']}/{summary['evaluable_case_count']} ({summary['capability_pass_rate']:.1%}) |",
        f"| Raw case pass | {summary['capability_pass_count']}/{summary['case_count']} ({summary['raw_case_pass_rate']:.1%}) |",
        f"| Schema validity | {summary['schema_validity']:.1%} |",
        f"| Dependency validity | {summary['dependency_validity']:.1%} |",
        f"| Executable plan rate | {summary['executable_plan_rate']:.1%} |",
        f"| Missing task rate | {summary['missing_task_rate']:.1%} |",
        f"| Overplanning rate | {summary['overplanning_rate']:.1%} |",
        f"| Clarification accuracy | {summary['clarification_accuracy']:.1%} |",
        f"| Unnecessary planning rate | {summary['unnecessary_planning_rate']:.1%} |",
        f"| Average / P95 tasks | {summary['average_task_count']:.2f} / {summary['p95_task_count']} |",
        f"| Average / P95 latency | {summary['average_latency_ms']:.0f}ms / {summary['p95_latency_ms']:.0f}ms |",
        "",
        "## Provider and format paths",
        "",
        f"- Provider path counts: `{json.dumps(summary.get('provider_path_counts', {}), ensure_ascii=False, sort_keys=True)}`",
        f"- Format path counts: `{json.dumps(summary.get('format_path_counts', {}), ensure_ascii=False, sort_keys=True)}`",
        f"- Cross-provider fallback cases: `{', '.join(summary.get('provider_fallback_case_ids', [])) or 'none'}`",
        f"- Structured-to-raw cases: `{', '.join(summary.get('format_fallback_case_ids', [])) or 'none'}`",
        "",
        "## Failure separation",
        "",
        f"- Provider/API failures: **{summary['provider_error_count']}**",
        f"- Contract/Oracle failures: **{summary['contract_or_oracle_failure_count']}**",
        f"- Runtime/integration failures: **{summary['runtime_integration_failure_count']}**",
        "",
        "## Failure clustering",
        "",
        "| Cluster | Count |",
        "| --- | ---: |",
    ]
    clusters = summary["failure_clusters"]
    if clusters:
        lines.extend(f"| {key} | {value} |" for key, value in sorted(clusters.items()))
    else:
        lines.append("| — | 0 |")
    lines.extend(
        [
            "",
            "## Case results",
            "",
            "| Case | Family | Mode | Provider status | Provider path | Format path | Pass | Tasks | Latency | Failure |",
            "| --- | --- | --- | --- | --- | --- | :---: | ---: | ---: | --- |",
        ]
    )
    for record in report["cases"]:
        oracle = record.get("oracle_result") or {}
        failure = record.get("failure_category") or "—"
        if record.get("failure_subcategory"):
            failure += f":{record['failure_subcategory']}"
        lines.append(
            f"| {record['case_id']} | {record['family']} | "
            f"{record['expected_mode']} | {record['provider_status']} | "
            f"{record.get('provider_path', '—')} | {record.get('format_path', '—')} | "
            f"{'✅' if record.get('passed') else '❌'} | "
            f"{oracle.get('predicted_task_count', '—')} | "
            f"{float(record.get('latency_ms', 0.0)):.0f}ms | {failure} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation rules",
            "",
            "- Provider/API failures are not included in capability-rate denominators.",
            "- The harness submits each Dataset case exactly once and does not retry the case.",
            "- Production Planner internal structured/JSON fallback calls are recorded, not altered.",
            "- No golden plan, case-specific correction, or output repair is used.",
        ]
    )
    return "\n".join(lines) + "\n"


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    os.chdir(ROOT)
    load_dotenv(args.dotenv if args.dotenv else ROOT / ".env")
    os.environ["TSAGENT_LLM_PROVIDER"] = args.provider
    os.environ.setdefault("TRANSFORMERS_NO_TF", "1")
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

    from evals.planner.oracle import dataset_hash, load_dataset, validate_dataset

    payload = load_dataset(Path(args.dataset) if args.dataset else DATASET_PATH)
    validation = validate_dataset(payload)
    if not validation.valid:
        return {
            "harness_version": HARNESS_VERSION,
            "status": "INVALID_DATASET",
            "dataset_errors": list(validation.errors),
        }

    import importlib

    planner_module = importlib.import_module("agent.planner.planner")
    from agent.llm import llm as production_llm

    original_planner_llm = planner_module.llm
    selected_ids = (
        {value.strip() for value in args.ids.split(",") if value.strip()}
        if args.ids
        else None
    )
    cases = [
        case
        for case in payload["cases"]
        if selected_ids is None or str(case["id"]) in selected_ids
    ]
    records: list[dict[str, Any]] = []
    try:
        for index, case in enumerate(cases, start=1):
            print(
                f"[{index}/{len(cases)}] {case['id']} "
                f"{str(case.get('name', ''))[:48]}",
                flush=True,
            )
            record = await _run_case(
                case=case,
                planner_module=planner_module,
                production_llm=production_llm,
                case_timeout=float(args.case_timeout),
            )
            records.append(record)
            print(
                f"  -> {record['provider_status']} "
                f"{'PASS' if record['passed'] else record.get('failure_category', 'FAIL')} "
                f"{record['latency_ms']:.0f}ms",
                flush=True,
            )
    finally:
        planner_module.llm = original_planner_llm

    prompt_hash = _sha256_text(str(getattr(planner_module, "PLANNER_PROMPT", "")))
    return {
        "harness_version": HARNESS_VERSION,
        "status": "COMPLETED",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "git_head": _git_head(),
        "working_tree_dirty": _git_dirty(),
        "dataset_path": str(Path(args.dataset) if args.dataset else DATASET_PATH),
        "dataset_version": str(payload["version"]),
        "dataset_hash": dataset_hash(payload),
        "planner_prompt_hash": prompt_hash,
        "provider": _provider_public_config(args.provider),
        "provider_mode": args.provider,
        "controls": {
            "automatic_case_retry": False,
            "golden_plan_fallback": False,
            "provider_fallback": False,
            "case_attempts": 1,
            "case_timeout_seconds": float(args.case_timeout),
        },
        "case_selection": {
            "requested_ids": sorted(selected_ids) if selected_ids is not None else None,
            "submitted_ids": [str(case["id"]) for case in cases],
        },
        "summary": _build_summary(records),
        "cases": records,
    }


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--provider",
        choices=("deepseek", "ollama"),
        default="deepseek",
        help="Explicit Provider; no cross-provider fallback is enabled.",
    )
    parser.add_argument("--dataset", type=Path, default=None)
    parser.add_argument(
        "--ids",
        default="",
        help="Comma-separated case IDs; default is all 50.",
    )
    parser.add_argument("--dotenv", type=Path, default=None)
    parser.add_argument("--case-timeout", type=float, default=180.0)
    parser.add_argument("--output", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--markdown-output", type=Path, default=DEFAULT_MARKDOWN)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    report = asyncio.run(_run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_output.write_text(_markdown(report), encoding="utf-8")
    print(f"JSON report: {args.output}")
    print(f"Markdown report: {args.markdown_output}")
    if report.get("status") == "INVALID_DATASET":
        print("Dataset validation failed: " + "; ".join(report["dataset_errors"]))
        return 2
    summary = report["summary"]
    print(
        "Baseline: "
        f"{summary['capability_pass_count']}/{summary['evaluable_case_count']} "
        f"evaluable capability cases; "
        f"{summary['provider_error_count']} Provider errors; "
        f"{summary['contract_or_oracle_failure_count']} contract/oracle failures; "
        f"{summary['runtime_integration_failure_count']} runtime failures."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
