"""Per-run browser session — one playwright page bound to a RunContext.

Lazy: the first browser_* tool call in a run starts the browser; the loop's
run() finally closes it.  Read ops are R1, mutating ops (click/type) are R2
and pass the shared approval gate in the dispatcher.

Network boundary: Chromium runs behind ``EgressProxy`` (see
``agent.browser.egress``), which checks every destination — including each
redirect hop and subresource — against the same public-address boundary as
web_fetch.  Non-proxied schemes (file:, data:, chrome:, …) are refused
before navigation."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from agent.browser.egress import BLOCKED_HEADER, EgressBlocked, EgressProxy


def check_scheme(url: str) -> None:
    scheme = urlparse(url).scheme
    if scheme not in {"http", "https"}:
        raise EgressBlocked(
            f"refusing to load {url}: scheme {scheme!r} is not http(s)"
        )


class BrowserSession:
    def __init__(self, playwright: Any, browser: Any, proxy: EgressProxy, page: Any):
        self._pw = playwright
        self._browser = browser
        self._proxy = proxy
        self.page = page

    @classmethod
    async def for_context(cls, run_context: Any) -> "BrowserSession":
        existing = getattr(run_context, "browser_session", None)
        if existing is not None:
            return existing
        from playwright.async_api import async_playwright

        proxy = EgressProxy()
        port = await proxy.start()
        pw = await async_playwright().start()
        browser = await pw.chromium.launch(
            proxy={"server": f"http://127.0.0.1:{port}"},
            # Chromium bypasses proxies for loopback by default; the
            # boundary must see those requests too.
            args=["--proxy-bypass-list=<-loopback>"],
        )
        context = await browser.new_context()
        session = cls(pw, browser, proxy, await context.new_page())
        run_context.browser_session = session
        return session

    async def navigate(self, url: str) -> str:
        check_scheme(url)
        seen = len(self._proxy.blocked)
        try:
            response = await self.page.goto(url, wait_until="domcontentloaded")
        except Exception:
            # A refused CONNECT (https hop into a blocked address) aborts the
            # navigation — report the boundary's reason, not net::ERR_*.
            if len(self._proxy.blocked) > seen:
                raise EgressBlocked(self._proxy.blocked[-1]) from None
            raise
        if response is not None and response.headers.get(BLOCKED_HEADER.lower()):
            raise EgressBlocked(self._proxy.blocked[-1])
        return f"{await self.page.title()} — {self.page.url}"

    async def snapshot(self, limit: int = 4000) -> str:
        text = await self.page.inner_text("body")
        return text[:limit]

    async def screenshot(self, path: str) -> str:
        await self.page.screenshot(path=path, full_page=True)
        return f"{self.page.url} → {path}"

    async def click(self, selector: str) -> str:
        await self.page.click(selector)
        return f"clicked {selector} → {self.page.url}"

    async def type_text(self, selector: str, text: str) -> str:
        await self.page.fill(selector, text)
        return f"typed into {selector}"

    async def extract(self, selector: str, limit: int = 4000) -> str:
        text = await self.page.inner_text(selector)
        return text[:limit]

    async def close(self) -> None:
        await self._browser.close()
        await self._pw.stop()
        await self._proxy.close()
