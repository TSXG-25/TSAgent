"""browser_* tools through the executor pipeline — offline contracts.

Playwright is replaced by ``FakeSession`` via ``BrowserSession.for_context``
(the Run-scoped factory the ExecutionStage hands to the tool layer), so the
real path runs: AgentLoop dispatch → authorization → approval gate →
ExecutionStage → ToolExecutor → PlanExecutor → verifier → evidence.

Acceptance covered:
1. a model-issued screenshot is written, emits task events, yields
   FILE_MUTATION evidence and an artifact, and the goal passes;
2. only page text actually read (snapshot/extract) becomes source evidence
   with the page's real URL — navigation alone does not;
3. an approved click leaves a success/failure execution record; a denied
   one has no side effect;
4. two Runs never share a browser; each is closed when its run ends,
   including on cancellation.
The egress (SSRF) boundary is covered in tests/test_browser_egress.py.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("playwright.async_api")  # browser tools need playwright

import tests.test_ha_r1_harness as harness  # noqa: E402
from agent.browser.egress import EgressBlocked  # noqa: E402
from agent.browser.session import BrowserSession  # noqa: E402
from agent.llm import ToolCall  # noqa: E402
from agent.loop.runner import AgentLoop  # noqa: E402
from agent.loop.toolset import build_toolset  # noqa: E402
from agent.cognition.effect_authorization import EffectAuthorization  # noqa: E402
from agent.service.contracts import StartRunRequest  # noqa: E402
from tests.test_agent_loop import _mutation_state, _orchestrator  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\nFAKE-SCREENSHOT"
PAGE_URL = "https://final.example/article"
PAGE_TEXT = "UNIQUE-PAGE-BODY-3141 lives only in the page body."


class FakeSession:
    def __init__(self, run_context=None, *, text=PAGE_TEXT):
        self.run_context = run_context
        self.calls: list[tuple] = []
        self.closed = False
        self.text = text
        self.url = "about:blank"
        self.click_error: Exception | None = None
        self.gate: asyncio.Event | None = None

    async def navigate(self, url):
        self.calls.append(("navigate", url))
        if self.gate is not None:
            await self.gate.wait()
        self.url = PAGE_URL  # redirected
        return f"Article — {self.url}"

    async def snapshot(self, limit=4000):
        self.calls.append(("snapshot",))
        return self.text

    async def extract(self, selector, limit=4000):
        self.calls.append(("extract", selector))
        return self.text

    async def screenshot(self):
        self.calls.append(("screenshot",))
        return PNG

    async def click(self, selector):
        self.calls.append(("click", selector))
        if self.click_error is not None:
            raise self.click_error
        return f"clicked {selector}"

    async def type_text(self, selector, text):
        self.calls.append(("type", selector, text))
        return f"typed into {selector}"

    async def close(self):
        self.closed = True


# --- dispatch-level contracts (real ExecutionStage) ------------------------

def _loop_env(tmp_path, *, outcomes=("USER_VISIBLE_OUTPUT",), targets=()):
    orch = _orchestrator(tmp_path)
    orch.run_context.run_id = "run-b"
    orch.run_context.browser_session = FakeSession()
    events: list[tuple[str, dict]] = []
    for kind in ("task_started", "task_completed"):
        orch.run_context.event_bus.subscribe(
            kind, lambda data, kind=kind: events.append((kind, data)))
    state = _mutation_state(
        requested_outcomes=list(outcomes),
        artifact_required=False,
        intent_write_targets=list(targets),
    )
    tools = build_toolset(EffectAuthorization.from_state(state, "浏览"))
    return orch, state, tools, events


def _call(orch, state, tools, name, args, index=1):
    return asyncio.run(AgentLoop(orch)._dispatch(
        ToolCall(id=f"c{index}", name=name, arguments=args), tools, state,
        index=index, run_context=orch.run_context, user_id="u",
        user_input="浏览",
    ))


def test_navigate_runs_through_executor_but_is_not_source_evidence(tmp_path):
    orch, state, tools, events = _loop_env(tmp_path)
    msg = _call(orch, state, tools, "browser_navigate",
                {"url": "https://start.example/"})
    assert "ERROR" not in msg["content"]
    assert [k for k, _ in events] == ["task_started", "task_completed"]
    assert events[0][1]["tool"] == "browser.navigate"
    assert events[1][1]["status"] == "succeeded"
    assert state["plan"][0]["observations"][0]["sources"] == []
    assert state["fresh_evidence"] is False


@pytest.mark.parametrize("name,args", [
    ("browser_snapshot", {}),
    ("browser_extract", {"selector": "article"}),
])
def test_read_text_records_actual_page_url_as_source(tmp_path, name, args):
    orch, state, tools, _ = _loop_env(tmp_path)
    _call(orch, state, tools, "browser_navigate", {"url": "https://start.example/"})
    msg = _call(orch, state, tools, name, args, index=2)
    assert "UNIQUE-PAGE-BODY-3141" in msg["content"]
    read_obs = state["plan"][1]["observations"][0]
    assert read_obs["sources"] == [PAGE_URL]  # after redirect, not the input URL
    assert state["fresh_evidence"] is True


def test_empty_page_text_is_not_source_evidence(tmp_path):
    orch, state, tools, _ = _loop_env(tmp_path)
    orch.run_context.browser_session.text = "   "
    _call(orch, state, tools, "browser_snapshot", {})
    assert state["plan"][0]["observations"][0]["sources"] == []
    assert state["fresh_evidence"] is False


def test_blocked_url_surfaces_stable_code(tmp_path):
    orch, state, tools, events = _loop_env(tmp_path)

    async def blocked(url):
        raise EgressBlocked(f"BROWSER_URL_BLOCKED: refusing to load {url}")

    orch.run_context.browser_session.navigate = blocked
    msg = _call(orch, state, tools, "browser_navigate", {"url": "http://10.0.0.1/"})
    assert "BROWSER_URL_BLOCKED" in msg["content"]
    assert events[-1][1]["status"] == "failed"
    assert events[-1][1]["error_code"] == "BROWSER_URL_BLOCKED"


def test_screenshot_writes_verified_file_with_evidence(tmp_path):
    orch, state, tools, events = _loop_env(
        tmp_path, outcomes=("USER_VISIBLE_OUTPUT", "FILE_MUTATION"),
        targets=("shots/page.png",),
    )
    msg = _call(orch, state, tools, "browser_screenshot", {"path": "shots/page.png"})
    assert "ERROR" not in msg["content"]
    assert (tmp_path / "shots/page.png").read_bytes() == PNG
    assert events[-1][1]["verb"] == "write"
    assert events[-1][1]["verified"] is True
    assert any(
        item["outcome"] == "FILE_MUTATION" and item["target"] == "shots/page.png"
        for item in state["execution_evidence"]
    )


def test_screenshot_hidden_and_refused_without_write_grant(tmp_path):
    orch, state, tools, events = _loop_env(tmp_path)
    assert "browser_screenshot" not in tools
    # Even if the model calls it anyway with a hand-built catalog entry.
    full = build_toolset(EffectAuthorization.from_state(
        _mutation_state(intent_write_targets=["x.png"]), "x"))
    msg = _call(orch, state, {"browser_screenshot": full["browser_screenshot"]},
                "browser_screenshot", {"path": "shots/p.png"})
    assert "EFFECT_SCOPE_VIOLATION" in msg["content"]
    assert not (tmp_path / "shots/p.png").exists()
    assert events == []


def test_screenshot_outside_write_scope_is_refused(tmp_path):
    orch, state, tools, events = _loop_env(
        tmp_path, outcomes=("USER_VISIBLE_OUTPUT", "FILE_MUTATION"),
        targets=("shots/ok.png",),
    )
    msg = _call(orch, state, tools, "browser_screenshot", {"path": "other/p.png"})
    assert "EFFECT_SCOPE_VIOLATION" in msg["content"]
    assert orch.run_context.browser_session.calls == []
    assert events == []


class _Approvals:
    def __init__(self, status):
        self.status = status
        self.requests = []

    async def request_approval(self, run_context, **kwargs):
        self.requests.append(kwargs)
        return SimpleNamespace(status=self.status)


def test_click_without_approval_channel_fails_closed(tmp_path):
    orch, state, tools, events = _loop_env(tmp_path)
    msg = _call(orch, state, tools, "browser_click", {"selector": "#buy"})
    assert "APPROVAL_UNAVAILABLE" in msg["content"]
    assert orch.run_context.browser_session.calls == []
    assert events == []


def test_denied_click_has_no_side_effect(tmp_path):
    orch, state, tools, events = _loop_env(tmp_path)
    orch.run_context.delegation = _Approvals("DENIED")
    msg = _call(orch, state, tools, "browser_click", {"selector": "#buy"})
    assert "APPROVAL_DENIED" in msg["content"]
    assert orch.run_context.browser_session.calls == []
    assert events == []
    assert orch.run_context.delegation.requests[0]["action"] == "browser.click"
    assert orch.run_context.delegation.requests[0]["risk_level"] == "R2"


def test_approved_click_records_success(tmp_path):
    orch, state, tools, events = _loop_env(tmp_path)
    orch.run_context.delegation = _Approvals("APPROVED")
    msg = _call(orch, state, tools, "browser_click", {"selector": "#buy"})
    assert msg["content"] == "clicked #buy"
    assert orch.run_context.browser_session.calls == [("click", "#buy")]
    assert events[-1][1]["status"] == "succeeded"
    assert state["inbox"]["next_step"][-1]["result"]["ok"] is True


def test_approved_click_failure_is_recorded(tmp_path):
    orch, state, tools, events = _loop_env(tmp_path)
    orch.run_context.delegation = _Approvals("APPROVED")
    orch.run_context.browser_session.click_error = RuntimeError(
        "Timeout 30000ms exceeded waiting for selector #buy")
    msg = _call(orch, state, tools, "browser_click", {"selector": "#buy"})
    assert "ERROR" in msg["content"]
    assert events[-1][1]["status"] == "failed"
    assert state["inbox"]["next_step"][-1]["result"]["ok"] is False


def test_run_end_closes_and_unbinds_session(tmp_path):
    orch = _orchestrator(tmp_path)
    fake = FakeSession()
    orch.run_context.browser_session = fake
    loop = AgentLoop(orch)

    async def impl(state, **kwargs):
        raise asyncio.CancelledError()

    loop._run_impl = impl
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(loop.run({}, user_input="x", user_id="u"))
    assert fake.closed is True
    assert orch.run_context.browser_session is None


# --- service-level E2E (real runtime, scripted provider) ------------------

class _Opened(list):
    gates: list


@pytest.fixture
def sessions(monkeypatch):
    """Patch the Run-scoped factory: one FakeSession per RunContext."""
    opened = _Opened()
    opened_gates: list[asyncio.Event] = []  # gates for the next sessions

    async def for_context(cls, run_context):
        if run_context.browser_session is None:
            session = FakeSession(run_context)
            if opened_gates:
                session.gate = opened_gates.pop(0)
            opened.append(session)
            run_context.browser_session = session
        return run_context.browser_session

    monkeypatch.setattr(BrowserSession, "for_context", classmethod(for_context))
    opened.gates = opened_gates  # type: ignore[attr-defined]
    return opened


async def _wait_terminal(service, tenant, session, run_id, timeout=60.0):
    for _ in range(int(timeout / 0.05)):
        head = service._store.get_run_head(tenant, run_id, session_id=session)
        status = getattr(head, "run_status", "") or ""
        if status.startswith(("COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT")):
            return head
        await asyncio.sleep(0.05)
    raise AssertionError(f"run {run_id} did not reach a terminal status")


def _service(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    service, store = harness.service_at(tmp_path)
    store.ensure_session("t", "s1", "u")
    store.ensure_session("t", "s2", "u")
    store.upsert_workspace("t", "ws", path=str(workspace))
    store.assign_session_workspace("t", "s1", "ws")
    store.assign_session_workspace("t", "s2", "ws")
    return service, store, workspace


SHOT = "打开 https://start.example/ 截图保存到 shots/page.png"


def test_e2e_model_screenshot_completes_with_artifact(monkeypatch, tmp_path, sessions):
    semantics = harness.intent(
        SHOT, artifact=True, outcomes=["FILE_MUTATION"],
        write_targets=["shots/page.png"],
    )
    semantics["delivery_requirements"][0]["target"] = "shots/page.png"
    harness.setup_provider(monkeypatch, [
        (SHOT, semantics),
        (SHOT, harness.call_tool("browser_navigate", url="https://start.example/")),
        ("Article", harness.call_tool("browser_screenshot", path="shots/page.png")),
        ("shots/page.png", "已截图保存到 shots/page.png"),
    ])

    async def scenario():
        service, store, workspace = _service(tmp_path)
        try:
            run = await service.start_run(StartRunRequest(
                tenant_id="t", user_id="u", session_id="s1",
                request_id="r1", request_text=SHOT, origin_planet="mercury"))
            head = await _wait_terminal(service, "t", "s1", run.run_id)
            assert head.run_status == "COMPLETED", head
            assert (workspace / "shots/page.png").read_bytes() == PNG
            from agent.service import EventStreamRequest, RunLookupRequest

            events = [e async for e in service.stream_events(EventStreamRequest(
                tenant_id="t", user_id="u", session_id="s1",
                run_id=run.run_id, request_id="ev"))]
            assert any(
                e.event_type.value == "task_completed"
                and e.payload.get("verb") == "write"
                and e.payload.get("verified") is True
                for e in events
            )
            snap = await service.get_run(RunLookupRequest(
                tenant_id="t", user_id="u", session_id="s1",
                run_id=run.run_id, request_id="lk"))
            import base64
            import hashlib

            from agent.service.contracts import ReadArtifactRequest

            shot = [a for a in snap.artifacts
                    if a.digest == hashlib.sha256(PNG).hexdigest()]
            assert shot and shot[0].verified and shot[0].exists
            preview = await service.read_artifact_preview(ReadArtifactRequest(
                tenant_id="t", user_id="u", session_id="s1", run_id=run.run_id,
                request_id="pv", artifact_id=shot[0].artifact_id))
            assert preview.result == "ok" and preview.mime == "image/png"
            assert base64.b64decode(preview.data_b64) == PNG
        finally:
            await service.close()

    asyncio.run(scenario())
    assert len(sessions) == 1 and sessions[0].closed


GROUNDED = "读取 https://start.example/ 的正文并标注来源"


def _grounded_semantics():
    return harness.intent(GROUNDED, source=True, action="read",
                          target="https://start.example/")


def test_e2e_grounded_read_needs_page_text_not_navigation(monkeypatch, tmp_path, sessions):
    harness.setup_provider(monkeypatch, [
        (GROUNDED, _grounded_semantics()),
        (GROUNDED, harness.call_tool("browser_navigate", url="https://start.example/")),
        ("Article", harness.call_tool("browser_snapshot")),
        ("UNIQUE-PAGE-BODY-3141", f"正文要点 UNIQUE-PAGE-BODY-3141 [来源]({PAGE_URL})"),
    ])

    async def scenario():
        service, store, _ = _service(tmp_path)
        try:
            run = await service.start_run(StartRunRequest(
                tenant_id="t", user_id="u", session_id="s1",
                request_id="r1", request_text=GROUNDED, origin_planet="mercury"))
            head = await _wait_terminal(service, "t", "s1", run.run_id)
            assert head.run_status == "COMPLETED", head
            out = store.read_run_snapshot("t", run.run_id, session_id="s1").output
            assert PAGE_URL in out.text
        finally:
            await service.close()

    asyncio.run(scenario())


def test_e2e_navigation_only_does_not_satisfy_grounding(monkeypatch, tmp_path, sessions):
    """Answering after navigation alone is refused by the grounding gate;
    the run completes only once page text is actually read."""
    provider = harness.setup_provider(monkeypatch, [
        (GROUNDED, _grounded_semantics()),
        (GROUNDED, harness.call_tool("browser_navigate", url="https://start.example/")),
        ("Article", f"页面是 Article [来源]({PAGE_URL})"),
        ("fresh external evidence", harness.call_tool("browser_snapshot")),
        ("UNIQUE-PAGE-BODY-3141", f"正文要点 UNIQUE-PAGE-BODY-3141 [来源]({PAGE_URL})"),
    ])

    async def scenario():
        service, store, _ = _service(tmp_path)
        try:
            run = await service.start_run(StartRunRequest(
                tenant_id="t", user_id="u", session_id="s1",
                request_id="r1", request_text=GROUNDED, origin_planet="mercury"))
            head = await _wait_terminal(service, "t", "s1", run.run_id)
            assert head.run_status == "COMPLETED", head
        finally:
            await service.close()

    asyncio.run(scenario())
    gap_calls = [c for c in provider.calls if "尚未满足: fresh external evidence" in c]
    assert gap_calls, "navigation-only answer was not refused by the grounding gate"
    assert not provider.replies


def test_e2e_two_runs_get_separate_browsers_closed_on_cancel(monkeypatch, tmp_path, sessions):
    """Run A parks inside navigate; Run B navigates and completes on its own
    page. A is then cancelled; both browsers end closed and never shared."""
    req_a, req_b = "A 打开 https://a.example/", "B 打开 https://b.example/"
    harness.setup_provider(monkeypatch, [
        (req_a, harness.intent(req_a, action="read", source=True)),
        (req_a, harness.call_tool("browser_navigate", url="https://a.example/")),
        (req_b, harness.intent(req_b, action="read", source=True)),
        (req_b, harness.call_tool("browser_navigate", url="https://b.example/")),
        ("Article", harness.call_tool("browser_snapshot")),
        ("UNIQUE-PAGE-BODY-3141", f"B 正文 [来源]({PAGE_URL})"),
    ])
    async def scenario():
        from agent.interruption import CancelRunRequest

        gate = asyncio.Event()
        sessions.gates.append(gate)  # Run A's page blocks inside navigate
        service, store, _ = _service(tmp_path)
        try:
            run_a = await service.start_run(StartRunRequest(
                tenant_id="t", user_id="u", session_id="s1",
                request_id="ra", request_text=req_a, origin_planet="mercury"))
            for _ in range(600):
                if sessions and sessions[0].calls:
                    break
                await asyncio.sleep(0.05)
            assert sessions[0].calls, "run A never reached the browser"
            run_b = await service.start_run(StartRunRequest(
                tenant_id="t", user_id="u", session_id="s2",
                request_id="rb", request_text=req_b, origin_planet="mercury"))
            head_b = await _wait_terminal(service, "t", "s2", run_b.run_id)
            assert head_b.run_status == "COMPLETED", head_b
            assert sessions[1].closed and not sessions[0].closed
            await service.cancel_run(CancelRunRequest(
                tenant_id="t", user_id="u", session_id="s1",
                run_id=run_a.run_id, request_id="ca", requested_by="u"))
            gate.set()
            head_a = await _wait_terminal(service, "t", "s1", run_a.run_id)
            assert head_a.run_status == "CANCELLED", head_a
        finally:
            await service.close()

    asyncio.run(scenario())
    assert len(sessions) == 2
    a, b = sessions
    assert a.run_context is not b.run_context
    assert a.calls == [("navigate", "https://a.example/")]
    assert b.calls == [("navigate", "https://b.example/"), ("snapshot",)]
    assert a.closed and b.closed
