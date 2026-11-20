"""Regression for the Mercury real-eval judge — pure-function checks,
no model. Guards the two attempted-vs-succeeded holes a probe found:
searched must be a *successful* search, and env-blocked fetch requires an
environment-signed failure (arg errors are the model's mistake)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.real_eval_mercury import (  # noqa: E402
    classify,
    collect_observations,
    compute_checks,
)

SPEC_BODY = {"body_required": True}
SPEC_FETCH = {
    "body_required": True,
    "required_fetch_url": "https://en.wikipedia.org/wiki/DuckDuckGo",
}


def _obs(tool, args, ok, content="", error_code=""):
    return {
        "tool": tool, "input": args,
        "result": {"ok": ok, "content": content, "error_code": error_code},
    }


def test_failed_search_does_not_satisfy_retrieval():
    """A failed web_search leaves the evidence set empty — citing a URL
    the request already contained cannot turn that into capability_met."""
    obs = [_obs(
        "web_search", {"query": "q"}, ok=False,
        content="RESEARCH_TOOL_UNAVAILABLE",
        error_code="TOOL_EXECUTION_FAILED",
    )]
    checks = compute_checks(
        obs, "answer [src](https://a.example/x)",
        "see https://a.example/x", {},
    )
    assert checks["searched"] is False
    assert classify("COMPLETED", checks, {}) == "capability_gap"


def test_arg_error_fetch_is_model_gap_not_environment():
    """INVALID_ARGUMENT on web_fetch is the model's bad call — not an
    environmental block."""
    obs = [_obs(
        "web_fetch", {"url": "not-a-url"}, ok=False,
        content="validation error", error_code="INVALID_ARGUMENT",
    )]
    checks = compute_checks(obs, "", "read https://x.example", SPEC_BODY)
    assert checks["fetch_env_blocked"] is False
    assert classify("COMPLETED", checks, SPEC_BODY) == "capability_gap"


def test_ssrf_signed_fetch_failure_is_environment():
    obs = [_obs(
        "web_fetch", {"url": "https://x.example/p"}, ok=False,
        content="refusing to fetch 'x.example' — it resolves to "
        "non-public address 198.18.0.1; blocks requests to "
        "private/internal addresses to prevent SSRF",
        error_code="TOOL_EXECUTION_FAILED",
    )]
    checks = compute_checks(obs, "", "read https://x.example", SPEC_BODY)
    assert checks["fetch_env_blocked"] is True
    assert classify("COMPLETED", checks, SPEC_BODY) == (
        "provider_or_environment"
    )


def test_successful_fetch_of_required_url_can_meet():
    """Positive control: a real body read of the required URL plus a
    cited evidence URL stays capability_met."""
    obs = [_obs(
        "web_fetch",
        {"url": "https://en.wikipedia.org/wiki/DuckDuckGo"},
        ok=True, content="launched in 2008",
        error_code="",
    )]
    out = "DuckDuckGo launched 2008 — [wiki](https://en.wikipedia.org/wiki/DuckDuckGo)"
    checks = compute_checks(obs, out, "task", SPEC_FETCH)
    assert checks["fetched_required_url"] is True
    assert classify("COMPLETED", checks, SPEC_FETCH) == "capability_met"


def _task_events(pairs):
    """(task_id, tool, verb, target, status, error_code) → typed event
    stream in the executor-path shape (task_started/task_completed)."""
    events = []
    for tid, tool, verb, target, status, code in pairs:
        events.append(("task_started", {
            "executor": "tool", "task_id": tid, "tool": tool,
            "verb": verb, "target": target}))
        events.append(("task_completed", {
            "executor": "tool", "task_id": tid, "status": status,
            "error_code": code, "verified": status == "succeeded"}))
    return events


def test_executor_path_fetch_attempts_are_counted():
    """PlanExecutor tool calls land as task_started/completed pairs, not
    continuation.observations — the evaluator must see them (DeepSeek's
    t1 run fetched every given URL this way and the report showed zero
    fetch attempts). A bare TOOL_EXECUTION_FAILED without error text is
    NOT proof of environment cause — the generic catch-all earns gap."""
    events = _task_events([
        ("loop-1", "web_fetch", "read", "https://a.example/x",
         "failed", "TOOL_EXECUTION_FAILED"),
        ("loop-2", "web_fetch", "read", "https://b.example/y",
         "failed", "TOOL_EXECUTION_FAILED"),
    ])
    obs = collect_observations(events)
    spec = {
        "body_required": True,
        "required_fetch_any_of": ["https://a.example/x"],
    }
    checks = compute_checks(obs, "", "read sources", spec)
    assert checks["fetch_attempted"] is True
    assert checks["required_fetch_attempted"] is True
    assert checks["fetch_env_blocked"] is False
    assert classify("COMPLETED", checks, spec) == "capability_gap"


def test_executor_fetch_env_verdict_grounded_in_stderr_evidence():
    """The SSRF refusal text lives on sidecar stderr, not in
    task_completed. Once real error lines are attached, the same run
    classifies as env-blocked on evidence — not a widened rule."""
    from scripts.real_eval_mercury import attach_executor_error_evidence
    events = _task_events([
        ("loop-1", "web_fetch", "read", "https://a.example/x",
         "failed", "TOOL_EXECUTION_FAILED"),
    ])
    obs = collect_observations(events)
    stderr = (
        "PlanExecutor: step 0 (web_fetch) 失败: Error: Refusing to fetch "
        "https://a.example/x (refusing to fetch 'a.example' — it resolves "
        "to non-public address 198.18.0.9). This server blocks requests "
        "to private/internal addresses to prevent SSRF.\n"
    )
    attach_executor_error_evidence(obs, stderr)
    spec = {
        "body_required": True,
        "required_fetch_any_of": ["https://a.example/x"],
    }
    checks = compute_checks(obs, "", "read sources", spec)
    assert checks["fetch_env_blocked"] is True
    assert classify("COMPLETED", checks, spec) == "provider_or_environment"


def test_stderr_evidence_requires_exact_url_not_host():
    """A refusal line for a.example/a must NOT be credited to a fetch of
    a.example/b — same host is not evidence about a different path.
    Unmatched failures keep 'cause unconfirmed' -> gap, not env."""
    from scripts.real_eval_mercury import attach_executor_error_evidence
    events = _task_events([
        ("loop-1", "web_fetch", "read", "https://a.example/b",
         "failed", "TOOL_EXECUTION_FAILED"),
    ])
    obs = collect_observations(events)
    stderr = (
        "PlanExecutor: step 0 (web_fetch) 失败: Error: Refusing to fetch "
        "https://a.example/a (refusing to fetch 'a.example' — non-public "
        "address 198.18.0.9; SSRF)\n"
    )
    attach_executor_error_evidence(obs, stderr)
    spec = {
        "body_required": True,
        "required_fetch_any_of": ["https://a.example/b"],
    }
    checks = compute_checks(obs, "", "read sources", spec)
    assert checks["fetch_env_blocked"] is False
    assert classify("COMPLETED", checks, spec) == "capability_gap"


def test_any_of_required_url_needs_one_real_body():
    """t1's 'read the given sources' is met only when at least one
    provided URL fetched ok — reading an unrelated page does not count."""
    obs = [_obs(
        "web_fetch", {"url": "https://unrelated.example/p"},
        ok=True, content="body", error_code="",
    )]
    spec = {
        "body_required": True,
        "required_fetch_any_of": ["https://a.example/x", "https://b.example/y"],
    }
    checks = compute_checks(obs, "answer https://a.example/x", "", spec)
    assert checks["fetched_required_url"] is False
    assert classify("COMPLETED", checks, spec) == "capability_gap"


def test_raw_unicode_citation_matches_percent_encoded_source():
    """A source percent-encoded in the request may be cited with raw
    CJK chars — same URL, not a fabrication."""
    provided = "https://n.example/cn/duckduckgo-%E6%90%9C%E7%B4%A2/"
    obs = [_obs(
        "web_fetch", {"url": provided}, ok=True,
        content="body", error_code="",
    )]
    out = "答案见 https://n.example/cn/duckduckgo-搜索/"
    spec = {"body_required": True, "required_fetch_any_of": [provided]}
    checks = compute_checks(obs, out, f"read {provided}", spec)
    assert checks["fetched_required_url"] is True
    assert checks["fabricated_urls"] == []
    assert classify("COMPLETED", checks, spec) == "capability_met"
