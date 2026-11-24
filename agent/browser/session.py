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

import asyncio
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from agent.browser.egress import (
    BLOCKED_HEADER,
    BLOCKED_META,
    EgressBlocked,
    EgressProxy,
)


@dataclass(frozen=True)
class BrowserRead:
    text: str
    url: str


_READ_DOCUMENT = """(elements, marker) => {
    const doc = elements.length ? elements[0].ownerDocument : document;
    const url = doc.location.href;
    const refused = !!doc.querySelector(`meta[name="${marker}"]`);
    if (refused || url.startsWith('chrome-error://'))
        return {url, refused, text: ''};
    return elements.length ? {url, refused, text: elements[0].innerText} : null;
}"""


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

    async def _read(self, selector: str, limit: int) -> BrowserRead:
        """Wait for an element or a failed document, then capture validity,
        text and URL in one synchronous browser evaluation.  The returned
        object contains values, not a live DOM reference."""
        from playwright.async_api import Error as PlaywrightError
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError

        for attempt in range(3):
            try:
                # evaluate_all preserves Playwright's selector semantics,
                # including open shadow roots. It also runs with no matches,
                # so a failed document can end the wait for a missing element.
                async with asyncio.timeout(30):
                    while True:
                        read = await self.page.locator(selector).evaluate_all(
                            _READ_DOCUMENT, BLOCKED_META,
                        )
                        if read is not None:
                            break
                        await asyncio.sleep(0.05)
                url = read["url"]
                if url.startswith("chrome-error://"):
                    raise RuntimeError(
                        f"the current document is a navigation error "
                        f"page, not page content ({url})"
                    )
                if read["refused"]:
                    raise EgressBlocked(
                        f"BROWSER_URL_BLOCKED: {url} was refused by the "
                        "egress boundary — the current document is the "
                        "refusal notice, not page content"
                    )
                return BrowserRead(text=read["text"][:limit], url=url)
            except TimeoutError as exc:
                raise PlaywrightTimeoutError(
                    f"Timeout 30000ms exceeded waiting to read {selector!r}"
                ) from exc
            except PlaywrightError as exc:
                if "Execution context was destroyed" not in str(exc) or attempt == 2:
                    raise
                try:
                    await self.page.wait_for_load_state(
                        "domcontentloaded", timeout=2000
                    )
                except PlaywrightTimeoutError:
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

    async def snapshot(self, limit: int = 4000) -> BrowserRead:
        return await self._read("body", limit)

    async def screenshot(self) -> bytes:
        return await self.page.screenshot(full_page=True)

    async def click(self, selector: str) -> str:
        await self.page.click(selector)
        return f"clicked {selector} → {self.page.url}"

    async def type_text(self, selector: str, text: str) -> str:
        await self.page.fill(selector, text)
        return f"typed into {selector}"

    async def extract(self, selector: str, limit: int = 4000) -> BrowserRead:
        return await self._read(selector, limit)

    async def close(self) -> None:
        await self._browser.close()
        await self._pw.stop()
        await self._proxy.close()
