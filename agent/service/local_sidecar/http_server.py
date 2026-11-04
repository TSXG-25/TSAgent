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
