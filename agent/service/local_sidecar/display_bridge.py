"""Loopback display channel — frames out, human input in, one WS per view.

Bound to loopback only, reached via ``display_ticket`` on the trusted
JSONL transport — the ticket mints a session-scoped token the WebSocket
path re-checks against the request's session and the window's owner.
No CDP endpoint is exposed; the only allowed messages are the display
and input verbs defined here.

Frame meta carries ``view_id`` + ``doc_generation`` so a client can drop
stale pictures — frames for a superseded document never paint over the
current window.  Slow clients get dropped frames, never a replay queue.
"""

from __future__ import annotations

import asyncio
import json
import struct
import uuid
from typing import Any
from urllib.parse import parse_qs, urlsplit


def _pack_frame(meta: dict, data: bytes) -> bytes:
    head = json.dumps(meta).encode("utf-8")
    return struct.pack(">I", len(head)) + head + data


# The Tauri WebView's own origins — always allowed alongside whatever
# dev origins the sidecar was configured with.
TAURI_ORIGINS = frozenset(
    {"tauri://localhost", "http://tauri.localhost", "https://tauri.localhost"}
)


class DisplayBridge:
    """Session-scoped token gate + per-window frame/input forwarding."""

    def __init__(self, service: Any, *, allowed_origins: frozenset[str] = frozenset()) -> None:
        self._service = service
        self._allowed_origins = allowed_origins | TAURI_ORIGINS
        self._server: Any | None = None
        self._port: int | None = None
        self._tokens: dict[str, tuple[str, str]] = {}  # token → (tenant, session)
        self._connections: set = set()

    async def ticket(self, tenant_id: str, session_id: str) -> dict[str, Any]:
        if self._server is None:
            from websockets.asyncio.server import serve

            self._server = await serve(self._handler, "127.0.0.1", 0)
            self._port = self._server.sockets[0].getsockname()[1]
        token = uuid.uuid4().hex
        self._tokens[token] = (tenant_id, session_id)
        return {"port": self._port, "token": token}

    async def close(self) -> None:
        for ws in list(self._connections):
            try:
                await ws.close()
            except Exception:
                pass
        self._connections.clear()
        self._tokens.clear()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _reject(self, ws: Any, reason: str) -> None:
        await ws.close(1008, reason)

    async def _handler(self, ws: Any) -> None:
        request = ws.request
        origin = request.headers.get("Origin")
        if origin is not None and origin not in self._allowed_origins:
            return await self._reject(ws, "untrusted Origin")
        parts = urlsplit(request.path)
        segments = [s for s in parts.path.split("/") if s]
        query = parse_qs(parts.query)
        if len(segments) != 3 or segments[0] != "display" or not query.get("token"):
            return await self._reject(ws, "bad channel path")
        _, session_id, view_id = segments
        bound = self._tokens.get(query["token"][0])
        if bound is None or bound[1] != session_id:
            return await self._reject(ws, "bad ticket")
        window = self._service.window_for_session(bound[0], session_id, view_id)
        if window is None:
            return await self._reject(ws, "unknown window")
        display = await window.ensure_display()
        self._connections.add(ws)
        pending = {"n": 0}

        def send(data: bytes, meta: dict) -> None:
            if pending["n"] >= 1:
                return  # slow client — drop stale frames, never replay
            blob = _pack_frame(
                {
                    **meta,
                    "view_id": view_id,
                    "doc_generation": window.doc_generation,
                },
                data,
            )
            pending["n"] += 1
            task = asyncio.get_running_loop().create_task(ws.send(blob))
            task.add_done_callback(lambda t: self._sent(ws, pending, t))

        display.subscribe(send)
        try:
            async for raw in ws:
                try:
                    await self._input(display, json.loads(raw))
                except Exception:
                    pass  # malformed input is ignored, never fatal
        finally:
            display.unsubscribe(send)
            self._connections.discard(ws)

    def _sent(self, ws: Any, pending: dict, task: Any) -> None:
        pending["n"] = max(0, pending["n"] - 1)
        if task.exception() is not None:
            self._connections.discard(ws)

    @staticmethod
    async def _input(display: Any, msg: dict) -> None:
        kind = msg.get("t")
        if kind == "click":
            await display.click(msg["x"], msg["y"], button=msg.get("b", "left"))
        elif kind == "scroll":
            await display.scroll(msg["x"], msg["y"], msg["dx"], msg["dy"])
        elif kind == "text":
            await display.insert_text(str(msg.get("s", "")))
        elif kind == "key":
            await display.key_press(str(msg["k"]))
        elif kind == "viewport":
            await display.set_viewport(int(msg["w"]), int(msg["h"]))
