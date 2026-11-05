"""HTTP transport for the local sidecar — browser dev mode.

The stdio JSONL transport needs a host process to spawn it; a plain
browser cannot.  This module exposes the same dispatcher over
``POST /rpc`` on loopback so the Vite dev build can use ``local`` mode.
Same request/response envelopes as local_protocol — one JSON object per
call, no streaming.
"""
from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TextIO

from ..local_protocol import (
    LocalProtocolError,
    LocalRpcRequest,
    encode_response,
)
from .dispatcher import SidecarDispatcher


def run_http_sidecar(
    service,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    diagnostics: TextIO,
) -> None:
    """Serve the sidecar dispatcher over HTTP until shutdown."""
    dispatcher = SidecarDispatcher(service, diagnostics=diagnostics)
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:  # keep stdout for the app
            return

        def do_GET(self) -> None:
            if self.path.split("?", 1)[0] != "/events":
                self.send_error(404)
                return
            from urllib.parse import parse_qs, urlparse

            query = parse_qs(urlparse(self.path).query)
            tenant_id = (query.get("tenant_id") or [""])[0]
            session_id = (query.get("session_id") or [""])[0]
            user_id = (query.get("user_id") or [""])[0]
            run_id = (query.get("run_id") or [""])[0]
            cursor = int((query.get("after") or ["0"])[0])
            if not (tenant_id and session_id and run_id and user_id):
                self.send_error(400, "tenant_id/session_id/run_id/user_id required")
                return

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                self._stream_events(
                    tenant_id, session_id, user_id, run_id, cursor
                )
            except (BrokenPipeError, ConnectionResetError):
                return

        def _stream_events(
            self,
            tenant_id: str,
            session_id: str,
            user_id: str,
            run_id: str,
            cursor: int,
        ) -> None:
            """Poll durable events and push increments as SSE frames."""
            import time
            from ..contracts import EventStreamRequest, RunLookupRequest

            deadline = time.monotonic() + 300
            last_beat = time.monotonic()
            sequence = 0
            while time.monotonic() < deadline:
                request = EventStreamRequest(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    session_id=session_id,
                    run_id=run_id,
                    request_id=f"sse-{cursor}-{sequence}",
                    after_sequence=cursor,
                )
                async def _collect() -> list:
                    return [e async for e in service.stream_events(request)]

                events = asyncio.run_coroutine_threadsafe(
                    _collect(), loop
                ).result(timeout=30)
                for event in events:
                    data = json.dumps(
                        SidecarDispatcher._to_wire(event), ensure_ascii=False
                    )
                    self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    cursor = max(cursor, getattr(event, "sequence_number", cursor))
                if time.monotonic() - last_beat >= 15:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    last_beat = time.monotonic()
                # Close once the run is terminal and the log is drained.
                lookup = RunLookupRequest(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    session_id=session_id,
                    run_id=run_id,
                    request_id=f"sse-head-{sequence}",
                )
                snap = asyncio.run_coroutine_threadsafe(
                    service.get_run(lookup), loop
                ).result(timeout=30)
                if snap.status.value in (
                    "COMPLETED", "FAILED_TERMINAL", "BLOCKED",
                    "CANCELLED", "TIMED_OUT",
                ) and not events:
                    self.wfile.write(b"event: end\ndata: {}\n\n")
                    self.wfile.flush()
                    return
                sequence += 1
                time.sleep(0.4)

        def do_POST(self) -> None:
            if self.path != "/rpc":
                self.send_error(404)
                return
            body = self.rfile.read(int(self.headers["Content-Length"]))
            try:
                request = LocalRpcRequest.from_dict(json.loads(body))
            except (LocalProtocolError, json.JSONDecodeError) as exc:
                self._reply(400, {"error": str(exc)})
                return
            future = asyncio.run_coroutine_threadsafe(
                dispatcher.dispatch(request), loop
            )
            try:
                response = future.result(timeout=300)
            except Exception as exc:
                self._reply(500, {"error": str(exc)})
                return
            self._reply(200, json.loads(encode_response(response)))

        def _reply(self, status: int, payload: dict) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer((host, port), Handler)
    print(f"TSAgent sidecar listening on http://{host}:{port}/rpc")
    try:
        server.serve_forever()
    finally:
        loop.call_soon_threadsafe(loop.stop)
