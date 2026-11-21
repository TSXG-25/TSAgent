"""Egress proxy for the per-run browser — the browser's SSRF boundary.

Chromium sends every request through this in-process HTTP/CONNECT proxy:
navigations, every redirect hop, subresources and WebSockets.  Before
connecting, the proxy resolves the target host, rejects it unless every
resolved address is allowed, and then connects to the address it checked —
so a redirect into an internal service and DNS rebinding between check and
connect are both closed.  Playwright's request routing cannot do this: the
browser follows redirect hops without re-entering the route handler.

Plain-HTTP requests are forced to ``Connection: close`` so one client
connection carries exactly one checked request.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
from typing import Callable
from urllib.parse import urlsplit

ALLOW_PRIVATE_ENV = "DDG_ALLOW_PRIVATE_URLS"
BLOCKED_HEADER = "X-TSAgent-Egress-Blocked"

Address = ipaddress.IPv4Address | ipaddress.IPv6Address


class EgressBlocked(Exception):
    """A destination the browser must not reach."""


def allow_private() -> bool:
    return os.environ.get(ALLOW_PRIVATE_ENV, "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def public_only(host: str, port: int, address: Address) -> bool:
    """Default policy — same boundary as web_fetch: publicly routable
    addresses only, unless the shared opt-in is set (fake-IP DNS or a
    trusted local deployment)."""
    return address.is_global or allow_private()


_HOP_HEADERS = frozenset({"proxy-connection", "connection", "keep-alive"})


class EgressProxy:
    def __init__(self, policy: Callable[[str, int, Address], bool] = public_only):
        self._policy = policy
        self._server: asyncio.base_events.Server | None = None
        self._clients: set[asyncio.StreamWriter] = set()
        self.blocked: list[str] = []

    async def start(self) -> int:
        self._server = await asyncio.start_server(
            self._handle, "127.0.0.1", 0,
        )
        return self._server.sockets[0].getsockname()[1]

    async def close(self) -> None:
        # wait_closed() waits for every open connection (3.12+); a lingering
        # tunnel must not hang run teardown.
        self._server.close()
        for writer in list(self._clients):
            writer.close()
        await self._server.wait_closed()

    async def _checked_address(self, host: str, port: int) -> str:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, port, type=socket.SOCK_STREAM,
        )
        addresses = [ipaddress.ip_address(info[4][0]) for info in infos]
        for address in addresses:
            if not self._policy(host, port, address):
                raise EgressBlocked(
                    f"refusing to load {host}:{port}: resolves to non-public "
                    f"address {address} (SSRF protection; set "
                    f"{ALLOW_PRIVATE_ENV}=1 for trusted local deployments)"
                )
        return str(addresses[0])

    async def _handle(self, reader, writer) -> None:
        self._clients.add(writer)
        try:
            await self._serve(reader, writer)
        finally:
            self._clients.discard(writer)

    async def _serve(self, reader, writer) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            writer.close()
            return
        request_line, *header_lines = head.decode("latin-1").split("\r\n")
        method, target, version = request_line.split(" ", 2)
        if method == "CONNECT":
            parts = urlsplit(f"//{target}")
            host, port, origin_head = parts.hostname, parts.port or 443, b""
        else:
            parts = urlsplit(target)
            if parts.scheme != "http" or not parts.hostname:
                await self._refuse(writer, 400, f"unsupported proxy target {target}")
                return
            host, port = parts.hostname, parts.port or 80
            path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
            kept = [
                line for line in header_lines
                if line and line.split(":", 1)[0].strip().lower() not in _HOP_HEADERS
            ]
            origin_head = (
                "\r\n".join([f"{method} {path} {version}", *kept, "Connection: close"])
                + "\r\n\r\n"
            ).encode("latin-1")
        try:
            address = await self._checked_address(host, port)
        except EgressBlocked as exc:
            self.blocked.append(str(exc))
            await self._refuse(writer, 403, str(exc))
            return
        except OSError as exc:
            await self._refuse(writer, 502, f"cannot resolve {host}: {exc}")
            return
        try:
            up_reader, up_writer = await asyncio.open_connection(address, port)
        except OSError as exc:
            await self._refuse(writer, 502, f"cannot connect {host}:{port}: {exc}")
            return
        if method == "CONNECT":
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
        else:
            up_writer.write(origin_head)
        await self._splice(reader, writer, up_reader, up_writer)

    @staticmethod
    async def _refuse(writer, status: int, reason: str) -> None:
        body = reason.encode("utf-8")
        writer.write(
            f"HTTP/1.1 {status} Blocked\r\n{BLOCKED_HEADER}: 1\r\n"
            f"Content-Type: text/plain; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
            .encode("latin-1") + body
        )
        await writer.drain()
        writer.close()

    @staticmethod
    async def _splice(c_reader, c_writer, u_reader, u_writer) -> None:
        async def pipe(src, dst):
            try:
                while data := await src.read(65536):
                    dst.write(data)
                    await dst.drain()
            except (ConnectionError, OSError):
                pass

        tasks = [
            asyncio.ensure_future(pipe(c_reader, u_writer)),
            asyncio.ensure_future(pipe(u_reader, c_writer)),
        ]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in tasks:
            task.cancel()
        for w in (u_writer, c_writer):
            w.close()
