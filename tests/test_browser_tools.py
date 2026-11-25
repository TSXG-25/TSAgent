"""browser_* tools through the executor pipeline — offline contracts.

Playwright is replaced by a fake window via ``resolve_browser``
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
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("playwright.async_api")  # browser tools need playwright

import tests.test_ha_r1_harness as harness  # noqa: E402
from agent.browser.egress import BLOCKED_HEADER, EgressBlocked  # noqa: E402
from agent.browser.window import BrowserRead, BrowserWindow  # noqa: E402
from agent.llm import ToolCall  # noqa: E402
from agent.loop.runner import AgentLoop  # noqa: E402
from agent.loop.toolset import build_toolset  # noqa: E402
from agent.cognition.effect_authorization import EffectAuthorization  # noqa: E402
from agent.service.contracts import StartRunRequest  # noqa: E402
from tests.test_agent_loop import _mutation_state, _orchestrator  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\nFAKE-SCREENSHOT"
PAGE_URL = "https://final.example/article"
PAGE_TEXT = "UNIQUE-PAGE-BODY-3141 lives only in the page body."

_KW = {"view_id": "vw-t", "owner": "run", "scope_key": "run:t"}


def _window(page, proxy):
    window = BrowserWindow(page, proxy, **_KW)
    window.controller = "agent"  # matches manager's run-window creation
    return window


def _window_of(orch):
    return next(iter(orch.run_context.browser_windows.values()))


class FakeSession:
    view_id = "vw-fake"
    doc_generation = 0
    page = SimpleNamespace(url="about:blank")

    @property
    def controller(self):
        return self._controller

    @controller.setter
    def controller(self, value):
        if value != getattr(self, "_controller", None):
            self.control_epoch = getattr(self, "control_epoch", 0) + 1
        self._controller = value

    def __init__(self, run_context=None, *, text=PAGE_TEXT):
        self._controller = "agent"
        self.control_epoch = 0
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
        return BrowserRead(text=self.text[:limit], url=self.url)

    async def extract(self, selector, limit=4000):
        self.calls.append(("extract", selector))
        return BrowserRead(text=self.text[:limit], url=self.url)

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
    orch.run_context.browser_windows = {"vw-fake": FakeSession()}
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
    _window_of(orch).text = "   "
    _call(orch, state, tools, "browser_snapshot", {})
    assert state["plan"][0]["observations"][0]["sources"] == []
    assert state["fresh_evidence"] is False


@pytest.mark.parametrize("name,args", [
    ("browser_snapshot", {}),
    ("browser_extract", {"selector": "article"}),
])
def test_source_stays_bound_to_read_when_page_navigates(tmp_path, name, args):
    orch, state, tools, _ = _loop_env(tmp_path)
    session = _window_of(orch)

    async def read_then_navigate(*args):
        read = BrowserRead(text=PAGE_TEXT, url=PAGE_URL)
        session.url = "https://next.example/"
        return read

    session.snapshot = session.extract = read_then_navigate
    msg = _call(orch, state, tools, name, args)
    assert PAGE_TEXT in msg["content"]
    assert state["plan"][0]["observations"][0]["sources"] == [PAGE_URL]


def test_blocked_url_surfaces_stable_code(tmp_path):
    orch, state, tools, events = _loop_env(tmp_path)

    async def blocked(url):
        raise EgressBlocked(f"BROWSER_URL_BLOCKED: refusing to load {url}")

    _window_of(orch).navigate = blocked
    msg = _call(orch, state, tools, "browser_navigate", {"url": "http://10.0.0.1/"})
    assert "BROWSER_URL_BLOCKED" in msg["content"]
    assert events[-1][1]["status"] == "failed"
    assert events[-1][1]["error_code"] == "BROWSER_URL_BLOCKED"


# --- a refused main document is not a readable source -------------------

class _RefusalPage:
    """Fake page: goto() commits the proxy's marked refusal document at the
    target URL — the same shape Chromium sees when the boundary answers its
    403 page with the BLOCKED_META marker."""

    refusal_text = "Blocked by the TSAgent egress boundary (SSRF protection)"

    def __init__(self):
        self.url = "about:blank"
        self.refused = True

    async def goto(self, url, wait_until=None):
        self.url = url
        return SimpleNamespace(headers={BLOCKED_HEADER.lower(): "1"})

    def on(self, *args, **kwargs):
        pass

    def locator(self, selector):
        return self

    async def evaluate_all(self, script, arg):
        return {
            "url": self.url, "refused": self.refused,
            "text": self.refusal_text,
        }


def _refusal_session():
    """Real BrowserWindow whose page always lands on the refusal doc."""
    proxy = SimpleNamespace(blocked=["refusing to load 10.0.0.1:80"])
    window = BrowserWindow(_RefusalPage(), proxy, **_KW)
    window.controller = "agent"
    return window


@pytest.mark.parametrize("name,args", [
    ("browser_snapshot", {}),
    ("browser_extract", {"selector": "article"}),
])
def test_refused_document_produces_no_source(tmp_path, name, args):
    """navigate → BROWSER_URL_BLOCKED, then a read of the proxy's refusal
    page must fail too — never recorded as a source, never evidence."""
    orch, state, tools, events = _loop_env(tmp_path)
    orch.run_context.browser_windows = {"vw-fake": _refusal_session()}
    _call(orch, state, tools, "browser_navigate", {"url": "http://10.0.0.1/"})
    msg = _call(orch, state, tools, name, args, index=2)
    assert "BROWSER_URL_BLOCKED" in msg["content"]
    # failed calls are attempts, not planned work — the task is removed
    # from state["plan"], so no observation can record the refused URL.
    assert events[-1][1]["status"] == "failed"
    assert events[-1][1]["error_code"] == "BROWSER_URL_BLOCKED"
    assert state["plan"] == []
    assert state["fresh_evidence"] is False


def test_refusal_marker_makes_document_unreadable():
    """The proxy's BLOCKED_META marker identifies the refusal document;
    a normal document stays readable."""
    session = _refusal_session()
    with pytest.raises(EgressBlocked):
        asyncio.run(session.snapshot())
    page = _RefusalPage()
    page.refused = False  # a real page has no marker
    session = BrowserWindow(page, SimpleNamespace(blocked=[]), **_KW)
    assert asyncio.run(session.snapshot()).text == _RefusalPage.refusal_text


def test_read_retries_destroyed_context_and_load_timeout_only():
    from playwright.async_api import Error, TimeoutError

    page = _RefusalPage()
    page.refused = False
    read = {"url": PAGE_URL, "refused": False, "text": PAGE_TEXT}
    page.evaluate_all = AsyncMock(side_effect=[
        Error("Execution context was destroyed"), read,
    ])
    page.wait_for_load_state = AsyncMock(side_effect=TimeoutError("load timed out"))
    session = BrowserWindow(page, None, **_KW)
    assert asyncio.run(session.snapshot()) == BrowserRead(PAGE_TEXT, PAGE_URL)
    assert page.evaluate_all.await_count == 2


def test_read_propagates_unrelated_load_error():
    from playwright.async_api import Error

    page = _RefusalPage()
    page.evaluate_all = AsyncMock(side_effect=Error("Execution context was destroyed"))
    page.wait_for_load_state = AsyncMock(side_effect=Error("UNRELATED_BROKEN_PROTOCOL"))
    session = BrowserWindow(page, None, **_KW)
    with pytest.raises(Error, match="UNRELATED_BROKEN_PROTOCOL"):
        asyncio.run(session.snapshot())
    assert page.evaluate_all.await_count == 1


def test_read_propagates_unrelated_read_error():
    from playwright.async_api import Error

    page = _RefusalPage()
    page.evaluate_all = AsyncMock(side_effect=Error("Target page has been closed"))
    page.wait_for_load_state = AsyncMock()
    session = BrowserWindow(page, None, **_KW)
    with pytest.raises(Error, match="Target page has been closed"):
        asyncio.run(session.snapshot())
    assert page.evaluate_all.await_count == 1
    page.wait_for_load_state.assert_not_awaited()


class _AbortPage(_RefusalPage):
    """goto() commits Chromium's error page, then fails — the shape of any
    aborted navigation (refused CONNECT, DNS, …).  `on_navigate` runs
    inside goto, like the proxy recording a refusal in flight."""

    error = "net::ERR_NAME_NOT_RESOLVED"
    on_navigate = None

    def __init__(self):
        super().__init__()
        self.refused = False

    async def goto(self, url, wait_until=None):
        if self.on_navigate is not None:
            self.on_navigate()
        self.url = "chrome-error://chromewebdata/"
        raise RuntimeError(self.error)


def test_aborted_navigation_document_is_not_readable():
    session = BrowserWindow(_AbortPage(), SimpleNamespace(blocked=[]), **_KW)
    with pytest.raises(RuntimeError, match="ERR_NAME_NOT_RESOLVED"):
        asyncio.run(session.navigate("http://missing.invalid/"))
    with pytest.raises(RuntimeError, match="navigation error page"):
        asyncio.run(session.snapshot())


def test_refused_connect_leaves_no_readable_document():
    proxy = SimpleNamespace(blocked=[])
    page = _AbortPage()
    page.error = "net::ERR_TUNNEL_CONNECTION_FAILED"
    page.on_navigate = lambda: proxy.blocked.append(
        "refusing to load 10.0.0.1:443")
    session = BrowserWindow(page, proxy, **_KW)

    async def go():
        # the caller still gets the boundary's reason …
        with pytest.raises(EgressBlocked):
            await session.navigate("https://10.0.0.1/")
        # … and the committed error page is not a readable document
        with pytest.raises(RuntimeError, match="navigation error page"):
            await session.snapshot()

    asyncio.run(go())


@pytest.mark.parametrize("name,args", [
    ("browser_snapshot", {}),
    ("browser_extract", {"selector": "article"}),
])
def test_error_document_produces_no_source(tmp_path, name, args):
    """Allowed page → 302/click → refused hop commits the error document;
    reading it must fail, never producing sources or evidence."""
    orch, state, tools, events = _loop_env(tmp_path)
    proxy = SimpleNamespace(blocked=[])
    page = _AbortPage()
    page.error = "net::ERR_TUNNEL_CONNECTION_FAILED"
    page.on_navigate = lambda: proxy.blocked.append(
        "refusing to load 10.0.0.1:443")
    orch.run_context.browser_windows = {"vw-fake": _window(page, proxy)}
    _call(orch, state, tools, "browser_navigate", {"url": "http://allowed.example/"})
    msg = _call(orch, state, tools, name, args, index=2)
    assert "navigation error page" in msg["content"]
    assert events[-1][1]["status"] == "failed"
    assert state["plan"] == []
    assert state["fresh_evidence"] is False


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
    assert _window_of(orch).calls == []
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
    assert _window_of(orch).calls == []
    assert events == []


def test_denied_click_has_no_side_effect(tmp_path):
    orch, state, tools, events = _loop_env(tmp_path)
    orch.run_context.delegation = _Approvals("DENIED")
    msg = _call(orch, state, tools, "browser_click", {"selector": "#buy"})
    assert "APPROVAL_DENIED" in msg["content"]
    assert _window_of(orch).calls == []
    assert events == []
    assert orch.run_context.delegation.requests[0]["action"] == "browser.click"
    assert orch.run_context.delegation.requests[0]["risk_level"] == "R2"


def test_approved_click_records_success(tmp_path):
    orch, state, tools, events = _loop_env(tmp_path)
    orch.run_context.delegation = _Approvals("APPROVED")
    msg = _call(orch, state, tools, "browser_click", {"selector": "#buy"})
    assert msg["content"] == "clicked #buy"
    assert _window_of(orch).calls == [("click", "#buy")]
    assert events[-1][1]["status"] == "succeeded"
    assert state["inbox"]["next_step"][-1]["result"]["ok"] is True


def test_approved_click_failure_is_recorded(tmp_path):
    orch, state, tools, events = _loop_env(tmp_path)
    orch.run_context.delegation = _Approvals("APPROVED")
    _window_of(orch).click_error = RuntimeError(
        "Timeout 30000ms exceeded waiting for selector #buy")
    msg = _call(orch, state, tools, "browser_click", {"selector": "#buy"})
    assert "ERROR" in msg["content"]
    assert events[-1][1]["status"] == "failed"
    assert state["inbox"]["next_step"][-1]["result"]["ok"] is False


def test_run_end_closes_and_unbinds_session(tmp_path):
    orch = _orchestrator(tmp_path)
    fake = FakeSession()
    orch.run_context.browser_windows = {"vw-fake": fake}
    loop = AgentLoop(orch)

    async def impl(state, **kwargs):
        raise asyncio.CancelledError()

    loop._run_impl = impl
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(loop.run({}, user_input="x", user_id="u"))
    assert fake.closed is True
    assert orch.run_context.browser_windows == {}


# --- service-level E2E (real runtime, scripted provider) ------------------

class _Opened(list):
    gates: list


@pytest.fixture
def sessions(monkeypatch):
    """Patch the executor's window resolver: one fake window per Run."""
    opened = _Opened()
    opened_gates: list[asyncio.Event] = []  # gates for the next sessions

    async def resolve(run_context, view_id=None):
        windows = run_context.browser_windows
        if view_id is not None:
            return windows[view_id]
        if not windows:
            session = FakeSession(run_context)
            if opened_gates:
                session.gate = opened_gates.pop(0)
            opened.append(session)
            windows["vw-fake"] = session
        return next(iter(windows.values()))

    monkeypatch.setattr(
        "agent.orchestrator.executor.resolve_browser", resolve)
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


def test_takeover_blocks_agent_input(tmp_path):
    """MB-06: a window under user control refuses injected actions —
    approved or not, no selector is ever clicked."""
    orch, state, tools, events = _loop_env(tmp_path)
    orch.run_context.delegation = _Approvals("APPROVED")
    _window_of(orch).controller = "user"
    msg = _call(orch, state, tools, "browser_click", {"selector": "#buy"})
    assert "WINDOW_NOT_UNDER_AGENT_CONTROL" in msg["content"]
    assert _window_of(orch).calls == []


def test_approval_invalidated_by_manual_navigation(tmp_path):
    """MB-06: a manual navigation while approval pends invalidates the
    grant — the approved click never reaches the page."""
    class _NavigatingApprovals(_Approvals):
        async def request_approval(self, run_context, **kwargs):
            run_context.browser_windows["vw-fake"].doc_generation += 1
            return await super().request_approval(run_context, **kwargs)

    orch, state, tools, events = _loop_env(tmp_path)
    orch.run_context.delegation = _NavigatingApprovals("APPROVED")
    msg = _call(
        orch, state, tools, "browser_click",
        {"selector": "#buy", "view_id": "vw-fake"},
    )
    assert "APPROVAL_STALE" in msg["content"]
    assert _window_of(orch).calls == []


def test_approval_invalidated_by_takeover(tmp_path):
    class _TakingApprovals(_Approvals):
        async def request_approval(self, run_context, **kwargs):
            run_context.browser_windows["vw-fake"].controller = "user"
            return await super().request_approval(run_context, **kwargs)

    orch, state, tools, events = _loop_env(tmp_path)
    orch.run_context.delegation = _TakingApprovals("APPROVED")
    msg = _call(
        orch, state, tools, "browser_click",
        {"selector": "#buy", "view_id": "vw-fake"},
    )
    assert "APPROVAL_STALE" in msg["content"]
    assert _window_of(orch).calls == []


def test_approval_invalidated_without_explicit_view_id(tmp_path):
    """Single bound window omits view_id — the approval still binds the
    actual resolved target, so a doc change during the wait is stale."""
    class _NavigatingApprovals(_Approvals):
        async def request_approval(self, run_context, **kwargs):
            run_context.browser_windows["vw-fake"].doc_generation += 1
            return await super().request_approval(run_context, **kwargs)

    orch, state, tools, events = _loop_env(tmp_path)
    orch.run_context.delegation = _NavigatingApprovals("APPROVED")
    msg = _call(orch, state, tools, "browser_click", {"selector": "#buy"})
    assert "APPROVAL_STALE" in msg["content"]
    assert _window_of(orch).calls == []


def test_approval_invalidated_by_takeover_release_roundtrip(tmp_path):
    """controller goes agent→user→agent during the wait — the epoch
    still moved, the grant is void."""
    class _RoundTrip(_Approvals):
        async def request_approval(self, run_context, **kwargs):
            w = run_context.browser_windows["vw-fake"]
            w.controller = "user"
            w.controller = "agent"
            return await super().request_approval(run_context, **kwargs)

    orch, state, tools, events = _loop_env(tmp_path)
    orch.run_context.delegation = _RoundTrip("APPROVED")
    _window_of(orch).control_epoch = 0
    msg = _call(
        orch, state, tools, "browser_click",
        {"selector": "#buy", "view_id": "vw-fake"},
    )
    assert "APPROVAL_STALE" in msg["content"]
    assert _window_of(orch).calls == []
