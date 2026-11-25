"""MB-01 probe contracts — same page, two operators.

Real Chromium through the egress proxy (skipped when playwright/Chromium
is unavailable).  Proves the display channel carries the *same* document
the Agent drives: human input via CDP lands in the page (Playwright reads
it back), an Agent-side DOM change shows up in a later frame, and sibling
windows stay isolated.
"""

from __future__ import annotations

import asyncio
import base64
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("playwright.async_api")  # probe needs a real page

from agent.browser.display import DisplaySession  # noqa: E402
from tests.test_browser_egress import _chromium_session  # noqa: E402

PAGE = """<!doctype html><title>probe</title><body>
<input id="field" style="width:400px">
<button id="mutate">m</button>
<div id="tall">""" + "<p>line</p>" * 400 + """</div>
<script>
document.getElementById('mutate').onclick = () =>
  document.getElementById('tall').insertAdjacentHTML(
    'afterbegin', '<p id="MARK">AGENT-CHANGE</p>');
</script></body>"""


class _Site:
    def __init__(self):
        site = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(PAGE.encode())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/"

    def close(self):
        self.server.shutdown()


@pytest.fixture
def site():
    s = _Site()
    yield s
    s.close()


async def _displayed(page):
    display = DisplaySession(page)
    await display.start()
    got: list[tuple[bytes, dict]] = []
    display.subscribe(lambda d, m: got.append((d, m)))
    return display, got


async def _wait_frames(got, n, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end and len(got) < n:
        await asyncio.sleep(0.02)
    assert len(got) >= n, f"expected {n} frames, got {len(got)}"


def _jpeg_size(data: bytes) -> tuple[int, int]:
    """SOF0/SOF2 marker dims — the frame's real raster size."""
    i = 2
    while i + 9 < len(data):
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC2):
            h = int.from_bytes(data[i + 5:i + 7], "big")
            w = int.from_bytes(data[i + 7:i + 9], "big")
            return w, h
        seg = int.from_bytes(data[i + 2:i + 4], "big")
        i += 2 + seg
    raise AssertionError("no SOF marker in frame")


def test_frames_arrive_at_viewport_size(site):
    async def go():
        async with _chromium_session(9) as session:
            page = session.page
            await page.goto(site.url)
            display, got = await _displayed(page)
            try:
                await _wait_frames(got, 1)
                data, meta = got[-1]
                assert _jpeg_size(data) == (1280, 720)
                assert meta["deviceWidth"] == 1280
                assert meta["deviceHeight"] == 720
                assert display.first_frame_ms is not None
                assert display.first_frame_ms < 5000
            finally:
                await display.stop()

    asyncio.run(go())


def test_input_through_display_reads_back_same_value(site):
    async def go():
        async with _chromium_session(9) as session:
            page = session.page
            await page.goto(site.url)
            display, _ = await _displayed(page)
            try:
                box = await page.locator("#field").bounding_box()
                await display.click(box["x"] + 5, box["y"] + 5)
                text = "uni-input-Δ中文-42"
                await display.insert_text(text)
                assert await page.input_value("#field") == text
            finally:
                await display.stop()

    asyncio.run(go())


def test_agent_change_appears_in_a_later_frame(site):
    async def go():
        async with _chromium_session(9) as session:
            page = session.page
            await page.goto(site.url)
            display, got = await _displayed(page)
            try:
                await _wait_frames(got, 1)
                baseline = got[-1][0]
                # "Agent" mutates the same page via Playwright — the human
                # display channel must surface it in a subsequent frame.
                await page.evaluate(
                    "document.getElementById('mutate').onclick()")
                end = time.monotonic() + 5
                while time.monotonic() < end:
                    if got[-1][0] != baseline:
                        return
                    await asyncio.sleep(0.02)
                pytest.fail("no new frame after agent mutation")
            finally:
                await display.stop()

    asyncio.run(go())


def test_scroll_updates_frame_metadata(site):
    async def go():
        async with _chromium_session(9) as session:
            page = session.page
            await page.goto(site.url)
            display, got = await _displayed(page)
            try:
                await _wait_frames(got, 1)
                await display.scroll(640, 360, 0, 800)
                end = time.monotonic() + 5
                while time.monotonic() < end:
                    if await page.evaluate("scrollY") > 0:
                        break
                    await asyncio.sleep(0.02)
                assert await page.evaluate("scrollY") > 0
                end = time.monotonic() + 5
                while time.monotonic() < end:
                    if got[-1][1]["scrollOffsetY"] > 0:
                        return
                    await asyncio.sleep(0.02)
                pytest.fail("no frame reflects the new scroll offset")
            finally:
                await display.stop()

    asyncio.run(go())


def test_three_windows_do_not_cross_input(site):
    async def go():
        async with _chromium_session(9) as session:
            page_a = session.page
            ctx = page_a.context
            pages = [page_a, await ctx.new_page(), await ctx.new_page()]
            for p in pages:
                await p.goto(site.url)
            displays = []
            for p in pages:
                d = DisplaySession(p)
                await d.start()
                displays.append(d)
            try:
                target = pages[1]
                box = await target.locator("#field").bounding_box()
                await displays[1].click(box["x"] + 5, box["y"] + 5)
                await displays[1].insert_text("only-in-window-B")
                assert await target.input_value("#field") == "only-in-window-B"
                for i in (0, 2):
                    assert await pages[i].input_value("#field") == ""
            finally:
                for d in displays:
                    await d.stop()

    asyncio.run(go())


def test_input_to_next_frame_latency_is_measurable(site):
    async def go():
        async with _chromium_session(9) as session:
            page = session.page
            await page.goto(site.url)
            display, got = await _displayed(page)
            try:
                await _wait_frames(got, 1)
                box = await page.locator("#field").bounding_box()
                await display.click(box["x"] + 5, box["y"] + 5)
                seq, start = got[-1][1]["_seq"], time.monotonic()
                await display.insert_text("latency-probe")
                end = time.monotonic() + 5
                while time.monotonic() < end:
                    if got[-1][1]["_seq"] > seq:
                        break
                    await asyncio.sleep(0.005)
                latency_ms = (time.monotonic() - start) * 1000
                assert got[-1][1]["_seq"] > seq, "no frame after input"
                # Bound is generous; the real p95 number is probe data.
                assert latency_ms < 2000, f"input→frame {latency_ms:.0f}ms"
            finally:
                await display.stop()

    asyncio.run(go())


async def test_key_press_accepts_modifier_chords_and_letters():
    """Cmd/Ctrl chords and Shift+letter reach CDP as keyDown+keyUp with
    the modifier bitmask — _KEYS is no longer an eight-entry allowlist."""
    from agent.browser.display import DisplaySession

    sent = []

    class CDP:
        async def send(self, method, params):
            sent.append((method, params))

    class Page:
        async def context(self):
            return None

        def on(self, *a):
            pass

    display = DisplaySession.__new__(DisplaySession)
    display._cdp = CDP()
    await display.key_press("A", modifiers=("shift",))
    await display.key_press("C", modifiers=("meta",))
    downs = [p for m, p in sent if m == "Input.dispatchKeyEvent"
             and p.get("type") == "rawKeyDown"]
    assert len(downs) == 2
    assert downs[0]["code"] == "KeyA" and downs[0]["modifiers"] == 8
    assert downs[1]["code"] == "KeyC" and downs[1]["modifiers"] == 4
