"""A browser window — one Playwright Page with identity and ownership.

Windows are created and closed by ``agent.browser.manager.BrowserManager``,
never by a Run directly.  A window's ``owner`` decides its lifetime:

- ``"workbench"`` windows live in the session's shared context and may be
  *borrowed* by a Run (``run_id`` bound, ``controller == "agent"``);
  releasing the Run unbinds them without closing the page.
- ``"run"`` windows live in a dedicated context owned by one Run and are
  closed when that Run releases its browser resources.

Read ops are R1, mutating ops (click/type) are R2 and pass the shared
approval gate in the dispatcher.  The page sits behind the manager's
``EgressProxy`` — the same SSRF boundary as before.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from agent.browser.egress import (
    BLOCKED_HEADER,
    BLOCKED_META,
    EgressBlocked,
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


class BrowserWindow:
    """One page the workbench or a Run may operate on.

    ``doc_generation`` counts committed main-frame documents and ``_history``
    tracks them for the window's back/forward contract — both are part of
    the window identity the display channel and approval binding rely on.
    """

    def __init__(
        self,
        page: Any,
        proxy: Any,
        *,
        view_id: str,
        owner: str,
        scope_key: str,
        run_id: str | None = None,
        manager: Any = None,
    ) -> None:
        self.page = page
        self._proxy = proxy
        self.view_id = view_id
        self.owner = owner
        self.scope_key = scope_key
        self.run_id = run_id
        self._controller = "user"
        self.control_epoch = 0  # bumps on every controller change —
        # approval binding survives a takeover→release roundtrip
        self.manager = manager
        self.doc_generation = 0
        self.display: Any | None = None
        self._closed = False
        self._loading = False
        self._history: list[str] = []
        self._history_index = -1
        page.on("framenavigated", self._on_frame_navigated)
        page.on("load", self._on_settled)
        page.on("domcontentloaded", self._on_settled)

    # ---- window state ----

    @property
    def controller(self) -> str:
        return self._controller

    @controller.setter
    def controller(self, value: str) -> None:
        if value != getattr(self, "_controller", None):
            self.control_epoch = getattr(self, "control_epoch", 0) + 1
        self._controller = value

    @property
    def closed(self) -> bool:
        return self._closed

    def _on_frame_navigated(self, frame: Any) -> None:
        if frame.parent_frame is not None:
            return
        self.doc_generation += 1
        self._loading = True
        url = frame.url
        history, index = self._history, self._history_index
        if index > 0 and url == history[index - 1]:
            self._history_index = index - 1
        elif index + 1 < len(history) and url == history[index + 1]:
            self._history_index = index + 1
        else:
            del history[index + 1:]
            history.append(url)
            self._history_index += 1

    def _on_settled(self, *args: Any) -> None:
        self._loading = False

    async def summary(self) -> dict[str, Any]:
        """The public window card — only fields the UI needs."""
        try:
            title = await self.page.title()
        except Exception:
            title = ""
        url = self.page.url
        return {
            "view_id": self.view_id,
            "title": title,
            "url": url,
            "loading": self._loading,
            "error": url.startswith("chrome-error://"),
            "can_back": self._history_index > 0,
            "can_forward": self._history_index + 1 < len(self._history),
            "controller": self.controller,
            "run_id": self.run_id,
            "owner": self.owner,
            "doc_generation": self.doc_generation,
        }

    async def ensure_display(self) -> Any:
        """Attach this window's live-display channel (screencast + input)."""
        if self.display is None:
            from agent.browser.display import DisplaySession

            self.display = DisplaySession(self.page)
            await self.display.start()
        return self.display

    # ---- page operations (same document contract as before) ----

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

    async def back(self) -> str:
        await self.page.go_back()
        return self.page.url

    async def forward(self) -> str:
        await self.page.go_forward()
        return self.page.url

    async def reload(self) -> str:
        await self.page.reload()
        return self.page.url

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
        if self._closed:
            return
        self._closed = True
        if self.display is not None:
            await self.display.stop()
        try:
            await self.page.close()
        except Exception:
            pass
