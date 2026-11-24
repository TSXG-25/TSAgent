"""Browser window manager — the single creation/lookup/close path.

One manager per ApplicationContext owns the playwright driver, the
Chromium browser and the ``EgressProxy``.  Two owner shapes exist
(work-order §4.2):

- workbench windows (``owner="workbench"``) share one BrowserContext per
  session scope (``tenant:session``) so same-site login state crosses
  windows; different sessions stay isolated;
- run windows (``owner="run"``) live in a dedicated context per Run and
  are closed when that Run releases its browser resources.

A Run *borrows* workbench windows via :meth:`BrowserManager.acquire` —
releasing unbinds them without closing pages a person is still looking
at.  ``close()`` reaps every owned context; there is no legacy per-Run
bypass.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Callable, Iterable

from agent.browser.egress import EgressProxy, public_only
from agent.browser.window import BrowserWindow

MAX_WORKBENCH_WINDOWS = 4


class WindowOccupied(RuntimeError):
    """A requested window is already bound to another Run."""

    def __init__(self, view_id: str, held_by: str) -> None:
        self.view_id = view_id
        self.held_by = held_by
        super().__init__(
            f"browser window {view_id} is occupied by run {held_by}"
        )


class BrowserManager:
    """Creates and owns browser contexts/windows for both owner kinds."""

    def __init__(
        self,
        policy: Callable[..., bool] = public_only,
        *,
        max_windows: int = MAX_WORKBENCH_WINDOWS,
    ) -> None:
        self._proxy = EgressProxy(policy=policy)
        self._max_windows = max_windows
        self._pw: Any | None = None
        self._browser: Any | None = None
        self._contexts: dict[str, Any] = {}
        self._windows: dict[str, BrowserWindow] = {}
        self._run_windows: dict[str, list[str]] = {}
        self._lock = asyncio.Lock()
        self._started = False

    @classmethod
    def for_application(cls, application: Any) -> "BrowserManager":
        manager = getattr(application, "browser_manager", None)
        if manager is None:
            manager = cls()
            application.browser_manager = manager
        return manager

    async def _ensure_started(self) -> None:
        if self._started:
            return
        async with self._lock:
            if self._started:
                return
            port = await self._proxy.start()
            from playwright.async_api import async_playwright

            self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(
                proxy={"server": f"http://127.0.0.1:{port}"},
                # Chromium bypasses proxies for loopback by default; the
                # boundary must see those requests too.
                args=["--proxy-bypass-list=<-loopback>"],
            )
            self._started = True

    async def open_window(
        self,
        *,
        scope_key: str,
        owner: str,
        run_id: str | None = None,
        controller: str = "user",
        url: str | None = None,
    ) -> BrowserWindow:
        """Create a window in the owner's context.

        ``scope_key`` is the session scope (``tenant:session``) for
        workbench windows — their context is shared; run windows ignore
        it and always get an isolated context keyed by run.
        """
        if owner == "run":
            context_key = f"run:{run_id}"
        else:
            owned = sum(
                1 for w in self._windows.values()
                if w.owner == "workbench" and w.scope_key == scope_key
                and not w.closed
            )
            if owned >= self._max_windows:
                raise ValueError(
                    f"browser window limit reached ({self._max_windows})"
                )
            context_key = scope_key
        await self._ensure_started()
        context = self._contexts.get(context_key)
        if context is None:
            context = await self._browser.new_context()
            self._contexts[context_key] = context
        page = await context.new_page()
        window = BrowserWindow(
            page,
            self._proxy,
            view_id=f"vw-{uuid.uuid4().hex[:12]}",
            owner=owner,
            scope_key=scope_key,
            run_id=run_id,
            manager=self,
        )
        window.controller = controller
        self._windows[window.view_id] = window
        if owner == "run":
            self._run_windows.setdefault(run_id or "", []).append(
                window.view_id
            )
        if url is not None:
            await window.navigate(url)
        return window

    def window(self, view_id: str) -> BrowserWindow | None:
        window = self._windows.get(view_id)
        return window if window is not None and not window.closed else None

    def windows(self, scope_key: str | None = None) -> list[BrowserWindow]:
        return [
            w
            for w in self._windows.values()
            if not w.closed
            and (scope_key is None or w.scope_key == scope_key)
        ]

    def acquire(
        self, run_id: str, view_ids: Iterable[str]
    ) -> list[BrowserWindow]:
        """Bind existing windows to a Run — all-or-nothing.

        A conflict names the window and the Run holding it so the caller
        can tell the user exactly which view to re-pick.
        """
        windows: list[BrowserWindow] = []
        for view_id in view_ids:
            window = self.window(view_id)
            if window is None:
                raise ValueError(f"unknown browser window: {view_id}")
            if window.run_id not in (None, run_id):
                raise WindowOccupied(view_id, window.run_id)
            windows.append(window)
        for window in windows:
            window.run_id = run_id
            window.controller = "agent"
        return windows

    async def release_run(self, run_id: str) -> None:
        """Drop everything one Run held: close run-owned windows and their
        context; unbind borrowed workbench windows (they stay open)."""
        owned = self._run_windows.pop(run_id, ())
        for view_id in owned:
            window = self._windows.pop(view_id, None)
            if window is not None:
                await window.close()
        context = self._contexts.pop(f"run:{run_id}", None)
        if context is not None:
            await context.close()
        for window in self._windows.values():
            if window.owner == "workbench" and window.run_id == run_id:
                window.run_id = None
                window.controller = "user"

    async def close_window(self, view_id: str) -> bool:
        window = self.window(view_id)
        if window is None:
            return False
        if window.run_id is not None:
            raise WindowOccupied(view_id, window.run_id)
        self._windows.pop(view_id)
        await window.close()
        return True

    async def close(self) -> None:
        windows = list(self._windows.values())
        self._windows.clear()
        self._run_windows.clear()
        for window in windows:
            await window.close()
        for context in self._contexts.values():
            try:
                await context.close()
            except Exception:
                pass
        self._contexts.clear()
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._pw is not None:
            await self._pw.stop()
            self._pw = None
        if self._started:
            await self._proxy.close()
            self._started = False


async def resolve_browser(run_context: Any, view_id: str | None = None) -> Any:
    """ExecutionContext ``open_browser`` — resolve this Run's window.

    No ``view_id``: the single bound window, else a lazily created
    run-owned one.  With ``view_id``: must be bound to this Run.
    """
    windows = run_context.browser_windows
    if view_id is not None:
        window = windows.get(view_id)
        if window is None:
            raise RuntimeError(
                "this run is not bound to browser window "
                f"{view_id} — bound: {sorted(windows)}"
            )
        return window
    if not windows:
        manager = BrowserManager.for_application(run_context.application)
        window = await manager.open_window(
            scope_key=f"run:{run_context.run_id}",
            owner="run",
            run_id=run_context.run_id,
            controller="agent",
        )
        windows[window.view_id] = window
        return window
    if len(windows) == 1:
        return next(iter(windows.values()))
    raise RuntimeError(
        "this run is bound to several windows — the tool call must "
        f"name view_id ({sorted(windows)})"
    )


async def release_run_browsers(run_context: Any) -> None:
    """Run-end release — close run-owned, unbind borrowed, keep none."""
    windows = getattr(run_context, "browser_windows", None) or {}
    run_context.browser_windows = {}
    if not windows:
        return
    managers = {
        w.manager for w in windows.values()
        if getattr(w, "manager", None) is not None
    }
    for manager in managers:
        await manager.release_run(run_context.run_id)
    for window in windows.values():
        if getattr(window, "manager", None) is None:
            await window.close()
