"""MB-01 probe: same-page live display + input over a local WS channel.

What this proves (matches the work order):
- frames streamed from a real Playwright Page render in a browser viewer;
- human input injected via CDP Input lands in that same page and reads
  back identical (unicode included);
- Agent-side Playwright changes show up in the display;
- up to three windows stay isolated.

Run:
    DDG_ALLOW_PRIVATE_URLS=1 .venv/bin/python scripts/probe_browser_display.py [--selfcheck] [--windows N]

Default mode serves a viewer at http://127.0.0.1:8899/ — pick a window,
click/type/scroll/resize inside it.  --selfcheck runs the automated
sequence headlessly and prints metrics.  Local loopback traffic needs the
documented dev override (DDG_ALLOW_PRIVATE_URLS=1); the egress proxy
still handles every request either way.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import struct
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.browser.egress import EgressProxy  # noqa: E402
from agent.browser.display import DisplaySession  # noqa: E402

VIEWER_PORT = 8899

PAGE = """<!doctype html><title>probe site</title><body style="font-family:sans-serif">
<h2>probe site</h2>
<input id="field" placeholder="type here / 在此输入" style="width:60%">
<button id="mutate">mutate DOM</button>
<a id="open" href="/other" target="_self">open other page</a>
<div id="tall">""" + "".join(
    f"<p>row {i:03d} — scrollable content 滚动内容</p>" for i in range(300)
) + """</div>
<script>
document.getElementById('mutate').onclick = () =>
  document.getElementById('tall').insertAdjacentHTML(
    'afterbegin', '<p><b>AGENT-CHANGE ' + Date.now() + '</b></p>');
</script></body>"""

OTHER_PAGE = """<!doctype html><title>other page</title><body>
<h2>other page</h2><input id="other-field" style="width:60%">
<div>""" + "<p>other row</p>" * 120 + "</div></body>"

VIEWER_HTML = """<!doctype html><title>mb01 display probe</title>
<style>
body{font-family:monospace;margin:0;background:#111;color:#ccc}
#bar{padding:6px;display:flex;gap:8px;align-items:center}
#wrap{position:relative;display:inline-block}
#frame{max-width:100%;display:block;outline:none}
.tab{color:#9cf;cursor:pointer;padding:2px 8px;border:1px solid #333}
.tab.on{border-color:#9cf}
</style>
<div id="bar">window:
  <span class="tab" id="t0">0</span><span class="tab" id="t1">1</span>
  <span class="tab" id="t2">2</span><span id="st"></span></div>
<div id="wrap"><img id="frame" tabindex="0"></div>
<script>
const img = document.getElementById('frame');
const st = document.getElementById('st');
let ws = null, devW = 1280, devH = 720;
function connect(w){
  if (ws) ws.close();
  ws = new WebSocket(`ws://${location.host}/ws/${w}`);
  ws.binaryType = 'arraybuffer';
  ws.onmessage = e => {
    const dv = new DataView(e.data);
    const ml = dv.getUint32(0);
    const meta = JSON.parse(new TextDecoder().decode(e.data.slice(4, 4+ml)));
    devW = meta.deviceWidth || devW; devH = meta.deviceHeight || devH;
    img.src = URL.createObjectURL(
      new Blob([e.data.slice(4+ml)], {type:'image/jpeg'}));
    st.textContent = `w${w} seq=${meta._seq} ${devW}x${devH} ` +
      `scroll=${(meta.scrollOffsetY||0).toFixed(0)}`;
  };
  ws.onclose = () => st.textContent = 'disconnected';
}
for (const w of [0,1,2])
  document.getElementById('t'+w).onclick = () => {
    document.querySelectorAll('.tab').forEach(t=>t.classList.remove('on'));
    document.getElementById('t'+w).classList.add('on'); connect(w);
  };
const send = o => ws && ws.readyState === 1 && ws.send(JSON.stringify(o));
const vp = e => { const r = img.getBoundingClientRect();
  return {x:(e.clientX-r.left)*devW/r.width, y:(e.clientY-r.top)*devH/r.height}; };
img.onmousedown = e => { const p = vp(e); img.focus();
  send({t:'click', x:p.x, y:p.y}); e.preventDefault(); };
img.onwheel = e => { const p = vp(e);
  send({t:'scroll', x:p.x, y:p.y, dx:e.deltaX, dy:e.deltaY});
  e.preventDefault(); };
const KEYS = {Enter:1,Backspace:1,Tab:1,Escape:1,ArrowLeft:1,ArrowUp:1,
  ArrowRight:1,ArrowDown:1};
img.onkeydown = e => { if (KEYS[e.key]) {send({t:'key', k:e.key});
  e.preventDefault();} };
img.onkeypress = e => { if (e.key.length === 1)
  send({t:'text', s:e.key}); };
new ResizeObserver(() => send({t:'viewport',
  w:img.clientWidth, h:img.clientHeight})).observe(img);
document.getElementById('t0').classList.add('on'); connect(0);
</script>"""


class _Site:
    def __init__(self):
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                body = OTHER_PAGE if self.path == "/other" else PAGE
                self.wfile.write(body.encode())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}/"


def _pack_frame(data: bytes, meta: dict) -> bytes:
    head = json.dumps(meta).encode()
    return struct.pack(">I", len(head)) + head + data


def _chromium_rss_kb() -> int | None:
    """Best-effort total RSS of playwright-managed Chromium processes."""
    try:
        pids = subprocess.run(
            ["pgrep", "-f", "playwright.*--user-data-dir"],
            capture_output=True, text=True, timeout=3,
        ).stdout.split()
        if not pids:
            return None
        out = subprocess.run(
            ["ps", "-o", "rss=", "-p", ",".join(pids)],
            capture_output=True, text=True, timeout=3,
        ).stdout.split()
        return sum(int(x) for x in out)
    except Exception:
        return None


class _Window:
    def __init__(self, page, display):
        self.page = page
        self.display = display
        self.clients: set = set()
        self._pending: dict = {}
        display.subscribe(self._broadcast)

    def _broadcast(self, data: bytes, meta: dict) -> None:
        if not self.clients:
            return
        blob = _pack_frame(data, meta)
        for ws in list(self.clients):
            if self._pending.get(ws, 0) >= 1:
                continue  # slow client: drop stale frames, never replay
            self._pending[ws] = self._pending.get(ws, 0) + 1
            task = asyncio.get_running_loop().create_task(ws.send(blob))
            task.add_done_callback(
                lambda t, w=ws: self._on_sent(w, t))

    def _on_sent(self, ws, task) -> None:
        self._pending[ws] = max(0, self._pending.get(ws, 0) - 1)
        if task.exception() is not None:
            self.clients.discard(ws)


class _Probe:
    def __init__(self, windows: list[_Window]):
        self.windows = windows
        self.samples_ms: list[float] = []
        self._pending_seq: dict[int, float] = {}

    async def handler(self, ws):
        path = ws.request.path
        if not path.startswith("/ws/"):
            await ws.close(1008, "unknown channel")
            return
        try:
            idx = int(path.removeprefix("/ws/"))
            win = self.windows[idx]
        except (ValueError, IndexError):
            await ws.close(1008, "unknown window")
            return
        win.clients.add(ws)
        try:
            async for raw in ws:
                await self._on_input(win, json.loads(raw))
        finally:
            win.clients.discard(ws)

    async def _on_input(self, win: _Window, msg: dict) -> None:
        d = win.display
        kind = msg.get("t")
        seq = d.latest_seq
        if kind == "click":
            await d.click(msg["x"], msg["y"], button=msg.get("b", "left"))
        elif kind == "scroll":
            await d.scroll(msg["x"], msg["y"], msg["dx"], msg["dy"])
        elif kind == "text":
            await d.insert_text(msg["s"])
        elif kind == "key":
            await d.key_press(msg["k"])
        elif kind == "viewport":
            await d.set_viewport(int(msg["w"]), int(msg["h"]))
        else:
            return
        self._pending_seq[seq] = time.monotonic()

    def note_frame(self, seq: int) -> None:
        for base, t0 in list(self._pending_seq.items()):
            if seq > base:
                self.samples_ms.append((time.monotonic() - t0) * 1000)
                del self._pending_seq[base]


async def _launch(n: int, site_url: str):
    from playwright.async_api import async_playwright

    proxy = EgressProxy()
    port = await proxy.start()
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        proxy={"server": f"http://127.0.0.1:{port}"},
        args=["--proxy-bypass-list=<-loopback>"],
    )
    context = await browser.new_context()
    windows = []
    for _ in range(n):
        page = await context.new_page()
        await page.goto(site_url)
        d = DisplaySession(page)
        await d.start()
        windows.append(_Window(page, d))
    return pw, browser, proxy, windows


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selfcheck", action="store_true",
                    help="run the automated sequence and print metrics")
    ap.add_argument("--windows", type=int, default=3, choices=(1, 2, 3))
    ap.add_argument("--port", type=int, default=VIEWER_PORT)
    args = ap.parse_args()

    site = _Site()
    pw, browser, proxy, windows = await _launch(args.windows, site.url)
    probe = _Probe(windows)
    for w in windows:
        w.display.subscribe(lambda d, m: probe.note_frame(m["_seq"]))

    from websockets.asyncio.server import serve
    from websockets.http11 import Response
    from websockets.datastructures import Headers

    async def process_request(conn, request):
        if request.path == "/":
            return Response(
                200, "OK",
                Headers([("Content-Type", "text/html")]),
                VIEWER_HTML.encode())
        return None

    server = await serve(probe.handler, "127.0.0.1", args.port,
                         process_request=process_request)
    print(f"viewer: http://127.0.0.1:{args.port}/  "
          f"({args.windows} window(s), site {site.url})")

    try:
        if args.selfcheck:
            await _selfcheck(windows, probe, site.url, args.port)
        else:
            await asyncio.Event().wait()  # serve until Ctrl+C
    finally:
        server.close()
        for w in windows:
            await w.display.stop()
        rss = _chromium_rss_kb()
        for w in windows:
            print(f"window: frames={w.display.frame_count} "
                  f"bytes={w.display.frame_bytes} "
                  f"first_frame_ms={w.display.first_frame_ms}")
        if probe.samples_ms:
            s = sorted(probe.samples_ms)
            print(f"input→frame samples n={len(s)} "
                  f"p50={s[len(s)//2]:.0f}ms p95={s[int(len(s)*0.95)]:.0f}ms")
        print(f"chromium_rss_kb={rss}")
        await browser.close()
        await pw.stop()
        await proxy.close()
        site.server.shutdown()


async def _selfcheck(windows, probe, site_url, port):
    """Automated pass: readback, agent-change visibility, isolation,
    a real WS client on the display channel, latency samples."""
    await asyncio.sleep(0.5)  # first frames settle
    page = windows[0].page

    # Real client on the display channel — proves frames + input traverse
    # the same WS the viewer uses, not just the in-process path.
    from websockets.asyncio.client import connect

    async with connect(f"ws://127.0.0.1:{port}/ws/0") as ws:
        await page.evaluate("document.getElementById('mutate').onclick()")
        raw = await asyncio.wait_for(ws.recv(), 5)
        ml = struct.unpack(">I", raw[:4])[0]
        meta = json.loads(raw[4:4 + ml])
        jpeg = raw[4 + ml:]
        assert jpeg[:2] == b"\xff\xd8" and meta["deviceWidth"] == 1280
        # input back through the same channel
        box = await page.locator("#field").bounding_box()
        await ws.send(json.dumps({"t": "click", "x": box["x"] + 5, "y": box["y"] + 5}))
        await ws.send(json.dumps({"t": "text", "s": "via-ws"}))
        end = time.monotonic() + 3
        while time.monotonic() < end:
            if await page.input_value("#field") == "via-ws":
                break
            await asyncio.sleep(0.02)
        assert await page.input_value("#field") == "via-ws"
    print("ws channel OK (frame out + input in)")

    await page.fill("#field", "")
    box = await page.locator("#field").bounding_box()
    await windows[0].display.click(box["x"] + 5, box["y"] + 5)
    text = "selfcheck-输入Δ-7"
    await windows[0].display.insert_text(text)
    assert await page.input_value("#field") == text, "input readback"
    print(f"readback OK: {text!r}")

    seq = windows[0].display.latest_seq
    await page.evaluate("document.getElementById('mutate').onclick()")
    end = time.monotonic() + 5
    while time.monotonic() < end and windows[0].display.latest_seq == seq:
        await asyncio.sleep(0.02)
    assert windows[0].display.latest_seq > seq, "agent change → no frame"
    print("agent-change frame OK")

    if len(windows) > 1:
        box = await windows[1].page.locator("#field").bounding_box()
        await windows[1].display.click(box["x"] + 5, box["y"] + 5)
        await windows[1].display.insert_text("w1-only")
        assert await windows[1].page.input_value("#field") == "w1-only"
        for i in (0, 2):
            if i < len(windows):
                assert await windows[i].page.input_value("#field") != "w1-only"
        print("isolation OK")

    # input→frame latency samples: each text input changes the DOM and
    # triggers a frame; measure dispatch→next-frame.
    for i in range(20):
        seq = windows[0].display.latest_seq
        t0 = time.monotonic()
        await windows[0].display.insert_text(".")
        end = time.monotonic() + 3
        while time.monotonic() < end and windows[0].display.latest_seq == seq:
            await asyncio.sleep(0.005)
        probe.samples_ms.append((time.monotonic() - t0) * 1000)

    # fps while the page is actively mutating
    t0, seq = time.monotonic(), windows[0].display.latest_seq
    async def churn():
        for _ in range(30):
            await page.evaluate(
                "document.getElementById('mutate').onclick()")
            await asyncio.sleep(0.1)
    await churn()
    dt = time.monotonic() - t0
    n = windows[0].display.latest_seq - seq
    print(f"active-window fps≈{n/dt:.1f} ({n} frames/{dt:.1f}s)")

    # viewport change → new dims in frames
    await windows[0].display.set_viewport(800, 600)
    end = time.monotonic() + 3
    got = []
    windows[0].display.subscribe(lambda d, m: got.append(m))
    while time.monotonic() < end and 800 not in {g.get("deviceWidth") for g in got}:
        await asyncio.sleep(0.02)
    assert 800 in {g.get("deviceWidth") for g in got}, "no 800px frame"
    print("viewport resize → 800px frames OK")


if __name__ == "__main__":
    asyncio.run(main())
