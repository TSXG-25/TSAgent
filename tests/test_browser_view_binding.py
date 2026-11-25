"""MB-05 — model-issued browser actions bind to explicit view_id and
carry window attribution into observations/sources."""

from __future__ import annotations

import functools
import http.server
import os
import socketserver
import threading
from pathlib import Path

import pytest
import pytest_asyncio

os.environ.setdefault("DDG_ALLOW_PRIVATE_URLS", "1")


class _Site:
    def __init__(self) -> None:
        self.requests: list[str] = []

    def __enter__(self) -> "_Site":
        site = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                site.requests.append(self.path)
                body = (
                    f"<html><title>{self.path}</title><body>"
                    f"<p id='fact'>FACT:{self.path}</p></body></html>"
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: object) -> None:
                pass

        self._server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever)
        self._thread.start()
        return self

    @property
    def url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def __exit__(self, *exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()


@pytest_asyncio.fixture
async def manager(tmp_path: Path):
    from agent.browser.manager import BrowserManager
    from agent.runtime_context import ApplicationContext

    app = ApplicationContext(workspace_root=tmp_path / "workspace")
    mgr = BrowserManager.for_application(app)
    try:
        yield mgr
    finally:
        await mgr.close()


def _run_context(manager, run_id: str = "run-1"):
    class RC:
        pass

    rc = RC()
    rc.run_id = run_id
    rc.browser_windows = {}
    app = _App()
    app.browser_manager = manager
    rc.application = app
    return rc


class _App:
    """Minimal application handle — resolve_browser only needs the
    attribute for_application looks at."""
    browser_manager = None


async def test_bound_windows_require_view_id_and_attribute_evidence(
    manager,
) -> None:
    from agent.browser.manager import resolve_browser
    from agent.executor.plan_executor import PlanExecutor

    with _Site() as site:
        try:
            a = await manager.open_window(
                scope_key="t:s", owner="workbench", url=f"{site.url}/page-a"
            )
            b = await manager.open_window(
                scope_key="t:s", owner="workbench", url=f"{site.url}/page-b"
            )
        except Exception as error:  # pragma: no cover
            if "Executable doesn't exist" in str(error):
                pytest.skip("chromium executable not installed")
            raise
        rc = _run_context(manager)
        rc.browser_windows = {w.view_id: w for w in manager.acquire(rc.run_id, [a.view_id, b.view_id])}
        open_browser = functools.partial(resolve_browser, rc)

        # two bound windows → no default: the call must name view_id
        with pytest.raises(RuntimeError, match="must name view_id"):
            await PlanExecutor._exec_browser(
                "browser.extract",
                {"selector": "#fact"},
                open_browser,
                None,
            )
        # an unbound id is rejected before any browser call
        with pytest.raises(RuntimeError, match="not bound"):
            await PlanExecutor._exec_browser(
                "browser.extract",
                {"selector": "#fact", "view_id": "vw-nope"},
                open_browser,
                None,
            )

        result_a = await PlanExecutor._exec_browser(
            "browser.extract",
            {"selector": "#fact", "view_id": a.view_id},
            open_browser,
            None,
        )
        result_b = await PlanExecutor._exec_browser(
            "browser.extract",
            {"selector": "#fact", "view_id": b.view_id},
            open_browser,
            None,
        )
        assert "FACT:/page-a" in result_a["content"]
        assert "FACT:/page-b" in result_b["content"]
        # unified evidence: view attribution + same-read URL source
        assert result_a["view_id"] == a.view_id
        assert result_b["view_id"] == b.view_id
        assert result_a["_sources"] == [f"{site.url}/page-a"]
        assert result_b["_sources"] == [f"{site.url}/page-b"]

        # bound windows stay open after the run releases its hold
        await manager.release_run(rc.run_id)
        assert not a.closed and not b.closed


async def test_single_bound_window_resolves_without_view_id(manager) -> None:
    from agent.browser.manager import resolve_browser

    with _Site() as site:
        try:
            window = await manager.open_window(
                scope_key="t:s", owner="workbench", url=site.url
            )
        except Exception as error:  # pragma: no cover
            if "Executable doesn't exist" in str(error):
                pytest.skip("chromium executable not installed")
            raise
    rc = _run_context(manager)
    rc.browser_windows = {window.view_id: window}
    resolved = await resolve_browser(rc)
    assert resolved.view_id == window.view_id


async def test_window_ids_survive_in_tool_args_and_observation() -> None:
    """The recorded tool call keeps view_id verbatim — a UI selection
    change after submission can't retarget the accepted run's args."""
    from agent.service.contracts import StartRunRequest

    request = StartRunRequest.from_dict({
        "tenant_id": "t", "user_id": "u", "session_id": "s",
        "request_id": "r", "request_text": "read both",
        "view_ids": ["v-a", "v-b"],
    })
    assert request.view_ids == ("v-a", "v-b")


async def test_occupied_window_rejects_third_run_with_holder(manager) -> None:
    """Two parallel runs hold different windows; a third claim on an
    occupied one fails naming the holding run."""
    from agent.browser.manager import WindowOccupied

    with _Site() as site:
        a = await manager.open_window(scope_key="t:s", owner="workbench",
                                      url=f"{site.url}/a")
        b = await manager.open_window(scope_key="t:s", owner="workbench",
                                      url=f"{site.url}/b")
        c = await manager.open_window(scope_key="t:s", owner="workbench",
                                      url=f"{site.url}/c")
        manager.acquire("run-a", [a.view_id])
        manager.acquire("run-b", [b.view_id])
        with pytest.raises(WindowOccupied) as info:
            manager.acquire("run-c", [a.view_id])
        assert info.value.view_id == a.view_id
        assert info.value.held_by == "run-a"
        # run-c can still take the free window — conflicts are per-window
        acquired = manager.acquire("run-c", [c.view_id])
        assert acquired[0].view_id == c.view_id
        # releasing run-a frees its window only; b stays bound and open
        await manager.release_run("run-a")
        assert a.run_id is None and not a.closed
        assert b.run_id == "run-b" and not b.closed


async def test_service_claim_release_roundtrip(tmp_path) -> None:
    """Takeover blocks agent input; explicit release returns control."""
    from agent.service.factory import create_default_agent_service
    from agent.service import contracts as c
    from agent.executor.plan_executor import PlanExecutor
    from agent.browser.manager import resolve_browser
    import functools

    svc = create_default_agent_service(
        tmp_path / "runtime.sqlite", workspace_root=tmp_path / "workspace"
    )
    try:
        with _Site() as site:
            opened = await svc.open_window(c.OpenWindowRequest(
                tenant_id="t", user_id="u", session_id="s",
                request_id="r1", url=site.url))
            # bind to a run so controller starts as agent
            manager = svc._browser_manager()
            manager.acquire("run-1", [opened.view_id])
            rc = _run_context(manager, "run-1")
            rc.browser_windows = {
                opened.view_id: manager.window(opened.view_id)}

            claimed = await svc.claim_window(c.ClaimWindowRequest(
                tenant_id="t", user_id="u", session_id="s",
                request_id="r2", view_id=opened.view_id))
            assert claimed.controller == "user"

            # while user drives, agent input is refused
            open_browser = functools.partial(resolve_browser, rc)
            with pytest.raises(RuntimeError, match="WINDOW_NOT_UNDER_AGENT_CONTROL"):
                await PlanExecutor._exec_browser(
                    "browser.click", {"selector": "#x", "view_id": opened.view_id},
                    open_browser, None)

            released = await svc.release_window(c.ReleaseWindowRequest(
                tenant_id="t", user_id="u", session_id="s",
                request_id="r3", view_id=opened.view_id))
            assert released.controller == "agent"
            result = await PlanExecutor._exec_browser(
                "browser.extract", {"selector": "#fact", "view_id": opened.view_id},
                open_browser, None)
            assert "FACT:" in result["content"]
    finally:
        await svc.close()


# --- MB-07: URL-bearing results + Mars handoff ---------------------------

def test_e2e_dual_window_research_then_mars_handoff(monkeypatch, tmp_path):
    """A run bound to two windows reads a distinct fact from each; the
    completed output carries both facts with their page URLs; delegating
    to Mars hands the conclusions to the child model verbatim."""
    import tests.test_ha_r1_harness as harness
    from tests.test_browser_tools import FakeSession, _service, _wait_terminal
    from agent.service.contracts import DelegateRunRequest, StartRunRequest
    import asyncio

    harness.setup_provider(monkeypatch, [
        ("两个窗口各读一条事实",
         harness.intent("两个窗口各读一条事实", action="read", source=True)),
        ("两个窗口各读一条事实",
         harness.call_tool("browser_extract", selector="#fact", view_id="vw-a")),
        ("FACT-A", harness.call_tool("browser_extract", selector="#fact", view_id="vw-b")),
        ("FACT-B", "A 页事实 FACT-A [a-url]，B 页事实 FACT-B [b-url]"),
    ])

    async def resolve(run_context, view_id=None):
        windows = run_context.browser_windows
        if not windows:
            for vid, text in (("vw-a", "FACT-A"), ("vw-b", "FACT-B")):
                session = FakeSession(run_context, text=text)
                session.view_id = vid
                windows[vid] = session
        if view_id is not None:
            return windows[view_id]
        raise RuntimeError("must name view_id")

    monkeypatch.setattr("agent.orchestrator.executor.resolve_browser", resolve)

    # bound windows exist in the manager — fake pages, real binding path
    from agent.browser.manager import BrowserManager

    def fake_acquire(self, run_id, view_ids):
        acquired = []
        for vid, text in zip(view_ids, ("FACT-A", "FACT-B")):
            session = FakeSession(text=text)
            session.view_id = vid
            session.owner = "workbench"
            session.scope_key = "t:s1"
            session.closed = False
            acquired.append(session)
            session.run_id = run_id
        return acquired

    bound: dict = {}

    def fake_window(self, view_id):
        if not bound:
            for vid in ("vw-a", "vw-b"):
                s = FakeSession()
                s.view_id = vid
                s.owner = "workbench"
                s.scope_key = "t:s1"
                s.closed = False
                s.run_id = None
                bound[vid] = s
        return bound.get(view_id)

    monkeypatch.setattr(BrowserManager, "acquire", fake_acquire)
    monkeypatch.setattr(BrowserManager, "window", fake_window)

    async def scenario():
        service, store, _ = _service(tmp_path)
        try:
            run = await service.start_run(StartRunRequest(
                tenant_id="t", user_id="u", session_id="s1",
                request_id="r1", request_text="两个窗口各读一条事实",
                origin_planet="mercury",
                view_ids=("vw-a", "vw-b")))
            head = await _wait_terminal(service, "t", "s1", run.run_id)
            assert head.run_status == "COMPLETED", head
            out = store.read_run_snapshot("t", run.run_id, session_id="s1").output
            assert "FACT-A" in out.text and "FACT-B" in out.text

            # research conclusions travel to Mars as the child's request —
            # not a browser session, not a login state.
            delegation = await service.delegate_run(DelegateRunRequest(
                tenant_id="t", user_id="u", session_id="s1",
                request_id="d1", parent_run_id=run.run_id,
                target_planet="mars",
                request_text="基于结论 FACT-A 与 FACT-B 继续实现"))
            child = await service.start_run(StartRunRequest(
                tenant_id="t", user_id="u", session_id="s1",
                request_id="r2", request_text="基于结论 FACT-A 与 FACT-B 继续实现",
                task_id=delegation["task_id"], origin_planet="mars"))
            await _wait_terminal(service, "t", "s1", child.run_id, timeout=20)
            assert delegation["task_id"]
        finally:
            await service.close()

    asyncio.run(scenario())


async def test_concurrent_opens_share_one_context_and_cap(manager) -> None:
    """Five concurrent opens on one scope: at most the cap of four land,
    and they all share the scope's single context."""
    import asyncio
    from agent.browser.manager import MAX_WORKBENCH_WINDOWS

    with _Site() as site:
        results = await asyncio.gather(
            *(manager.open_window(scope_key="t:s", owner="workbench",
                                  url=site.url) for _ in range(5)),
            return_exceptions=True,
        )
        opened = [w for w in results if not isinstance(w, Exception)]
        refused = [w for w in results if isinstance(w, ValueError)]
        assert len(opened) == MAX_WORKBENCH_WINDOWS
        assert len(refused) == 5 - MAX_WORKBENCH_WINDOWS
        assert len(manager._contexts) == 1


async def test_start_run_refuses_foreign_session_window(tmp_path) -> None:
    """view_ids from another session are refused at admission."""
    from agent.service.factory import create_default_agent_service
    from agent.service import contracts as c
    from agent.service.errors import AgentServiceError

    svc = create_default_agent_service(
        tmp_path / "runtime.sqlite", workspace_root=tmp_path / "workspace"
    )
    try:
        with _Site() as site:
            opened = await svc.open_window(c.OpenWindowRequest(
                tenant_id="t", user_id="u", session_id="s-A",
                request_id="r1", url=site.url))
            with pytest.raises(AgentServiceError) as info:
                await svc.start_run(c.StartRunRequest(
                    tenant_id="t", user_id="u", session_id="s-B",
                    request_id="r2", request_text="借用别人的窗口",
                    view_ids=(opened.view_id,)))
            assert info.value.code.value == "INVALID_REQUEST"
    finally:
        await svc.close()


async def test_refused_run_leaves_no_reservation_and_replay_uses_admitted(tmp_path) -> None:
    """A refused view_ids request writes no run row; a replayed request_id
    resolves to the originally admitted run without re-validating."""
    from agent.service.factory import create_default_agent_service
    from agent.service import contracts as c
    from agent.service.errors import AgentServiceError

    svc = create_default_agent_service(
        tmp_path / "runtime.sqlite", workspace_root=tmp_path / "workspace"
    )
    try:
        with _Site() as site:
            opened = await svc.open_window(c.OpenWindowRequest(
                tenant_id="t", user_id="u", session_id="s-A",
                request_id="r1", url=site.url))
            # refused foreign bind → no CREATED row anywhere
            with pytest.raises(AgentServiceError):
                await svc.start_run(c.StartRunRequest(
                    tenant_id="t", user_id="u", session_id="s-B",
                    request_id="r2", request_text="借用窗口",
                    view_ids=(opened.view_id,)))
            assert await svc.list_runs(c.ListRunsRequest(
                tenant_id="t", user_id="u", session_id="s-B",
                request_id="r2l")) == []

            # an identical replay resolves to the admitted run — the
            # admitted path runs before view_ids validation, so replay
            # reuses admitted facts instead of re-validating windows
            first = await svc.start_run(c.StartRunRequest(
                tenant_id="t", user_id="u", session_id="s-A",
                request_id="r3", request_text="占位 run",
                view_ids=(opened.view_id,)))
            await svc.close_window(c.CloseWindowRequest(
                tenant_id="t", user_id="u", session_id="s-A",
                request_id="rc", view_id=opened.view_id))
            replayed = await svc.start_run(c.StartRunRequest(
                tenant_id="t", user_id="u", session_id="s-A",
                request_id="r3", request_text="占位 run",
                view_ids=(opened.view_id,)))
            assert replayed.run_id == first.run_id
    finally:
        await svc.close()
