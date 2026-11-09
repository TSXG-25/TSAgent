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
    allowed_origins: frozenset[str] = frozenset(),
) -> None:
    """Serve the sidecar dispatcher over HTTP until shutdown."""
    dispatcher = SidecarDispatcher(service, diagnostics=diagnostics)
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args) -> None:  # keep stdout for the app
            return

        def _guard(self) -> bool:
            """Loopback-only gate: the RPC has no auth, so reject anything a
            webpage or a DNS-rebinded hostname could send.  Host must be the
            literal loopback address; a browser Origin must be allowlisted."""
            if self.headers.get("Host") not in allowed_hosts:
                self.send_error(403, "untrusted Host")
                return False
            origin = self.headers.get("Origin")
            if origin is not None and origin not in allowed_origins:
                self.send_error(403, "untrusted Origin")
                return False
            return True

        def _cors(self) -> None:
            origin = self.headers.get("Origin")
            if origin is not None and origin in allowed_origins:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def do_OPTIONS(self) -> None:
            if not self._guard():
                return
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_POST(self) -> None:
            if not self._guard():
                return
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
            self._cors()
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
