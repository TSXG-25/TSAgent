"""Per-run browser session — one playwright page bound to a RunContext.

Lazy: the first browser_* tool call in a run starts the browser; the loop's
run() finally closes it.  Read ops are R1, mutating ops (click/type) are R2
and pass the shared approval gate in the dispatcher."""

from __future__ import annotations

from typing import Any


class BrowserSession:
    def __init__(self, playwright: Any, browser: Any, context: Any, page: Any):
        self._pw = playwright
        self._browser = browser
        self._context = context
        self.page = page

    @classmethod
    async def for_context(cls, run_context: Any) -> "BrowserSession":
        existing = getattr(run_context, "browser_session", None)
        if existing is not None:
            return existing
        from playwright.async_api import async_playwright

        pw = await async_playwright().start()
        browser = await pw.chromium.launch()
        context = await browser.new_context()
        page = await context.new_page()
        session = cls(pw, browser, context, page)
        run_context.browser_session = session
        return session

    async def navigate(self, url: str) -> str:
        await self.page.goto(url, wait_until="domcontentloaded")
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
