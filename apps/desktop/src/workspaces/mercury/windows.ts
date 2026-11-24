/** Mercury live-window board — service windows + per-window display
 *  channel.  Mirrors MercurySearchSession's ownership discipline: a
 *  response that resolves after `bind()` to another session (or after
 *  the board was stopped) is dropped; stale channel sockets die with
 *  the window they were opened for. */

import type {
  DesktopAgentServiceClient,
  WindowSummary,
} from "../../types/service";

export type Frame = { data: Blob; meta: Record<string, unknown> };

/** One window's WS channel — frames out, human input in (plan §4.3).
 *  Latest-wins: a repaint is skipped while one is still drawing. */
export class DisplayChannel {
  private ws: WebSocket | null = null;
  private bitmap: ImageBitmap | null = null;
  private drawing = false;
  private stale = false;
  private closed = false;
  /** Paint callback set by the pane — receives the decoded bitmap. */
  onFrame: ((bmp: ImageBitmap, meta: Record<string, unknown>) => void) | null =
    null;
  onClose: (() => void) | null = null;

  constructor(
    private readonly service: DesktopAgentServiceClient,
    private readonly sessionId: string,
    readonly viewId: string,
  ) {}

  async connect(): Promise<void> {
    const ticket = await this.service.displayTicket({
      sessionId: this.sessionId,
    });
    const ws = new WebSocket(
      `ws://127.0.0.1:${ticket.port}/display/${this.sessionId}/${this.viewId}?token=${ticket.token}`,
    );
    ws.binaryType = "arraybuffer";
    ws.onmessage = (ev) => void this._message(ev.data as ArrayBuffer);
    ws.onclose = () => {
      this.onClose?.();
    };
    ws.onerror = () => {
      ws.close();
    };
    await new Promise<void>((resolve, reject) => {
      ws.onopen = () => resolve();
      const timer = setTimeout(() => reject(new Error("display timeout")), 8000);
      ws.onclose = () => {
        clearTimeout(timer);
        reject(new Error("display closed"));
      };
      // keep original onclose for the post-open path
      ws.addEventListener("open", () => {
        ws.onclose = () => this.onClose?.();
      });
    }).then(() => {
      if (this.closed) ws.close();
    });
    this.ws = ws;
  }

  private async _message(buf: ArrayBuffer): Promise<void> {
    const dv = new DataView(buf);
    const headLen = dv.getUint32(0);
    const meta = JSON.parse(
      new TextDecoder().decode(new Uint8Array(buf, 4, headLen)),
    ) as Record<string, unknown>;
    const data = buf.slice(4 + headLen);
    if (this.drawing) {
      this.stale = true;
      return;
    }
    this.drawing = true;
    try {
      const bmp = await createImageBitmap(new Blob([data]));
      if (this.closed) {
        bmp.close();
        return;
      }
      this.bitmap?.close();
      this.bitmap = bmp;
      this.lastMeta = meta;
      this.onFrame?.(bmp, meta);
    } finally {
      this.drawing = false;
      if (this.stale) {
        this.stale = false;
      }
    }
  }

  send(msg: Record<string, unknown>): void {
    if (this.ws?.readyState === WebSocket.OPEN) this.ws.send(JSON.stringify(msg));
  }

  private lastMeta: Record<string, unknown> = {};

  private stamp(msg: Record<string, unknown>): void {
    // Tag input with the document generation the user saw — the bridge
    // drops anything aimed at a superseded document.
    const g = this.lastMeta.doc_generation;
    if (typeof g === "number") msg.g = g;
    this.send(msg);
  }

  click(x: number, y: number): void {
    this.stamp({ t: "click", x, y });
  }
  down(x: number, y: number): void {
    this.stamp({ t: "down", x, y });
  }
  move(x: number, y: number): void {
    this.stamp({ t: "move", x, y });
  }
  up(x: number, y: number): void {
    this.stamp({ t: "up", x, y });
  }
  scroll(x: number, y: number, dx: number, dy: number): void {
    this.stamp({ t: "scroll", x, y, dx, dy });
  }
  text(s: string): void {
    this.stamp({ t: "text", s });
  }
  key(k: string, m: string[] = []): void {
    this.stamp({ t: "key", k, m });
  }
  viewport(w: number, h: number): void {
    this.send({ t: "viewport", w, h });
  }

  close(): void {
    this.closed = true;
    this.ws?.close();
    this.bitmap?.close();
    this.bitmap = null;
  }
}

type WindowEntry = {
  summary: WindowSummary;
  channel: DisplayChannel | null;
  channelFailed: boolean;
};

/** The workbench's window set for one chat session. */
export class MercuryWindowBoard {
  listener: (() => void) | null = null;
  sessionId = "";
  windows: WindowEntry[] = [];
  activeViewId: string | null = null;
  err: string | null = null;
  /** Whether the backend speaks window ops — mock/other modes say no. */
  available: boolean | null = null;
  private seq = 0;

  constructor(private readonly service: DesktopAgentServiceClient) {}

  private notify() {
    this.listener?.();
  }

  get active(): WindowEntry | null {
    return (
      this.windows.find((w) => w.summary.viewId === this.activeViewId) ?? null
    );
  }

  /** Per-window task selection — checked windows, else the active one.
   *  A send never claims the whole board: claiming A+B once blocks a
   *  second task aimed only at B. */
  selected = new Set<string>();

  toggleSelected(viewId: string): void {
    if (this.selected.has(viewId)) this.selected.delete(viewId);
    else this.selected.add(viewId);
    this.notify();
  }

  /** window ids bound at submission time — a snapshot, never live state. */
  boundIds(): string[] {
    if (this.selected.size) {
      return [...this.selected].filter((id) =>
        this.windows.some((w) => w.summary.viewId === id));
    }
    return this.activeViewId ? [this.activeViewId] : [];
  }

  private entry(viewId: string): WindowEntry | undefined {
    return this.windows.find((w) => w.summary.viewId === viewId);
  }

  private replace(viewId: string, summary: WindowSummary): void {
    const i = this.windows.findIndex((w) => w.summary.viewId === viewId);
    if (i >= 0) this.windows[i] = { ...this.windows[i], summary };
  }

  async refresh(): Promise<boolean> {
    const id = ++this.seq;
    try {
      const list = await this.service.listWindows({
        sessionId: this.sessionId,
      });
      if (id !== this.seq) return false;
      this.available = true;
      const seen = new Set(list.map((w) => w.viewId));
      for (const w of this.windows) {
        if (!seen.has(w.summary.viewId)) w.channel?.close();
      }
      for (const id of [...this.selected]) {
        if (!seen.has(id)) this.selected.delete(id);
      }
      this.windows = list.map((summary) => {
        const prior = this.entry(summary.viewId);
        return {
          summary,
          channel: prior?.channel ?? null,
          channelFailed: prior?.channelFailed ?? false,
        };
      });
      if (!this.activeViewId || !seen.has(this.activeViewId)) {
        this.activeViewId = list[0]?.viewId ?? null;
      }
      this.err = null;
      this.notify();
      return true;
    } catch (error) {
      if (id !== this.seq) return false;
      this.available = false;
      this.err = String(error instanceof Error ? error.message : error);
      this.notify();
      return true;
    }
  }

  async open(url = ""): Promise<void> {
    try {
      const summary = await this.service.openWindow(
        { sessionId: this.sessionId },
        url,
      );
      this.activeViewId = summary.viewId;
      await this.refresh();
    } catch (error) {
      this.err = String(error instanceof Error ? error.message : error);
      this.notify();
    }
  }

  async close(viewId: string): Promise<void> {
    try {
      await this.service.closeWindow({ sessionId: this.sessionId }, viewId);
      this.entry(viewId)?.channel?.close();
      await this.refresh();
    } catch (error) {
      this.err = String(error instanceof Error ? error.message : error);
      this.notify();
    }
  }

  async navigate(viewId: string, action: string, url = ""): Promise<void> {
    try {
      const summary = await this.service.navigateWindow(
        { sessionId: this.sessionId },
        viewId,
        action,
        url,
      );
      this.replace(viewId, summary);
      this.err = null;
      this.notify();
    } catch (error) {
      this.err = String(error instanceof Error ? error.message : error);
      this.notify();
    }
  }

  async claim(viewId: string): Promise<void> {
    try {
      const summary = await this.service.claimWindow(
        { sessionId: this.sessionId }, viewId);
      this.replace(viewId, summary);
      this.notify();
    } catch (error) {
      this.err = String(error instanceof Error ? error.message : error);
      this.notify();
    }
  }

  async release(viewId: string): Promise<void> {
    try {
      const summary = await this.service.releaseWindow(
        { sessionId: this.sessionId }, viewId);
      this.replace(viewId, summary);
      this.notify();
    } catch (error) {
      this.err = String(error instanceof Error ? error.message : error);
      this.notify();
    }
  }

  /** Lazily attach a display channel for a visible pane. */
  async channelFor(viewId: string): Promise<DisplayChannel | null> {
    const entry = this.entry(viewId);
    if (!entry) return null;
    if (entry.channel || entry.channelFailed) return entry.channel;
    const channel = new DisplayChannel(this.service, this.sessionId, viewId);
    try {
      await channel.connect();
    } catch {
      entry.channelFailed = true;
      this.notify();
      return null;
    }
    if (!this.entry(viewId)) {
      channel.close();
      return null;
    }
    entry.channel = channel;
    channel.onClose = () => {
      entry.channel = null;
      this.notify();
    };
    return channel;
  }

  /** Re-bind to a chat session — channels and windows are session-scoped
   *  on the service side; a switched session owns nothing here. */
  bind(sessionId: string): void {
    this.seq++;
    if (sessionId !== this.sessionId) {
      for (const w of this.windows) w.channel?.close();
      this.windows = [];
      this.activeViewId = null;
      this.err = null;
      this.available = null;
    }
    this.sessionId = sessionId;
  }

  stop(): void {
    this.seq++;
    for (const w of this.windows) w.channel?.close();
    this.windows = [];
    this.listener = null;
  }
}
