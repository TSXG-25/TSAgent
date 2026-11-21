"""Offline contracts for the loop's browser tool family.

Playwright is stubbed: a fake session is pre-bound on the RunContext
(``BrowserSession.for_context`` returns an existing session), and the SSRF
egress boundary itself is covered in tests/test_browser_egress.py.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from agent.browser.egress import EgressBlocked
from agent.llm import ToolCall
from agent.loop.runner import AgentLoop
from agent.loop.toolset import _BROWSER_TOOLS, LoopTool

from tests.test_agent_loop import _mutation_state, _orchestrator


class FakeSession:
    def __init__(self):
        self.calls: list[tuple] = []
        self.closed = False

    async def navigate(self, url):
        self.calls.append(("navigate", url))
        return f"Example — {url}"

    async def snapshot(self, limit=4000):
        self.calls.append(("snapshot",))
        return "UNIQUE-PAGE-BODY-3141"

    async def extract(self, selector, limit=4000):
        self.calls.append(("extract", selector))
        return "extracted"

    async def screenshot(self, path):
        self.calls.append(("screenshot", path))
        return f"shot → {path}"

    async def click(self, selector):
        self.calls.append(("click", selector))
        return f"clicked {selector}"

    async def type_text(self, selector, text):
        self.calls.append(("type", selector, text))
        return f"typed into {selector}"

    async def close(self):
        self.closed = True


def _toolset():
    return {
        name: LoopTool(
            name=name, executor_tool=None, verb=verb, spec=spec,
            risk_level=risk,
        )
        for name, verb, risk, spec in _BROWSER_TOOLS
    }


def _dispatch(orch, name, args, *, index=1, state=None):
    if state is None:
        state = _mutation_state(
            requested_outcomes=["USER_VISIBLE_OUTPUT"], artifact_required=False,
        )

    async def go():
        return await AgentLoop(orch)._dispatch(
            ToolCall(id=f"c{index}", name=name, arguments=args),
            _toolset(), state, index=index,
            run_context=orch.run_context, user_id="u", user_input="浏览",
        )

    return asyncio.run(go()), state


def test_read_tools_run_without_approval(tmp_path):
    """R1 navigate/snapshot reach the session with no approval channel."""
    orch = _orchestrator(tmp_path)
    fake = FakeSession()
    orch.run_context.browser_session = fake
    msg, _ = _dispatch(orch, "browser_navigate", {"url": "https://example.com/"})
    assert "ERROR" not in msg["content"]
    msg, _ = _dispatch(orch, "browser_snapshot", {}, index=2)
    assert msg["content"] == "UNIQUE-PAGE-BODY-3141"
    assert fake.calls == [("navigate", "https://example.com/"), ("snapshot",)]


@pytest.mark.parametrize("name,args", [
    ("browser_click", {"selector": "#buy"}),
    ("browser_type", {"selector": "#q", "text": "hi"}),
])
def test_mutating_tools_fail_closed_without_approval_channel(tmp_path, name, args):
    orch = _orchestrator(tmp_path)
    orch.run_context.run_id = "run-b"
    fake = FakeSession()
    orch.run_context.browser_session = fake
    msg, _ = _dispatch(orch, name, args)
    assert "APPROVAL_UNAVAILABLE" in msg["content"]
    assert fake.calls == []


def test_denied_click_never_reaches_page(tmp_path):
    requests = []

    class Deny:
        async def request_approval(self, run_context, **kwargs):
            requests.append(kwargs)
            return SimpleNamespace(status="DENIED")

    orch = _orchestrator(tmp_path)
    orch.run_context.run_id = "run-b"
    orch.run_context.delegation = Deny()
    fake = FakeSession()
    orch.run_context.browser_session = fake
    msg, state = _dispatch(orch, "browser_click", {"selector": "#buy"})
    assert "APPROVAL_DENIED" in msg["content"]
    assert fake.calls == []
    assert requests[0]["scope"] == "#buy"
    assert requests[0]["risk_level"] == "R2"
    assert state["authorization_denials"][0]["tool"] == "browser_click"


def test_approved_type_reaches_page(tmp_path):
    class Approve:
        async def request_approval(self, run_context, **kwargs):
            return SimpleNamespace(status="APPROVED")

    orch = _orchestrator(tmp_path)
    orch.run_context.run_id = "run-b"
    orch.run_context.delegation = Approve()
    fake = FakeSession()
    orch.run_context.browser_session = fake
    msg, _ = _dispatch(orch, "browser_type", {"selector": "#q", "text": "hi"})
    assert msg["content"] == "typed into #q"
    assert fake.calls == [("type", "#q", "hi")]


def _write_state(*targets):
    return _mutation_state(
        requested_outcomes=["USER_VISIBLE_OUTPUT", "FILE_MUTATION"],
        intent_write_targets=list(targets),
    )


def test_screenshot_hidden_without_file_mutation_grant():
    # Browser tools are only exposed where playwright is importable.
    pytest.importorskip("playwright.async_api")
    from agent.cognition.effect_authorization import EffectAuthorization
    from agent.cognition.execution_need import RequestedOutcome
    from agent.loop.toolset import build_toolset

    read_only = EffectAuthorization(
        requested_outcomes=(RequestedOutcome.USER_VISIBLE_OUTPUT,),
        write_scopes=(),
    )
    names = set(build_toolset(read_only))
    assert "browser_navigate" in names
    assert "browser_screenshot" not in names


def test_screenshot_without_grant_is_denied_and_writes_nothing(tmp_path):
    """Even if the model calls it anyway, the per-call check stops it."""
    orch = _orchestrator(tmp_path)
    fake = FakeSession()
    orch.run_context.browser_session = fake
    msg, state = _dispatch(orch, "browser_screenshot", {"path": "shots/p.png"})
    assert "EFFECT_SCOPE_VIOLATION" in msg["content"]
    assert fake.calls == []
    assert state["authorization_denials"][0]["tool"] == "browser_screenshot"


def test_screenshot_outside_write_scope_is_denied(tmp_path):
    orch = _orchestrator(tmp_path)
    fake = FakeSession()
    orch.run_context.browser_session = fake
    msg, _ = _dispatch(
        orch, "browser_screenshot", {"path": "other/p.png"},
        state=_write_state("shots/p.png"),
    )
    assert "EFFECT_SCOPE_VIOLATION" in msg["content"]
    assert fake.calls == []


def test_screenshot_in_scope_resolves_inside_workspace(tmp_path):
    orch = _orchestrator(tmp_path)
    fake = FakeSession()
    orch.run_context.browser_session = fake
    msg, _ = _dispatch(
        orch, "browser_screenshot", {"path": "shots/page.png"},
        state=_write_state("shots/page.png"),
    )
    assert "ERROR" not in msg["content"]
    target = fake.calls[0][1]
    assert target.startswith(str(tmp_path.resolve()))
    assert target.endswith("shots/page.png")


def test_screenshot_path_escape_is_rejected(tmp_path):
    orch = _orchestrator(tmp_path)
    fake = FakeSession()
    orch.run_context.browser_session = fake
    msg, _ = _dispatch(
        orch, "browser_screenshot", {"path": "../../escape.png"},
        state=_write_state("../../escape.png"),
    )
    assert "ERROR" in msg["content"]
    assert fake.calls == []


def test_run_end_closes_and_unbinds_session(tmp_path):
    orch = _orchestrator(tmp_path)
    fake = FakeSession()
    orch.run_context.browser_session = fake
    loop = AgentLoop(orch)

    async def impl(state, **kwargs):
        raise RuntimeError("boom")

    loop._run_impl = impl
    with pytest.raises(RuntimeError):
        asyncio.run(loop.run({}, user_input="x", user_id="u"))
    assert fake.closed is True
    assert orch.run_context.browser_session is None


def test_blocked_navigate_maps_to_browser_url_blocked(tmp_path):
    """Through the dispatcher the refusal is a distinct, explicit code."""
    orch = _orchestrator(tmp_path)

    class Blocking(FakeSession):
        async def navigate(self, url):
            raise EgressBlocked(f"refusing to load {url}: non-public")

    orch.run_context.browser_session = Blocking()
    msg, _ = _dispatch(orch, "browser_navigate", {"url": "http://127.0.0.1/"})
    assert "ERROR[BROWSER_URL_BLOCKED]" in msg["content"]
