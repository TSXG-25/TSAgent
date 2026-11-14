/** Mercury search ownership — one request identity per search.
 *
 *  Each `run()` takes a fresh sequence id; a response or failure that
 *  resolves after `stop()` (or after a newer search) is dropped — it
 *  can neither update the panes nor clear a newer request's running
 *  state.  Panes are rebuilt from the current result set, so a result
 *  with fewer sources clears the stale panes instead of leaking them.
 *  (The transport has no abort — Stop invalidates the response.) */

export type MercuryPane = {
  source: Record<string, unknown> | null;
  content: string | null;
};

export type MercurySearchStatus =
  | "idle"
  | "running"
  | "ok"
  | "empty"
  | "unavailable"
  | "error";

export type MercurySearchResult = {
  sources?: Record<string, unknown>[];
  provider?: string;
  content?: string;
};

export class MercurySearchSession {
  private seq = 0;
  listener: (() => void) | null = null;

  sessionId = "";
  status: MercurySearchStatus = "idle";
  running = false;
  err: string | null = null;
  provider = "";
  sourceCount = 0;
  panes: MercuryPane[] = [{ source: null, content: null }];
  private sources: Record<string, unknown>[] = [];

  constructor(
    private readonly search: (
      query: string,
    ) => Promise<MercurySearchResult>,
  ) {}

  private notify() {
    this.listener?.();
  }

  /** Resolves true when this request's outcome was applied — a stopped
   *  or superseded request resolves false without touching the view. */
  async run(query: string): Promise<boolean> {
    const q = query.trim();
    if (!q) return false;
    const id = ++this.seq;
    this.running = true;
    this.status = "running";
    this.err = null;
    this.notify();
    try {
      const res = await this.search(q);
      if (id !== this.seq) return false; // stale response — drop
      this.provider = String(res.provider ?? "");
      const sources = res.sources ?? [];
      this.sources = sources;
      this.sourceCount = sources.length;
      // 按本次结果重建 pane — 结果变少时旧来源不再残留。
      this.panes = this.panes.map((_p, i) => ({
        source: sources[i] ?? null,
        content: i === 0 ? (res.content ?? "") : null,
      }));
      this.status = sources.length ? "ok" : "empty";
      this.running = false;
      this.notify();
      return true;
    } catch (error) {
      if (id !== this.seq) return false; // stale failure — drop
      const message = String(error instanceof Error ? error.message : error);
      this.err = message;
      this.status = /UNAVAILABLE|not installed|unavailable/i.test(message)
        ? "unavailable"
        : "error";
      this.running = false;
      this.notify();
      return true;
    }
  }

  /** Invalidate the in-flight request — its late resolution is dropped. */
  stop(): void {
    this.seq++;
    this.running = false;
    this.notify();
  }

  /** Re-bind to a chat session — a response from a search issued under
   *  a different session must not write this session's view, and the old
   *  session's sources/panes must not leak into the new one. */
  bind(sessionId: string): void {
    if (sessionId !== this.sessionId) this.clear();
    this.sessionId = sessionId;
    this.stop();
  }

  /** Drop all search state — used on session switch. */
  clear(): void {
    this.sources = [];
    this.sourceCount = 0;
    this.provider = "";
    this.err = null;
    this.status = "idle";
    this.panes = [{ source: null, content: null }];
    this.notify();
  }

  addPane(): void {
    if (this.panes.length >= 4) return;
    const i = this.panes.length;
    this.panes = [...this.panes, { source: this.sources[i] ?? null, content: null }];
    this.notify();
  }

  removePane(): void {
    if (this.panes.length <= 1) return;
    this.panes = this.panes.slice(0, -1);
    this.notify();
  }
}
