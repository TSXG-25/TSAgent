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

from agent.browser.egress import (
    BLOCKED_HEADER,
    BLOCKED_META,
    EgressBlocked,
    EgressProxy,
)


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

    async def _document_state(self) -> dict:
        """The document that is committed right now, probed at read time.

        Response/commit events race with the read — a refused CONNECT can
        commit Chromium's error page *after* goto() already raised, and
        click-triggered navigations never pass through navigate() at all.
        So readability is not tracked state: a chrome-error page is a
        navigation failure, and a page carrying the proxy's BLOCKED_META
        marker is the refusal document.  Neither is page content; reading
        either would fake source evidence.
        """
        return await self.page.evaluate(
            "() => ({url: location.href, refused: !!"
            f"document.querySelector('meta[name=\"{BLOCKED_META}\"]')}})"
        )

    async def _read(self, selector: str, limit: int) -> str:
        """Probe the committed document, then read its text.  Either call
        can hit "Execution context was destroyed" while a navigation is
        committing — wait for the page to settle and retry."""
        for attempt in range(3):
            try:
                state = await self._document_state()
                url = str(state.get("url") or "")
                if url.startswith("chrome-error://"):
                    raise RuntimeError(
                        f"the current document is a navigation error "
                        f"page, not page content ({url})"
                    )
                if state.get("refused"):
                    raise EgressBlocked(
                        f"BROWSER_URL_BLOCKED: {url} was refused by the "
                        "egress boundary — the current document is the "
                        "refusal notice, not page content"
                    )
                return (await self.page.inner_text(selector))[:limit]
            except Exception as exc:
                if "Execution context was destroyed" not in str(exc) or attempt == 2:
                    raise
                try:
                    await self.page.wait_for_load_state(
                        "domcontentloaded", timeout=2000
                    )
                except Exception:
                    pass

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

    @property
    def url(self) -> str:
        return self.page.url

    async def snapshot(self, limit: int = 4000) -> str:
        return await self._read("body", limit)

    async def screenshot(self) -> bytes:
        return await self.page.screenshot(full_page=True)

    async def click(self, selector: str) -> str:
        await self.page.click(selector)
        return f"clicked {selector} → {self.page.url}"

    async def type_text(self, selector: str, text: str) -> str:
        await self.page.fill(selector, text)
        return f"typed into {selector}"

    async def extract(self, selector: str, limit: int = 4000) -> str:
        return await self._read(selector, limit)

    async def close(self) -> None:
        await self._browser.close()
        await self._pw.stop()
        await self._proxy.close()
