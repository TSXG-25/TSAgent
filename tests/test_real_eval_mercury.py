"""Regression for the Mercury real-eval judge — pure-function checks,
no model. Guards the two attempted-vs-succeeded holes a probe found:
searched must be a *successful* search, and env-blocked fetch requires an
environment-signed failure (arg errors are the model's mistake)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.real_eval_mercury import classify, compute_checks  # noqa: E402

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
