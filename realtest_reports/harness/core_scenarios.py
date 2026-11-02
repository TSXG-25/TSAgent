"""Core real-provider multi-turn scenario regression harness.

Purpose: validate real multi-turn behavior that the offline pytest suite
cannot prove — durable conversation continuity, follow-up reference
resolution, artifact modification across turns, authorization/prohibition
handling, provider-call budgets — through the configured real LLM provider.

This is a real-scenario test artifact (AGENTS.md testing boundary).  It is
NOT part of the offline pytest suite and must be run on demand:

    python -m realtest_reports.harness.core_scenarios [--case ID] \
        [--provider auto|deepseek|ollama] [--list]

The harness aborts early with a clear message when no usable provider is
configured; it never fakes a provider.  Per case it creates a fresh SQLite
runtime store and a per-case workspace under
``realtest_reports/results/core_scenarios_<timestamp>_workspace/`` and
writes ``core_scenarios_<timestamp>.json`` + ``.md`` summaries.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("TRANSFORMERS_NO_TF", "1")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

MANIFEST = "core-scenarios-v1"
CASES_FILE = Path(__file__).with_name("core_scenarios_cases.json")
RESULTS_DIR = ROOT / "realtest_reports" / "results"


# ────────────────────────── provider gate ──────────────────────────


def _load_dotenv() -> None:
    from dotenv import load_dotenv

    # Same convention as agent.llm: find_dotenv walks up from the calling
    # module's directory, so the repo .env is found regardless of cwd.
    load_dotenv()


def _provider_mode(cli_provider: str | None) -> str:
    raw = cli_provider or os.getenv("TSAGENT_LLM_PROVIDER") or "auto"
    mode = raw.strip().lower()
    if mode not in {"auto", "deepseek", "ollama"}:
        raise SystemExit(
            f"invalid provider {mode!r}: expected auto|deepseek|ollama"
        )
    return mode


def _ollama_reachable() -> tuple[bool, str]:
    base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
    parsed = urlparse(base)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=3.0):
            return True, f"{host}:{port}"
    except OSError as error:
        return False, f"{host}:{port} ({error})"


def ensure_provider(mode: str) -> str | None:
    """Return an abort message when no usable real provider is configured."""
    if mode in {"auto", "deepseek"}:
        if not os.getenv("OPENAI_API_KEY", "").strip():
            return (
                f"no LLM provider key configured for provider mode {mode!r}: "
                "set OPENAI_API_KEY in .env for DeepSeek, or run with "
                "--provider ollama against a reachable OLLAMA_BASE_URL. "
                "Refusing to fake a provider."
            )
        return None
    reachable, detail = _ollama_reachable()
    if not reachable:
        return (
            "provider mode 'ollama' selected but the Ollama endpoint is not "
            f"reachable at {detail}; start Ollama or set OLLAMA_BASE_URL. "
            "Refusing to fake a provider."
        )
    return None


# ────────────────────────── recording instrumentation ──────────────────────────


def _intent_to_dict(intent) -> dict:
    """Project IntentResult into the durable JSON shape used in reports."""
    deliveries = []
    for item in getattr(intent, "delivery_requirements", ()) or ():
        deliveries.append(
            item.to_dict() if hasattr(item, "to_dict") else dict(item)
        )
    criteria = []
    for item in getattr(intent, "acceptance_criteria", ()) or ():
        criteria.append(
            item.to_dict() if hasattr(item, "to_dict") else dict(item)
        )
    return {
        "domain": getattr(intent, "domain", ""),
        "action": getattr(intent, "action", ""),
        "target": getattr(intent, "target", ""),
        "confidence": getattr(intent, "confidence", 0.0),
        "summary": getattr(intent, "summary", ""),
        "requires_execution": bool(getattr(intent, "requires_execution", False)),
        "artifact_required": bool(getattr(intent, "artifact_required", False)),
        "requested_outcomes": [
            getattr(item, "value", str(item))
            for item in getattr(intent, "requested_outcomes", ()) or ()
        ],
        "delivery_requirements": deliveries,
        "acceptance_criteria": criteria,
        "write_targets": list(getattr(intent, "write_targets", ()) or ()),
        "prohibited_effects": list(
            getattr(intent, "prohibited_effects", ()) or ()
        ),
        "external_effects": [
            dict(item) for item in getattr(intent, "external_effects", ()) or ()
        ],
        "conversation_relation": getattr(intent, "conversation_relation", ""),
        "resolved_request": getattr(intent, "resolved_request", ""),
        "memory_action": getattr(intent, "memory_action", ""),
        "memory_facts": list(getattr(intent, "memory_facts", []) or []),
        "search_query": getattr(intent, "search_query", ""),
        "search_timeliness": getattr(intent, "search_timeliness", ""),
        "failure_code": getattr(intent, "failure_code", ""),
    }


class _Recorder:
    """Process-wide recording wrapper on the real provider/intent spine.

    Wraps the shared LLM router (call counting), the singleton IntentEngine
    (structured intent capture), the orchestrator (plan/replan counts) and
    the plan executor tool boundary (tool invocations).  The wrapped targets
    are the same production objects the Runtime uses — nothing is faked.
    """

    def __init__(self) -> None:
        self.provider_calls = 0
        self.plan_calls = 0
        self.replan_calls = 0
        self.tool_calls: list[dict] = []
        self.intents: list[dict] = []
        self._originals: list[tuple[object, str, object]] = []

    def _patch(self, owner: object, name: str, replacement: object) -> None:
        self._originals.append((owner, name, getattr(owner, name)))
        setattr(owner, name, replacement)

    def install(self) -> None:
        import agent.llm as llm_module
        import agent.executor.plan_executor as plan_executor
        import agent.orchestrator.main as orchestrator_main
        from agent.cognition import intent_engine as intent_module

        original_ainvoke = llm_module.llm.ainvoke
        original_invoke = llm_module.llm.invoke

        async def ainvoke(messages, **kwargs):
            self.provider_calls += 1
            return await original_ainvoke(messages, **kwargs)

        def invoke(messages, **kwargs):
            self.provider_calls += 1
            return original_invoke(messages, **kwargs)

        self._patch(llm_module.llm, "ainvoke", ainvoke)
        self._patch(llm_module.llm, "invoke", invoke)

        engine = intent_module.engine
        original_analyze = engine.analyze_async

        async def analyze_async(context):
            result = await original_analyze(context)
            self.intents.append(_intent_to_dict(result))
            return result

        # Instance attribute shadows the class method on the singleton.
        engine.analyze_async = analyze_async
        self._originals.append((engine, "analyze_async", original_analyze))

        original_plan = orchestrator_main.ExecutionOrchestrator.plan
        original_replan = orchestrator_main.ExecutionOrchestrator.replan
        original_recover = (
            orchestrator_main.ExecutionOrchestrator.recover_structural_failure
        )

        async def plan(owner, *args, **kwargs):
            self.plan_calls += 1
            return await original_plan(owner, *args, **kwargs)

        async def replan(owner, *args, **kwargs):
            self.replan_calls += 1
            return await original_replan(owner, *args, **kwargs)

        async def recover(owner, *args, **kwargs):
            self.replan_calls += 1
            return await original_recover(owner, *args, **kwargs)

        self._patch(orchestrator_main.ExecutionOrchestrator, "plan", plan)
        self._patch(orchestrator_main.ExecutionOrchestrator, "replan", replan)
        self._patch(
            orchestrator_main.ExecutionOrchestrator,
            "recover_structural_failure",
            recover,
        )

        original_exec_tool = plan_executor.PlanExecutor._exec_tool

        async def exec_tool(owner, tool_name, args=None, **kwargs):
            self.tool_calls.append(
                {
                    "tool": str(tool_name),
                    "args": {
                        str(key): str(value)[:160]
                        for key, value in dict(args or {}).items()
                    },
                }
            )
            return await original_exec_tool(
                owner, tool_name, args, **kwargs
            )

        self._patch(plan_executor.PlanExecutor, "_exec_tool", exec_tool)

    def close(self) -> None:
        for owner, name, original in reversed(self._originals):
            setattr(owner, name, original)
        self._originals.clear()

    def mark(self) -> tuple[int, int, int, int, int]:
        return (
            self.provider_calls,
            self.plan_calls,
            self.replan_calls,
            len(self.tool_calls),
            len(self.intents),
        )


# ────────────────────────── turn driver ──────────────────────────


async def _drive_turn(
    service,
    *,
    tenant_id: str,
    user_id: str,
    session_id: str,
    turn_index: int,
    request_text: str,
    timeout: float,
    recorder: _Recorder,
) -> dict:
    """Drive one durable Run to its terminal event and project the record."""
    from agent.service import (
        EventStreamRequest,
        RunLookupRequest,
        StartRunRequest,
    )

    baseline = recorder.mark()
    started = time.perf_counter()
    request_id = f"req-{turn_index}-{uuid.uuid4().hex[:8]}"
    error = ""
    events: list = []
    snapshot = None
    try:
        handle = await service.start_run(
            StartRunRequest(
                tenant_id=tenant_id,
                user_id=user_id,
                session_id=session_id,
                request_id=request_id,
                request_text=request_text,
            )
        )

        async def drain() -> None:
            async for event in service.stream_events(
                EventStreamRequest(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    session_id=session_id,
                    run_id=handle.run_id,
                    request_id=f"{request_id}-events",
                )
            ):
                events.append(event)

        try:
            await asyncio.wait_for(drain(), timeout=timeout)
        except asyncio.TimeoutError:
            error = f"TURN_TIMEOUT after {timeout:.0f}s"
        snapshot = await service.get_run(
            RunLookupRequest(
                tenant_id=tenant_id,
                user_id=user_id,
                session_id=session_id,
                run_id=handle.run_id,
                request_id=f"{request_id}-lookup",
            )
        )
    except Exception as exc:  # provider/service failures are evidence
        error = error or f"{type(exc).__name__}: {exc}"

    (
        provider_calls,
        plan_calls,
        replan_calls,
        tool_index,
        intent_index,
    ) = baseline
    turn_tools = recorder.tool_calls[tool_index:]
    turn_intents = recorder.intents[intent_index:]
    duration_ms = (time.perf_counter() - started) * 1000

    status = ""
    output = ""
    artifacts: list[dict] = []
    failure = None
    if snapshot is not None:
        status = snapshot.status.value
        output = snapshot.output.text if snapshot.output is not None else ""
        artifacts = [artifact.to_dict() for artifact in snapshot.artifacts]
        if snapshot.failure_summary is not None:
            failure = snapshot.failure_summary.to_dict()
    return {
        "turn": turn_index,
        "request": request_text,
        "status": status,
        "output": output[:3000],
        "artifacts": artifacts,
        "provider_calls": recorder.provider_calls - provider_calls,
        "plan_calls": recorder.plan_calls - plan_calls,
        "replan_calls": recorder.replan_calls - replan_calls,
        "tool_calls": turn_tools,
        "intent": turn_intents[-1] if turn_intents else None,
        "intent_count": len(turn_intents),
        "event_types": [event.event_type.value for event in events],
        "failure_summary": failure,
        "error": error,
        "duration_ms": round(duration_ms, 1),
    }


# ────────────────────────── deterministic checks ──────────────────────────


def _as_list(value) -> list:
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _match_files(workspace: Path, pattern: str) -> list[Path]:
    matched = []
    for path in workspace.glob(pattern):
        if not path.is_file():
            continue
        if path.name.startswith("runtime.sqlite"):
            continue
        matched.append(path)
    return sorted(matched)


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _eval_intent(spec: dict, intent: dict | None) -> list[dict]:
    results = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        results.append({"check": f"intent.{name}", "passed": bool(ok), "detail": detail})

    if intent is None:
        add("_recorded", False, "no intent analysis was recorded for this turn")
        return results

    exact_fields = (
        "domain",
        "action",
        "conversation_relation",
        "memory_action",
        "artifact_required",
        "requires_execution",
    )
    for field_name in exact_fields:
        if field_name in spec:
            expected = spec[field_name]
            actual = intent.get(field_name)
            add(field_name, actual == expected,
                f"expected {expected!r} got {actual!r}")
    for field_name in (
        "domain_in",
        "action_in",
        "conversation_relation_in",
        "memory_action_in",
    ):
        if field_name in spec:
            expected = list(spec[field_name])
            field = field_name[: -len("_in")]
            actual = intent.get(field)
            add(field_name, actual in expected,
                f"expected one of {expected} got {actual!r}")
    for token in _as_list(spec.get("resolved_request_contains")):
        haystack = str(intent.get("resolved_request", ""))
        add("resolved_request_contains", token in haystack,
            f"{token!r} in resolved_request={haystack[:160]!r}")
    write_targets = [str(item) for item in intent.get("write_targets", [])]
    for token in _as_list(spec.get("write_targets_contains")):
        add("write_targets_contains",
            any(token in item for item in write_targets),
            f"{token!r} in write_targets={write_targets}")
    for token in _as_list(spec.get("target_contains")):
        add("target_contains", token in str(intent.get("target", "")),
            f"{token!r} in target={intent.get('target')!r}")
    outcomes = [str(item) for item in intent.get("requested_outcomes", [])]
    for token in _as_list(spec.get("requested_outcomes_contains")):
        add("requested_outcomes_contains", token in outcomes,
            f"{token!r} in {outcomes}")
    for token in _as_list(spec.get("requested_outcomes_excludes")):
        add("requested_outcomes_excludes", token not in outcomes,
            f"{token!r} unexpectedly in {outcomes}")
    prohibited = [str(item) for item in intent.get("prohibited_effects", [])]
    for token in _as_list(spec.get("prohibited_effects_contains")):
        add("prohibited_effects_contains", token in prohibited,
            f"{token!r} in {prohibited}")
    return results


def _eval_turn_checks(spec: dict, turn: dict, workspace: Path) -> list[dict]:
    results = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        results.append({"check": name, "passed": bool(ok), "detail": detail})

    status = str(turn.get("status", ""))
    if "status" in spec:
        add("status", status == spec["status"],
            f"expected {spec['status']} got {status or 'none'}")
    elif "status_in" in spec:
        expected = list(spec["status_in"])
        add("status_in", status in expected,
            f"expected one of {expected} got {status or 'none'}")
    else:
        add("status", status == "COMPLETED",
            f"expected COMPLETED got {status or 'none'}")

    output = str(turn.get("output", "") or "")
    for token in _as_list(spec.get("output_contains")):
        add("output_contains", token in output,
            f"{token!r} missing from output")
    for token in _as_list(spec.get("output_not_contains")):
        add("output_not_contains", token not in output,
            f"{token!r} present in output")

    for pattern in _as_list(spec.get("artifact_exists")):
        matched = _match_files(workspace, pattern)
        add("artifact_exists", bool(matched),
            f"{pattern!r} matched {[str(p) for p in matched] or 'nothing'}")
    any_patterns = _as_list(spec.get("artifact_exists_any"))
    if any_patterns:
        matched_any = {
            pattern: _match_files(workspace, pattern)
            for pattern in any_patterns
        }
        add("artifact_exists_any",
            any(matched_any.values()),
            f"no match for any of {any_patterns}")
    for pattern in _as_list(spec.get("artifact_absent")):
        matched = _match_files(workspace, pattern)
        add("artifact_absent", not matched,
            f"{pattern!r} unexpectedly matched {[str(p) for p in matched]}")

    for pattern, tokens in dict(spec.get("file_contains", {})).items():
        matched = _match_files(workspace, pattern)
        hit = None
        for path in matched:
            content = _read(path)
            if all(token in content for token in tokens):
                hit = path
                break
        add(f"file_contains:{pattern}", hit is not None,
            f"tokens {tokens} in {pattern!r}: "
            + (f"found in {hit}" if hit else f"no file contains all tokens ({len(matched)} matched)"))
    for pattern, tokens in dict(spec.get("file_not_contains", {})).items():
        offending = []
        for path in _match_files(workspace, pattern):
            content = _read(path)
            present = [token for token in tokens if token in content]
            if present:
                offending.append(f"{path.name}:{present}")
        add(f"file_not_contains:{pattern}", not offending,
            f"unexpected tokens in {offending}")

    provider_calls = int(turn.get("provider_calls", 0))
    if "provider_calls_max" in spec:
        add("provider_calls_max", provider_calls <= int(spec["provider_calls_max"]),
            f"{provider_calls} calls > {spec['provider_calls_max']}")
    if "provider_calls_min" in spec:
        add("provider_calls_min", provider_calls >= int(spec["provider_calls_min"]),
            f"{provider_calls} calls < {spec['provider_calls_min']}")
    if "plan_calls_max" in spec:
        plan_calls = int(turn.get("plan_calls", 0))
        add("plan_calls_max", plan_calls <= int(spec["plan_calls_max"]),
            f"{plan_calls} plans > {spec['plan_calls_max']}")
    if "replan_calls_max" in spec:
        replan_calls = int(turn.get("replan_calls", 0))
        add("replan_calls_max", replan_calls <= int(spec["replan_calls_max"]),
            f"{replan_calls} replans > {spec['replan_calls_max']}")

    used_tools = {str(call.get("tool", "")) for call in turn.get("tool_calls", [])}
    if spec.get("no_tool_calls"):
        add("no_tool_calls", not used_tools,
            f"tools used: {sorted(used_tools)}")
    for name in _as_list(spec.get("no_tools")):
        add("no_tools", name not in used_tools,
            f"{name!r} was invoked (tools: {sorted(used_tools)})")
    any_tools = _as_list(spec.get("tools_any"))
    if any_tools:
        add("tools_any", bool(used_tools & set(any_tools)),
            f"none of {any_tools} used (tools: {sorted(used_tools)})")
    for name in _as_list(spec.get("tools_all")):
        add("tools_all", name in used_tools,
            f"{name!r} not used (tools: {sorted(used_tools)})")

    intent_spec = spec.get("intent")
    if isinstance(intent_spec, dict) and intent_spec:
        results.extend(_eval_intent(intent_spec, turn.get("intent")))
    return results


# ────────────────────────── case runner ──────────────────────────


def _search_unavailable_reason() -> str:
    try:
        from agent.mcp.duckduckgo import unavailable_reason

        return unavailable_reason()
    except Exception as error:  # registry/import failure also means unavailable
        return f"search availability check failed: {error}"


def _execution_unavailable_reason() -> str:
    """Reason when no isolated execution environment exists.

    Reuses the Runtime's own boundary (agent.sandbox): command execution is
    fail-closed unless Docker is up or local execution is explicitly opted
    in via TSAGENT_ALLOW_LOCAL_EXECUTION.
    """
    from agent import sandbox

    if sandbox._check_docker() or sandbox.local_execution_allowed():
        return ""
    return (
        "Docker is unavailable and "
        f"{sandbox.LOCAL_EXECUTION_ENV} is not set"
    )


def _skip_reason(case: dict, cache: dict) -> str:
    """Evaluate a case's ``skip_if`` condition; "" means do not skip."""
    skip_if = str(case.get("skip_if", ""))
    if not skip_if:
        return ""
    if skip_if not in cache:
        if skip_if == "no_search_provider":
            cache[skip_if] = _search_unavailable_reason()
        elif skip_if == "no_execution_environment":
            cache[skip_if] = _execution_unavailable_reason()
        else:
            cache[skip_if] = ""
    reason = cache[skip_if]
    return f"{skip_if}: {reason}" if reason else ""


def _new_service(database: Path, workspace: Path):
    from agent.service import create_default_agent_service

    return create_default_agent_service(
        database,
        workspace_root=workspace,
        writer_id="core-scenarios",
    )


def _workspace_files(workspace: Path) -> dict:
    files: dict[str, str] = {}
    for path in sorted(workspace.rglob("*")):
        if len(files) >= 50:
            break
        if not path.is_file():
            continue
        if path.name.startswith("runtime.sqlite") or path.name.startswith("."):
            continue
        if "__pycache__" in path.parts:
            continue
        try:
            if path.stat().st_size > 256 * 1024:
                continue
        except OSError:
            continue
        files[str(path.relative_to(workspace))] = _read(path)[:4000]
    return files


async def _run_case(
    case: dict,
    workspace: Path,
    *,
    timeout: float,
    recorder: _Recorder,
) -> dict:
    case_id = str(case["id"])
    workspace.mkdir(parents=True, exist_ok=True)
    for relpath, content in dict(case.get("seed_files", {})).items():
        seed = workspace / relpath
        seed.parent.mkdir(parents=True, exist_ok=True)
        seed.write_text(str(content), encoding="utf-8")

    database = workspace / "runtime.sqlite"
    session_id = f"core-{case_id.lower()}"
    tenant_id = "core-scenarios"
    user_id = "core-user"
    restart_after = int(case.get("restart_after_turn", 0) or 0)

    turns = list(case.get("turns", []))
    turn_records: list[dict] = []
    case_error = ""
    started = time.perf_counter()
    service = _new_service(database, workspace)
    try:
        for index, text in enumerate(turns, 1):
            if restart_after and index == restart_after + 1:
                await service.close()
                service = _new_service(database, workspace)
            turn_records.append(
                await _drive_turn(
                    service,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    session_id=session_id,
                    turn_index=index,
                    request_text=str(text),
                    timeout=timeout,
                    recorder=recorder,
                )
            )
    except Exception as exc:
        case_error = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            await service.close()
        except Exception:
            pass

    files = _workspace_files(workspace)

    check_results: list[dict] = []
    turn_specs = _as_list((case.get("checks") or {}).get("turns"))
    for index, record in enumerate(turn_records):
        spec = turn_specs[index] if index < len(turn_specs) else {}
        for item in _eval_turn_checks(dict(spec or {}), record, workspace):
            item["turn"] = index + 1
            check_results.append(item)

    failed = [item for item in check_results if not item["passed"]]
    if case_error:
        result = "ERROR"
    elif failed:
        result = "FAIL"
    else:
        result = "PASS"
    return {
        "id": case_id,
        "title": str(case.get("title", "")),
        "result": result,
        "skip_reason": "",
        "workspace": str(workspace),
        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        "error": case_error,
        "turns": turn_records,
        "checks": check_results,
        "files": files,
    }


# ────────────────────────── reporting ──────────────────────────


def _summarize(records: list[dict]) -> dict:
    return {
        "total": len(records),
        "passed": sum(1 for r in records if r["result"] == "PASS"),
        "failed": sum(1 for r in records if r["result"] == "FAIL"),
        "errors": sum(1 for r in records if r["result"] == "ERROR"),
        "skipped": sum(1 for r in records if r["result"] == "SKIP"),
        "provider_calls": sum(
            int(t.get("provider_calls", 0))
            for r in records
            for t in r.get("turns", [])
        ),
    }


def _render_markdown(payload: dict) -> str:
    lines = [
        f"# Core Scenarios — {payload['run_id']}",
        "",
        f"- Provider mode: `{payload['provider']}`  Model: `{payload['model']}`",
        f"- Started: {payload['started_at']}",
        f"- Manifest: `{payload['manifest']}`  Cases file: `{payload['cases_file']}`",
        "",
        "Real-provider scenario evidence; not offline proof. Every turn ran "
        "through the configured provider against a fresh SQLite store and a "
        "per-case workspace.",
        "",
        "| Case | Result | Turns | Statuses | Provider calls | Failed checks |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for record in payload["cases"]:
        statuses = ", ".join(
            str(t.get("status", "")) or "?" for t in record["turns"]
        )
        calls = sum(int(t.get("provider_calls", 0)) for t in record["turns"])
        failed = [
            f"t{c['turn']}:{c['check']}"
            for c in record["checks"]
            if not c["passed"]
        ]
        note = record["skip_reason"] or record["error"] or "; ".join(failed)
        lines.append(
            f"| {record['id']} | {record['result']} | {len(record['turns'])} "
            f"| {statuses or '—'} | {calls} | {note or '—'} |"
        )
    summary = payload["summary"]
    lines += [
        "",
        f"**{summary['passed']}/{summary['total']} passed, "
        f"{summary['failed']} failed, {summary['errors']} errors, "
        f"{summary['skipped']} skipped — {summary['provider_calls']} provider calls total.**",
        "",
    ]
    for record in payload["cases"]:
        failed = [c for c in record["checks"] if not c["passed"]]
        if not failed and not record["error"]:
            continue
        lines.append(f"## {record['id']} — {record['title']}")
        if record["error"]:
            lines.append(f"- harness error: `{record['error']}`")
        for item in failed:
            lines.append(
                f"- turn {item['turn']} `{item['check']}`: {item['detail']}"
            )
        lines.append("")
    return "\n".join(lines)


# ────────────────────────── main ──────────────────────────


def _load_cases(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases")
    if not isinstance(cases, list) or not cases:
        raise SystemExit(f"cases file {path} contains no cases")
    return cases


async def _run_all(
    cases: list[dict],
    *,
    workspace_root: Path,
    timeout: float,
) -> list[dict]:
    from agent.bootstrap import load_all

    # Mirror the production CLI bootstrap (tools + skills + capabilities).
    load_all(
        include_workspace=False,
        include_workflows=False,
        include_knowledge=False,
    )
    recorder = _Recorder()
    recorder.install()
    try:
        records = []
        skip_cache: dict = {}
        for case in cases:
            case_id = str(case["id"])
            skip_reason = _skip_reason(case, skip_cache)
            if skip_reason:
                records.append(
                    {
                        "id": case_id,
                        "title": str(case.get("title", "")),
                        "result": "SKIP",
                        "skip_reason": skip_reason,
                        "workspace": "",
                        "duration_ms": 0.0,
                        "error": "",
                        "turns": [],
                        "checks": [],
                        "files": {},
                    }
                )
                print(f"[{case_id}] SKIP ({skip_reason})", flush=True)
                continue
            workspace = workspace_root / case_id.lower()
            print(f"[{case_id}] {case.get('title', '')} ...", flush=True)
            record = await _run_case(
                case, workspace, timeout=timeout, recorder=recorder
            )
            records.append(record)
            n_fail = sum(1 for c in record["checks"] if not c["passed"])
            print(
                f"[{case_id}] -> {record['result']} "
                f"({len(record['turns'])} turns, {n_fail} failed checks, "
                f"{record['duration_ms'] / 1000:.1f}s)",
                flush=True,
            )
        return records
    finally:
        recorder.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="core_scenarios",
        description=(
            "Real-provider multi-turn scenario regression harness. Runs "
            "fixed cases through the configured provider against fresh "
            "SQLite stores and per-case workspaces; writes JSON+MD reports "
            "under realtest_reports/results/."
        ),
    )
    parser.add_argument(
        "--case",
        action="append",
        default=[],
        metavar="ID",
        help="run only the given case id(s); repeatable or comma-separated",
    )
    parser.add_argument(
        "--provider",
        choices=("auto", "deepseek", "ollama"),
        default=None,
        help="override TSAGENT_LLM_PROVIDER for this run",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="list cases and exit (offline, no provider needed)",
    )
    parser.add_argument(
        "--turn-timeout",
        type=float,
        default=240.0,
        help="per-turn terminal-event timeout in seconds (default 240)",
    )
    parser.add_argument(
        "--results-dir",
        default=str(RESULTS_DIR),
        help="directory for reports and per-case workspaces",
    )
    args = parser.parse_args()

    cases = _load_cases(CASES_FILE)
    selected_ids = {
        item.strip()
        for group in args.case
        for item in group.split(",")
        if item.strip()
    }
    if selected_ids:
        known = {str(case["id"]) for case in cases}
        unknown = selected_ids - known
        if unknown:
            raise SystemExit(f"unknown case id(s): {sorted(unknown)}")
        cases = [case for case in cases if str(case["id"]) in selected_ids]

    if args.list:
        for case in cases:
            skip = f" [skip_if={case['skip_if']}]" if case.get("skip_if") else ""
            restart = (
                f" [restart_after_turn={case['restart_after_turn']}]"
                if case.get("restart_after_turn")
                else ""
            )
            print(
                f"{case['id']}: {case.get('title', '')} "
                f"({len(case.get('turns', []))} turns){skip}{restart}"
            )
        return 0

    _load_dotenv()
    if args.provider:
        # Must be set before any agent import binds the router singleton.
        os.environ["TSAGENT_LLM_PROVIDER"] = args.provider
    mode = _provider_mode(args.provider)
    if mode == "ollama":
        # Local inference is an order of magnitude slower than the remote
        # provider; give runs room so time budgets measure behaviour, not
        # machine speed. An explicit env var still wins.
        os.environ.setdefault("TSAGENT_MAX_RUNTIME_SECONDS", "600")
    abort_message = ensure_provider(mode)
    if abort_message:
        print(f"ABORT: {abort_message}", file=sys.stderr)
        return 2

    started_at = datetime.now(timezone.utc)
    run_id = started_at.strftime("core_scenarios_%Y%m%d_%H%M%S")
    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    workspace_root = results_dir / f"{run_id}_workspace"

    records = asyncio.run(
        _run_all(cases, workspace_root=workspace_root, timeout=args.turn_timeout)
    )
    payload = {
        "manifest": MANIFEST,
        "run_id": run_id,
        "provider": mode,
        "model": os.getenv("MODEL_NAME", "deepseek-v4-flash")
        if mode != "ollama"
        else os.getenv("OLLAMA_MODEL", "qwen3:8b"),
        "started_at": started_at.isoformat(),
        "cases_file": CASES_FILE.name,
        "workspace_root": str(workspace_root),
        "cases": records,
        "summary": _summarize(records),
    }
    json_path = results_dir / f"{run_id}.json"
    md_path = results_dir / f"{run_id}.md"
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(_render_markdown(payload), encoding="utf-8")
    print(f"report: {json_path}")
    print(f"report: {md_path}")
    summary = payload["summary"]
    print(
        f"done: {summary['passed']}/{summary['total']} pass, "
        f"{summary['failed']} fail, {summary['errors']} error, "
        f"{summary['skipped']} skip"
    )
    return 1 if summary["failed"] or summary["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
