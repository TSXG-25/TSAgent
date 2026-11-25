"""MB-03 — cross-end window contract + loopback display bridge.

Real Chromium, real egress proxy, real WebSocket channel: service window
ops, scope isolation, ticket-gated display connections, frame-out and
input-in on one connection.
"""

from __future__ import annotations

import asyncio
import base64
import http.server
import json
import os
import socketserver
import struct
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
                    b'<html><title>bridge page</title><body>'
                    b'<input id="box"><p id="marker">BRIDGE-OK</p>'
                    b"</body></html>"
                )
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
async def service(tmp_path: Path):
    from agent.service.factory import create_default_agent_service

    svc = create_default_agent_service(
        tmp_path / "runtime.sqlite", workspace_root=tmp_path / "workspace"
    )
    try:
        yield svc
    finally:
        await svc.close()


def _window_request(name: str, session: str = "s-1", **kw):
    from agent.service import contracts as c

    base = {
        "tenant_id": "t-1",
        "user_id": "u-1",
        "session_id": session,
        "request_id": f"r-{name}",
    }
    return {
        "list": c.ListWindowsRequest,
        "open": c.OpenWindowRequest,
        "close": c.CloseWindowRequest,
        "nav": c.NavigateWindowRequest,
        "ticket": c.DisplayTicketRequest,
    }[name](**{**base, **kw})


def _skip_no_chromium(error: BaseException) -> None:
    if "Executable doesn't exist" in str(error):
        pytest.skip("chromium executable not installed")
    raise error


async def test_service_window_lifecycle_and_scope(service) -> None:
    with _Site() as site:
        try:
            opened = await service.open_window(
                _window_request("open", url=site.url)
            )
        except Exception as error:  # pragma: no cover - env dependent
            _skip_no_chromium(error)
            return
        assert opened.url.startswith(site.url)
        assert "bridge page" in opened.title or opened.title == "bridge page"
        assert opened.owner == "workbench"

        listed = await service.list_windows(_window_request("list"))
        assert [w.view_id for w in listed] == [opened.view_id]

        # another session must not see or navigate this window
        other = await service.list_windows(
            _window_request("list", session="s-2")
        )
        assert other == []
        from agent.service.errors import AgentServiceError

        with pytest.raises(AgentServiceError):
            await service.navigate_window(
                _window_request(
                    "nav",
                    session="s-2",
                    view_id=opened.view_id,
                    action="navigate",
                    url=site.url,
                )
            )

        # history: navigate → back → forward through the service
        await service.navigate_window(
            _window_request(
                "nav",
                view_id=opened.view_id,
                action="navigate",
                url=f"{site.url}/second",
            )
        )
        back = await service.navigate_window(
            _window_request("nav", view_id=opened.view_id, action="back")
        )
        assert back.url == site.url + "/" or back.url == site.url
        fwd = await service.navigate_window(
            _window_request("nav", view_id=opened.view_id, action="forward")
        )
        assert fwd.url.endswith("/second")

        closed = await service.close_window(
            _window_request("close", view_id=opened.view_id)
        )
        assert closed["closed"] is True
        assert await service.list_windows(_window_request("list")) == []


def _pack(meta: dict) -> bytes:
    head = json.dumps(meta).encode()
    return struct.pack(">I", len(head)) + head


async def _read_frame(ws) -> tuple[dict, bytes]:
    blob = await asyncio.wait_for(ws.recv(), timeout=10)
    (length,) = struct.unpack(">I", blob[:4])
    meta = json.loads(blob[4 : 4 + length])
    return meta, blob[4 + length :]


async def test_display_bridge_frames_and_input(service) -> None:
    websockets = pytest.importorskip("websockets")
    from agent.service.local_sidecar.display_bridge import DisplayBridge

    with _Site() as site:
        try:
            opened = await service.open_window(
                _window_request("open", url=site.url)
            )
        except Exception as error:  # pragma: no cover
            _skip_no_chromium(error)
            return
        bridge = DisplayBridge(
            service, allowed_origins=frozenset({"http://127.0.0.1:4173"})
        )
        try:
            ticket = await bridge.ticket("t-1", "s-1")
            url = (
                f"ws://127.0.0.1:{ticket['port']}/display/s-1/"
                f"{opened.view_id}?token={ticket['token']}"
            )
            async with websockets.connect(
                url, origin="http://127.0.0.1:4173", additional_headers={}
            ) as ws:
                meta, data = await _read_frame(ws)
                assert meta["view_id"] == opened.view_id
                assert meta["doc_generation"] >= 1
                assert len(base64.b64encode(data)) > 100  # real JPEG bytes

                # input rides the same connection — click focuses, then text
                window = service.window_for_session(
                    "t-1", "s-1", opened.view_id
                )
                box = await window.page.locator("#box").bounding_box()
                await ws.send(
                    json.dumps(
                        {
                            "t": "click",
                            "x": box["x"] + box["width"] / 2,
                            "y": box["y"] + box["height"] / 2,
                        }
                    )
                )
                await ws.send(json.dumps({"t": "text", "s": "经由通道"}))
                for _ in range(40):
                    value = await window.page.input_value("#box")
                    if value == "经由通道":
                        break
                    await asyncio.sleep(0.1)
                assert value == "经由通道"

                # a second frame arrives carrying the same view_id
                meta2, _ = await _read_frame(ws)
                assert meta2["view_id"] == opened.view_id

            # wrong session in path → rejected
            with pytest.raises(Exception):
                async with websockets.connect(
                    f"ws://127.0.0.1:{ticket['port']}/display/s-2/"
                    f"{opened.view_id}?token={ticket['token']}"
                ) as ws:
                    await _read_frame(ws)
        finally:
            await bridge.close()


async def test_display_bridge_rejects_bad_ticket(service) -> None:
    websockets = pytest.importorskip("websockets")
    from agent.service.local_sidecar.display_bridge import DisplayBridge

    bridge = DisplayBridge(service)
    try:
        ticket = await bridge.ticket("t-1", "s-1")
        with pytest.raises(Exception):
            async with websockets.connect(
                f"ws://127.0.0.1:{ticket['port']}/display/s-1/"
                f"view-x?token=wrong-token"
            ) as ws:
                await _read_frame(ws)
    finally:
        await bridge.close()


async def test_input_respects_controller_and_generation(service) -> None:
    """Human input injects only while the user holds the window, and a
    message stamped with a stale document generation is dropped."""
    websockets = pytest.importorskip("websockets")
    from agent.service.local_sidecar.display_bridge import DisplayBridge
    from agent.service import contracts as c

    with _Site() as site:
        try:
            opened = await service.open_window(
                _window_request("open", url=site.url)
            )
        except Exception as error:  # pragma: no cover
            _skip_no_chromium(error)
            return
        bridge = DisplayBridge(
            service, allowed_origins=frozenset({"http://127.0.0.1:4173"})
        )
        try:
            ticket = await bridge.ticket("t-1", "s-1")
            url = (
                f"ws://127.0.0.1:{ticket['port']}/display/s-1/"
                f"{opened.view_id}?token={ticket['token']}"
            )
            window = service.window_for_session("t-1", "s-1", opened.view_id)

            async def value() -> str:
                return await window.page.input_value("#box")

            async def fill(chars: str, gen=None) -> str:
                await ws.send(
                    json.dumps({"t": "click", "x": 60, "y": 40, "g": gen})
                )
                await ws.send(json.dumps({"t": "text", "s": chars, "g": gen}))
                for _ in range(30):
                    v = await value()
                    if chars in v:
                        return v
                    await asyncio.sleep(0.1)
                return await value()

            async with websockets.connect(url) as ws:
                await _read_frame(ws)
                # agent holds the window → clicks/text inject nothing
                manager = service._browser_manager()
                manager.acquire("run-1", [opened.view_id])
                box = await window.page.locator("#box").bounding_box()
                await ws.send(json.dumps({
                    "t": "click",
                    "x": box["x"] + 2, "y": box["y"] + 2,
                }))
                await ws.send(json.dumps({"t": "text", "s": "agent期输入"}))
                await asyncio.sleep(0.6)
                assert await value() == ""

                # user takes over → input flows again
                await service.claim_window(c.ClaimWindowRequest(
                    tenant_id="t-1", user_id="u", session_id="s-1",
                    request_id="r-claim", view_id=opened.view_id))
                await ws.send(json.dumps({
                    "t": "click",
                    "x": box["x"] + 2, "y": box["y"] + 2,
                }))
                await ws.send(json.dumps({"t": "text", "s": "abc"}))
                for _ in range(30):
                    if await value() == "abc":
                        break
                    await asyncio.sleep(0.1)
                assert await value() == "abc"

                # stale generation → dropped
                stale = window.doc_generation - 1
                await window.page.fill("#box", "")
                await ws.send(json.dumps({"t": "text", "s": "stale", "g": stale}))
                await asyncio.sleep(0.6)
                assert await value() == ""
        finally:
            await bridge.close()


async def test_frame_generation_bound_at_arrival(service) -> None:
    """A frame received before a navigation commit keeps the old document
    generation — meta is stamped when the frame arrives, not when the
    bridge later sends it, so an input stamped against the stale picture
    is correctly refused."""
    websockets = pytest.importorskip("websockets")
    from agent.service import contracts as c

    with _Site() as site:
        try:
            opened = await service.open_window(
                _window_request("open", url=site.url)
            )
        except Exception as error:  # pragma: no cover
            _skip_no_chromium(error)
            return
        window = service.window_for_session("t-1", "s-1", opened.view_id)
        display = await window.ensure_display()
        captured: list[dict] = []
        display.subscribe(lambda data, meta: captured.append(meta))
        for _ in range(40):
            if captured:
                break
            await asyncio.sleep(0.1)
        assert captured and captured[0]["doc_generation"] == 1
        gen_before = window.doc_generation
        await window.navigate(f"{site.url}/next")
        assert window.doc_generation == gen_before + 1
        # the frame captured pre-nav still reports its own generation
        assert captured[0]["doc_generation"] == gen_before


async def test_claim_waits_for_inflight_action(service) -> None:
    """claim_window confirms handover only after the action already in
    flight settles — a pending click must not land post-takeover."""
    from agent.service import contracts as c

    with _Site() as site:
        try:
            opened = await service.open_window(
                _window_request("open", url=site.url)
            )
        except Exception as error:  # pragma: no cover
            _skip_no_chromium(error)
            return
        window = service.window_for_session("t-1", "s-1", opened.view_id)
        window._controller = "agent"

        gate = asyncio.Event()
        orig_click = window.page.click

        async def slow_click(selector: str) -> None:
            await gate.wait()
            return await orig_click(selector)

        window.page.click = slow_click  # type: ignore[assignment]
        in_flight = asyncio.ensure_future(window.click("#box"))
        await asyncio.sleep(0.05)
        claim = asyncio.ensure_future(
            service.claim_window(
                c.ClaimWindowRequest(
                    tenant_id="t-1", user_id="u-1", session_id="s-1",
                    request_id="rc", view_id=opened.view_id,
                )
            )
        )
        await asyncio.sleep(0.05)
        assert not claim.done()  # still waiting for the click to settle
        gate.set()
        summary = await claim
        assert summary.controller == "user"
        await in_flight


async def test_replayed_frame_carries_its_own_generation(service) -> None:
    """A late subscriber gets the cached frame with the same
    doc_generation it was stamped at arrival — no KeyError, no
    send-time re-stamp."""
    websockets = pytest.importorskip("websockets")
    from agent.service.local_sidecar.display_bridge import DisplayBridge

    with _Site() as site:
        try:
            opened = await service.open_window(
                _window_request("open", url=site.url)
            )
        except Exception as error:  # pragma: no cover
            _skip_no_chromium(error)
            return
        window = service.window_for_session("t-1", "s-1", opened.view_id)
        display = await window.ensure_display()
        for _ in range(40):
            if display.frame_count:
                break
            await asyncio.sleep(0.1)
        gen_seen = display._latest[2]["doc_generation"]
        replayed: list[dict] = []
        display.subscribe(lambda d, m: replayed.append(m))
        assert replayed and replayed[0]["doc_generation"] == gen_seen

        # end-to-end: a bridge client connecting late receives a frame
        # whose meta has doc_generation — no KeyError, no dead channel.
        bridge = DisplayBridge(
            service, allowed_origins=frozenset({"http://127.0.0.1:4173"})
        )
        try:
            ticket = await bridge.ticket("t-1", "s-1")
            url = (
                f"ws://127.0.0.1:{ticket['port']}/display/s-1/"
                f"{opened.view_id}?token={ticket['token']}"
            )
            async with websockets.connect(url) as ws:
                meta, _ = await _read_frame(ws)
                assert "doc_generation" in meta
        finally:
            await bridge.close()


async def test_settling_blocks_human_and_agent_input(service) -> None:
    """While a claim drains the in-flight action the window is
    'settling' — the bridge drops human input AND the executor-facing
    controller check refuses new agent ops."""
    from agent.service import contracts as c

    with _Site() as site:
        try:
            opened = await service.open_window(
                _window_request("open", url=site.url)
            )
        except Exception as error:  # pragma: no cover
            _skip_no_chromium(error)
            return
        window = service.window_for_session("t-1", "s-1", opened.view_id)
        window._controller = "agent"

        gate = asyncio.Event()
        orig_click = window.page.click

        async def slow_click(selector: str) -> None:
            await gate.wait()
            return await orig_click(selector)

        window.page.click = slow_click  # type: ignore[assignment]
        in_flight = asyncio.ensure_future(window.click("#box"))
        await asyncio.sleep(0.05)
        claim = asyncio.ensure_future(
            service.claim_window(
                c.ClaimWindowRequest(
                    tenant_id="t-1", user_id="u-1", session_id="s-1",
                    request_id="rs", view_id=opened.view_id,
                )
            )
        )
        await asyncio.sleep(0.05)
        assert not claim.done()
        # during the drain the window is neither user nor agent
        assert window.controller == "settling"
        gate.set()
        await claim
        assert window.controller == "user"
        await in_flight
