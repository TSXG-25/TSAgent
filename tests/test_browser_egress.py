"""The browser's SSRF boundary: EgressProxy + BrowserSession.navigate.

Three layers, all offline (loopback only):
- the default address policy;
- the proxy's wire behaviour with a raw asyncio client (no Chromium);
- real Chromium behind the proxy, proving redirect hops and subresources
  cannot reach a forbidden origin (skipped when playwright/Chromium is not
  installed).  That last case is the regression for the hole found in
  real smoke: Playwright's request routing does not see redirect hops.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import threading
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from agent.browser import egress
from agent.browser.egress import BLOCKED_HEADER, EgressBlocked, EgressProxy, public_only
from agent.browser.session import BrowserSession, check_scheme


# --- policy ---------------------------------------------------------------

@pytest.mark.parametrize("address", [
    "127.0.0.1", "169.254.169.254", "10.0.0.5", "172.16.0.1",
    "192.168.1.1", "198.18.0.159", "100.64.0.1", "::1", "fd00::1",
])
def test_default_policy_rejects_non_public(monkeypatch, address):
    monkeypatch.delenv(egress.ALLOW_PRIVATE_ENV, raising=False)
    assert not public_only("h", 80, ipaddress.ip_address(address))


def test_default_policy_allows_public_and_honours_opt_in(monkeypatch):
    monkeypatch.delenv(egress.ALLOW_PRIVATE_ENV, raising=False)
    assert public_only("h", 443, ipaddress.ip_address("93.184.216.34"))
    monkeypatch.setenv(egress.ALLOW_PRIVATE_ENV, "1")
    assert public_only("h", 443, ipaddress.ip_address("198.18.0.1"))


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "javascript:alert(1)", "ftp://example.com/x",
    "chrome://settings", "data:text/html,hi",
])
def test_non_http_schemes_are_refused_before_navigation(url):
    with pytest.raises(EgressBlocked, match="scheme"):
        check_scheme(url)


# --- proxy wire behaviour -------------------------------------------------

class _Origin:
    """Loopback HTTP origin recording every request it receives."""

    def __init__(self):
        self.hits: list[tuple[str, dict]] = []
        origin = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                origin.hits.append((self.path, dict(self.headers)))
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", origin.redirect_to)
                    self.end_headers()
                    return
                if self.path == "/link":
                    body = (
                        f'<body>LINK<a id="go" href="{origin.link_to}">'
                        "go</a></body>"
                    )
                elif self.path == "/delayed":
                    # No title exists until the next document commits. A
                    # title read must wait through this navigation and
                    # check the new document before returning its text.
                    body = (
                        '<body>Loading<script>setTimeout(() => location.href = '
                        + json.dumps(origin.redirect_to)
                        + ', 500)</script></body>'
                    )
                elif self.path == "/embed":
                    body = (
                        f'<body>EMBED<img src="{origin.redirect_to}">'
                        f'<script>fetch("{origin.redirect_to}x")'
                        ".catch(() => 0)</script></body>"
                    )
                else:
                    body = "<title>ok</title><body>ALLOWED-BODY</body>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(body.encode())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.redirect_to = ""
        self.link_to = ""
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def origins():
    allowed, forbidden = _Origin(), _Origin()
    yield allowed, forbidden
    allowed.close()
    forbidden.close()


def _policy(forbidden_port):
    return lambda host, port, address: port != forbidden_port


async def _raw(proxy_port, head: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
    writer.write(head)
    await writer.drain()
    data = await asyncio.wait_for(reader.read(), timeout=5)
    writer.close()
    return data


def test_proxy_refuses_forbidden_plain_http_without_touching_origin(origins):
    allowed, forbidden = origins

    async def go():
        proxy = EgressProxy(policy=_policy(forbidden.port))
        port = await proxy.start()
        try:
            return await _raw(port, (
                f"GET http://127.0.0.1:{forbidden.port}/secret HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{forbidden.port}\r\n\r\n"
            ).encode()), proxy.blocked
        finally:
            await proxy.close()

    data, blocked = asyncio.run(go())
    assert data.startswith(b"HTTP/1.1 403")
    assert BLOCKED_HEADER.encode() in data
    assert "non-public" in blocked[0]
    assert forbidden.hits == []


def test_proxy_refuses_forbidden_connect_tunnel(origins):
    _, forbidden = origins

    async def go():
        proxy = EgressProxy(policy=_policy(forbidden.port))
        port = await proxy.start()
        try:
            return await _raw(port, (
                f"CONNECT 127.0.0.1:{forbidden.port} HTTP/1.1\r\n\r\n"
            ).encode())
        finally:
            await proxy.close()

    assert asyncio.run(go()).startswith(b"HTTP/1.1 403")
    assert forbidden.hits == []


def test_proxy_forwards_allowed_request_as_one_shot(origins):
    """Allowed plain-HTTP is rewritten to origin-form with Connection:
    close, so a reused client connection can't smuggle a second,
    unchecked destination."""
    allowed, forbidden = origins

    async def go():
        proxy = EgressProxy(policy=_policy(forbidden.port))
        port = await proxy.start()
        try:
            return await _raw(port, (
                f"GET http://127.0.0.1:{allowed.port}/page?q=1 HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{allowed.port}\r\n"
                "Proxy-Connection: keep-alive\r\n\r\n"
            ).encode())
        finally:
            await proxy.close()

    data = asyncio.run(go())
    assert b"ALLOWED-BODY" in data
    path, headers = allowed.hits[0]
    assert path == "/page?q=1"
    assert headers.get("Connection") == "close"
    assert "Proxy-Connection" not in headers


# --- navigate mapping (fake page) -----------------------------------------

def test_navigate_maps_proxy_refusal_response_to_blocked():
    proxy = SimpleNamespace(blocked=[])

    async def goto(url, wait_until):
        proxy.blocked.append("refusing to load 10.0.0.1:80: non-public")
        return SimpleNamespace(headers={BLOCKED_HEADER.lower(): "1"})

    session = BrowserSession(None, None, proxy, SimpleNamespace(goto=goto, on=lambda *a, **k: None))
    with pytest.raises(EgressBlocked, match="non-public"):
        asyncio.run(session.navigate("http://example.com/redirect"))


def test_navigate_maps_refused_tunnel_error_to_blocked():
    proxy = SimpleNamespace(blocked=[])

    async def goto(url, wait_until):
        proxy.blocked.append("refusing to load 10.0.0.1:443: non-public")
        raise RuntimeError("net::ERR_TUNNEL_CONNECTION_FAILED")

    session = BrowserSession(None, None, proxy, SimpleNamespace(goto=goto, on=lambda *a, **k: None))
    with pytest.raises(EgressBlocked, match="non-public"):
        asyncio.run(session.navigate("https://example.com/"))


def test_navigate_keeps_unrelated_errors():
    proxy = SimpleNamespace(blocked=[])

    async def goto(url, wait_until):
        raise RuntimeError("net::ERR_NAME_NOT_RESOLVED")

    session = BrowserSession(None, None, proxy, SimpleNamespace(goto=goto, on=lambda *a, **k: None))
    with pytest.raises(RuntimeError, match="NAME_NOT_RESOLVED"):
        asyncio.run(session.navigate("https://nx.example/"))


# --- real Chromium behind the proxy ---------------------------------------

@asynccontextmanager
async def _chromium_session(forbidden_port):
    async_api = pytest.importorskip("playwright.async_api")
    proxy = EgressProxy(policy=_policy(forbidden_port))
    port = await proxy.start()
    try:
        async with async_api.async_playwright() as pw:
            try:
                browser = await pw.chromium.launch(
                    proxy={"server": f"http://127.0.0.1:{port}"},
                    args=["--proxy-bypass-list=<-loopback>"],
                )
            except async_api.Error as exc:
                if "Executable doesn't exist at" in str(exc):
                    pytest.skip(f"Chromium not installed: {exc}")
                raise
            try:
                page = await (await browser.new_context()).new_page()
                yield BrowserSession(pw, browser, proxy, page)
            finally:
                await browser.close()
    finally:
        await proxy.close()


def test_chromium_redirect_and_subresources_cannot_reach_forbidden_origin(origins):
    allowed, forbidden = origins
    forbidden_url = f"http://127.0.0.1:{forbidden.port}/secret"
    allowed.redirect_to = forbidden_url

    async def go():
        async with _chromium_session(forbidden.port) as session:
            page = session.page
            base = f"http://127.0.0.1:{allowed.port}"
            assert (await session.navigate(f"{base}/")).startswith("ok")
            assert "ALLOWED-BODY" in (await session.snapshot()).text
            with pytest.raises(EgressBlocked):
                await session.navigate(f"{base}/redirect")
            # the proxy's refusal document is not a readable source
            with pytest.raises(EgressBlocked):
                await session.snapshot()
            # a 302 hop into a refused CONNECT commits the error page
            forbidden_https = f"https://127.0.0.1:{forbidden.port}/"
            allowed.redirect_to = forbidden_https
            with pytest.raises(EgressBlocked):
                await session.navigate(f"{base}/redirect")
            with pytest.raises(RuntimeError, match="navigation error page"):
                await session.snapshot()
            # so does a click-initiated navigation into a refused hop —
            # commit events cover navigations that bypass navigate()
            allowed.link_to = forbidden_https
            await session.navigate(f"{base}/link")
            await page.click("#go", no_wait_after=True)
            await page.wait_for_timeout(500)
            assert page.url.startswith("chrome-error://")
            with pytest.raises(RuntimeError, match="navigation error page"):
                await session.snapshot()
            await session.navigate(f"{base}/embed")
            await page.wait_for_timeout(500)
            # refused subresources do not mark the real main document
            assert "EMBED" in (await session.snapshot()).text
            with pytest.raises(EgressBlocked):
                await session.navigate(forbidden_https)
            with pytest.raises(RuntimeError, match="navigation error page"):
                await session.snapshot()

    asyncio.run(go())
    assert forbidden.hits == []
    assert any(path == "/redirect" for path, _ in allowed.hits)


@pytest.mark.parametrize("selector", ["title", "article"])
def test_chromium_read_during_navigation_cannot_publish_refusal_source(origins, tmp_path, selector):
    """A selector wait can finish on a different document. The entire
    executor path must reject the refusal page, not publish its title."""
    from agent.llm import ToolCall
    from agent.loop.runner import AgentLoop
    from tests.test_browser_tools import _loop_env

    allowed, forbidden = origins
    allowed.redirect_to = f"http://127.0.0.1:{forbidden.port}/secret"

    async def go():
        async with _chromium_session(forbidden.port) as session:
            page = session.page
            orch, state, tools, events = _loop_env(tmp_path)
            orch.run_context.browser_session = session
            await session.navigate(f"http://127.0.0.1:{allowed.port}/delayed")
            assert await page.locator("title").count() == 0
            result = await AgentLoop(orch)._dispatch(
                ToolCall(id="read", name="browser_extract", arguments={"selector": selector}),
                tools, state, index=1, run_context=orch.run_context,
                user_id="u", user_input="读取页面标题",
            )
            assert "BROWSER_URL_BLOCKED" in result["content"]
            assert events[-1][1]["status"] == "failed"
            assert state["fresh_evidence"] is False
            assert state["plan"] == []
            assert page.url == allowed.redirect_to

    asyncio.run(go())
    assert forbidden.hits == []


def test_chromium_extract_reads_nested_shadow_dom(origins):
    allowed, forbidden = origins

    async def go():
        async with _chromium_session(forbidden.port) as session:
            url = f"http://127.0.0.1:{allowed.port}/"
            await session.navigate(url)
            await session.page.evaluate("""() => {
                const host = document.createElement('div');
                host.id = 'host';
                document.body.append(host);
                const root = host.attachShadow({mode: 'open'});
                root.innerHTML = '<section></section>';
                const nested = root.querySelector('section').attachShadow({mode: 'open'});
                setTimeout(() => nested.innerHTML = '<article>SHADOW-CONTENT</article>', 100);
            }""")
            for selector in ('article', '#host article', 'article:has-text("SHADOW")'):
                read = await session.extract(selector)
                expected = await session.page.inner_text(selector)
                assert read.text == expected == "SHADOW-CONTENT"
                assert read.url == url
            assert (await session.extract('article', limit=6)).text == "SHADOW"

    asyncio.run(go())


@pytest.mark.parametrize("missing", [True, False])
def test_chromium_launch_only_skips_missing_executable(monkeypatch, missing):
    async_api = pytest.importorskip("playwright.async_api")
    message = "Executable doesn't exist at /missing/chromium" if missing else "BROKEN_PROTOCOL"

    async def fail_launch(self, **kwargs):
        raise async_api.Error(message)

    monkeypatch.setattr(async_api.BrowserType, "launch", fail_launch)

    async def go():
        async with _chromium_session(9):
            pytest.fail("launch unexpectedly succeeded")

    expected = pytest.skip.Exception if missing else async_api.Error
    with pytest.raises(expected, match=message):
        asyncio.run(go())
