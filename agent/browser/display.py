"""Live display of one Playwright page — screencast frames out, CDP input in.

One ``DisplaySession`` wraps the CDP session of an existing ``Page`` (the
same object the Agent drives): ``Page.startScreencast`` yields JPEG frames
with metadata (device size, scroll offset, timestamp); ``Input.*`` events
inject human input into that same page.  Everything here is in-memory —
frames are never persisted and never become screenshot artifacts or
source evidence; explicit screenshots stay on the workspace/artifact path.

Coordinates are CSS pixels in the *viewport* space — the same space the
frame metadata reports via ``scrollOffsetX/Y``; device-pixel frames are
scaled by ``deviceWidth/deviceHeight`` at the viewer side.  Stale frames
are dropped by the subscriber side (a seq counter), never queued for
replay.
"""

from __future__ import annotations

import asyncio
import base64
import time
from typing import Any, Callable

FrameListener = Callable[[bytes, dict], Any]

# Keys that can't go through Input.insertText — (code, windowsVirtualKeyCode,
# key label).  Text entry uses insertText so IME/Unicode input is lossless.
_KEYS: dict[str, tuple[str, int, str]] = {
    "Enter": ("Enter", 13, "Enter"),
    "Backspace": ("Backspace", 8, "Backspace"),
    "Tab": ("Tab", 9, "Tab"),
    "Escape": ("Escape", 27, "Escape"),
    "ArrowLeft": ("ArrowLeft", 37, "ArrowLeft"),
    "ArrowUp": ("ArrowUp", 38, "ArrowUp"),
    "ArrowRight": ("ArrowRight", 39, "ArrowRight"),
    "ArrowDown": ("ArrowDown", 40, "ArrowDown"),
}


class DisplaySession:
    """Screencast + input channel for a single existing Playwright Page."""

    def __init__(self, page: Any):
        self.page = page
        self._cdp: Any | None = None
        self._listeners: list[FrameListener] = []
        self._seq = 0
        self._latest: tuple[int, bytes, dict] | None = None
        self._started_at: float | None = None
        self._first_frame_at: float | None = None
        self.frame_count = 0
        self.frame_bytes = 0

    @property
    def started(self) -> bool:
        return self._cdp is not None

    async def start(self, *, quality: int = 70, every_nth: int = 1) -> None:
        """Begin streaming this page's frames.  Each frame is acked back so
        Chromium keeps producing at its adaptive rate (static pages emit
        only on change)."""
        if self._cdp is not None:
            return
        self._cdp = await self.page.context.new_cdp_session(self.page)
        self._cdp.on("Page.screencastFrame", self._on_frame)
        self._started_at = time.monotonic()
        await self._cdp.send(
            "Page.startScreencast",
            {
                "format": "jpeg",
                "quality": quality,
                "everyNthFrame": every_nth,
            },
        )

    async def stop(self) -> None:
        if self._cdp is None:
            return
        try:
            await self._cdp.send("Page.stopScreencast")
        except Exception:
            pass
        cdp, self._cdp = self._cdp, None
        try:
            await cdp.detach()
        except Exception:
            pass

    def subscribe(self, listener: FrameListener, *, replay_latest: bool = True) -> None:
        """Receive (jpeg_bytes, metadata) per frame.  ``replay_latest`` gives
        a late subscriber the current frame instead of a blank until the
        next change."""
        self._listeners.append(listener)
        if replay_latest and self._latest is not None:
            seq, data, meta = self._latest
            listener(data, {**meta, "_seq": seq, "_replay": True})

    def unsubscribe(self, listener: FrameListener) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def _on_frame(self, event: dict) -> None:
        meta = dict(event.get("metadata") or {})
        data = event.get("data") or ""
        session_id = event.get("sessionId")
        cdp = self._cdp
        if cdp is not None and session_id is not None:
            # Flow control: ack or Chromium stops producing frames.
            asyncio.get_running_loop().create_task(
                cdp.send("Page.screencastFrameAck", {"sessionId": session_id})
            )
        payload = base64.b64decode(data)
        self._seq += 1
        self.frame_count += 1
        self.frame_bytes += len(payload)
        if self._first_frame_at is None:
            self._first_frame_at = time.monotonic()
        self._latest = (self._seq, payload, meta)
        meta = {
            **meta,
            "_seq": self._seq,
            "_received_mono": time.monotonic(),
        }
        for listener in list(self._listeners):
            try:
                listener(payload, meta)
            except Exception:
                pass

    @property
    def latest_seq(self) -> int:
        return self._seq

    @property
    def first_frame_ms(self) -> float | None:
        if self._started_at is None or self._first_frame_at is None:
            return None
        return (self._first_frame_at - self._started_at) * 1000

    # ---- input (CSS-pixel viewport coordinates) ----

    async def click(self, x: float, y: float, *, button: str = "left") -> None:
        for kind, count in (("mousePressed", 1), ("mouseReleased", 1)):
            await self._cdp.send(
                "Input.dispatchMouseEvent",
                {
                    "type": kind,
                    "x": x,
                    "y": y,
                    "button": button,
                    "clickCount": count,
                },
            )

    async def mouse_move(self, x: float, y: float) -> None:
        await self._cdp.send(
            "Input.dispatchMouseEvent",
            {"type": "mouseMoved", "x": x, "y": y},
        )

    async def scroll(self, x: float, y: float, delta_x: float, delta_y: float) -> None:
        await self._cdp.send(
            "Input.dispatchMouseEvent",
            {
                "type": "mouseWheel",
                "x": x,
                "y": y,
                "deltaX": delta_x,
                "deltaY": delta_y,
            },
        )

    async def insert_text(self, text: str) -> None:
        await self._cdp.send("Input.insertText", {"text": text})

    async def key_press(self, name: str) -> None:
        code, vk, key = _KEYS[name]
        await self._cdp.send(
            "Input.dispatchKeyEvent",
            {
                "type": "rawKeyDown",
                "code": code,
                "key": key,
                "windowsVirtualKeyCode": vk,
            },
        )
        await self._cdp.send(
            "Input.dispatchKeyEvent",
            {
                "type": "keyUp",
                "code": code,
                "key": key,
                "windowsVirtualKeyCode": vk,
            },
        )

    async def set_viewport(self, width: int, height: int, *, scale: float = 1.0) -> None:
        """Resize the page's viewport — what a viewer-side window resize maps
        to.  New frames then carry the new deviceWidth/deviceHeight."""
        await self._cdp.send(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": width,
                "height": height,
                "deviceScaleFactor": scale,
                "mobile": False,
            },
        )
