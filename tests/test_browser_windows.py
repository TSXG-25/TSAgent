"""MB-02 window identity & lifecycle contracts — real Chromium.

One BrowserManager owns the driver/browser/proxy; workbench windows share
their session context while run windows stay isolated and die with the
Run.  Skipped without playwright/Chromium.
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright.async_api")

from agent.browser.manager import (  # noqa: E402
    BrowserManager,
    WindowOccupied,
    resolve_browser,
)
from tests.test_browser_display import _Site  # noqa: E402


def _policy(*a):
    return True


@pytest.fixture
def site():
    s = _Site()
    yield s
    s.close()


# pytest-asyncio runs in AUTO mode — async fixtures/tests share one loop,
# which the EgressProxy server needs (it binds on start()).
@pytest.fixture
async def manager():
    m = BrowserManager(policy=_policy)
    yield m
    await m.close()


async def test_workbench_windows_share_session_context(manager, site):
    """Three windows in one session share a context — same-site login
    state crosses views — while staying independent pages."""
    a = await manager.open_window(
        scope_key="t:s1", owner="workbench", url=site.url)
    b = await manager.open_window(scope_key="t:s1", owner="workbench")
    c = await manager.open_window(scope_key="t:s1", owner="workbench")
    assert a.page.context is b.page.context is c.page.context
    assert len({a.view_id, b.view_id, c.view_id}) == 3
    # one window's DOM doesn't leak into another's
    assert "AGENT" not in (await b.snapshot()).text


async def test_sessions_are_isolated(manager, site):
    a = await manager.open_window(
        scope_key="t:s1", owner="workbench", url=site.url)
    b = await manager.open_window(
        scope_key="t:s2", owner="workbench", url=site.url)
    assert a.page.context is not b.page.context


async def test_run_window_is_isolated_and_dies_with_the_run(manager, site):
    w = await manager.open_window(
        scope_key="run:r1", owner="run", run_id="r1", url=site.url)
    wb = await manager.open_window(
        scope_key="t:s1", owner="workbench", url=site.url)
    assert w.page.context is not wb.page.context
    await manager.release_run("r1")
    assert w.closed and w.page.is_closed()
    assert not wb.closed  # workbench untouched
    assert manager.window(w.view_id) is None


async def test_workbench_window_survives_run_release_and_stays_readable(manager, site):
    """A borrowed window outlives the Run: release unbinds, page keeps
    working for the person still looking at it."""
    w = await manager.open_window(
        scope_key="t:s1", owner="workbench", url=site.url)
    manager.acquire("r1", [w.view_id])
    assert w.run_id == "r1" and w.controller == "agent"
    await manager.release_run("r1")
    assert w.run_id is None and w.controller == "user"
    assert not w.closed and not w.page.is_closed()
    assert "line" in (await w.snapshot()).text


async def test_acquire_conflict_names_the_holding_run(manager, site):
    w = await manager.open_window(scope_key="t:s1", owner="workbench")
    manager.acquire("r1", [w.view_id])
    with pytest.raises(WindowOccupied) as held:
        manager.acquire("r2", [w.view_id])
    assert held.value.held_by == "r1"
    # the second run got nothing — all-or-nothing
    assert w.run_id == "r1"
    # closing an occupied window is refused, not retargeted
    with pytest.raises(WindowOccupied):
        await manager.close_window(w.view_id)
    await manager.release_run("r1")
    assert await manager.close_window(w.view_id)
    assert w.closed


async def test_window_identity_tracks_navigation(manager, site):
    """doc_generation + history drive the back/forward contract."""
    w = await manager.open_window(scope_key="t:s1", owner="workbench")
    await w.navigate(site.url)
    base = w.doc_generation
    await w.navigate(f"{site.url}other")
    assert w.doc_generation > base
    summary = await w.summary()
    assert summary["can_back"] and not summary["can_forward"]
    assert summary["url"].endswith("/other")
    await w.back()
    summary = await w.summary()
    assert summary["url"] == site.url and summary["can_forward"]


async def test_window_limit_is_enforced(manager):
    for _ in range(4):
        await manager.open_window(scope_key="t:s1", owner="workbench")
    with pytest.raises(ValueError, match="limit"):
        await manager.open_window(scope_key="t:s1", owner="workbench")


async def test_close_reaps_the_browser_process(manager):
    """Service-end teardown: every context dies with the manager."""
    w = await manager.open_window(scope_key="t:s1", owner="workbench")
    r = await manager.open_window(
        scope_key="run:r1", owner="run", run_id="r1")
    browser = w.page.context.browser
    await manager.close()
    assert not browser.is_connected()
    assert w.page.is_closed() and r.page.is_closed()


async def test_resolve_browser_creates_one_run_window():
    """The executor-facing resolver: no view_id → a lazy run-owned
    window; explicit view_id must be bound to the Run."""
    from agent.runtime_context import ApplicationContext

    app = ApplicationContext()
    manager = BrowserManager(policy=_policy)
    app.browser_manager = manager
    try:
        session = app.create_session("s1", tenant_id="t")
        rc = session.create_run("r1")
        w = await resolve_browser(rc)
        assert w.owner == "run" and w.run_id == "r1"
        assert rc.browser_windows[w.view_id] is w
        assert await resolve_browser(rc) is w  # stable across calls
        with pytest.raises(RuntimeError, match="not bound"):
            await resolve_browser(rc, "vw-unknown")
    finally:
        await manager.close()
