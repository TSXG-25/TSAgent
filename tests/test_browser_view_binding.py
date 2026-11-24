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
            with pytest.raises(RuntimeError, match="WINDOW_UNDER_USER_CONTROL"):
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
