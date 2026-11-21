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
            f"BROWSER_URL_BLOCKED: refusing to load {url}: "
            f"scheme {scheme!r} is not http(s)"
        )


class BrowserSession:
    def __init__(self, playwright: Any, browser: Any, proxy: EgressProxy, page: Any):
        self._pw = playwright
        self._browser = browser
        self._proxy = proxy
        self.page = page
        # The proxy answers a refused request with a marked 403 document,
        # and an aborted navigation leaves Chromium's error page; neither
        # is page content, and reading either would turn the boundary's
        # refusal into fake source evidence.  Refusals are tracked via the
        # response's BLOCKED_HEADER, never by matching body text.
        self._refused_document = False
        self._aborted_document = False
        self._document_loaded = False
        page.on("response", self._track_document_refusal)

    def _track_document_refusal(self, response: Any) -> None:
        request = response.request
        if request.resource_type != "document" or request.frame.parent_frame is not None:
            return
        self._document_loaded = True
        self._refused_document = (
            response.headers.get(BLOCKED_HEADER.lower()) is not None
        )
        if not self._refused_document:
            self._aborted_document = False

    def _require_readable_document(self) -> None:
        if self._refused_document:
            raise EgressBlocked(
                f"BROWSER_URL_BLOCKED: {self.page.url} was refused by the "
                "egress boundary — the current document is the refusal "
                "notice, not page content"
            )
        if self._aborted_document:
            raise RuntimeError(
                f"the current document is a navigation error page, not "
                f"page content ({self.page.url})"
            )

    @classmethod
    async def for_context(cls, run_context: Any) -> "BrowserSession":
        if run_context.browser_session is not None:
            return run_context.browser_session
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
        self._document_loaded = False
        try:
            response = await self.page.goto(url, wait_until="domcontentloaded")
        except Exception:
            # No main-frame document arrived → the current doc is an error
            # page, not content.  A refused CONNECT (https hop into a
            # blocked address) reports the boundary's reason, not net::ERR_*.
            if not self._document_loaded:
                self._aborted_document = True
                if len(self._proxy.blocked) > seen:
                    self._refused_document = True
            if len(self._proxy.blocked) > seen:
                raise EgressBlocked(self._proxy.blocked[-1]) from None
            raise
        if response is not None and response.headers.get(BLOCKED_HEADER.lower()):
            raise EgressBlocked(self._proxy.blocked[-1])
        return f"{await self.page.title()} — {self.page.url}"

    @property
    def url(self) -> str:
        return self.page.url

    async def snapshot(self, limit: int = 4000) -> str:
        self._require_readable_document()
        text = await self.page.inner_text("body")
        return text[:limit]

    async def screenshot(self) -> bytes:
        return await self.page.screenshot(full_page=True)

    async def click(self, selector: str) -> str:
        await self.page.click(selector)
        return f"clicked {selector} → {self.page.url}"

    async def type_text(self, selector: str, text: str) -> str:
        await self.page.fill(selector, text)
        return f"typed into {selector}"

    async def extract(self, selector: str, limit: int = 4000) -> str:
        self._require_readable_document()
        text = await self.page.inner_text(selector)
        return text[:limit]

    async def close(self) -> None:
        await self._browser.close()
        await self._pw.stop()
        await self._proxy.close()
