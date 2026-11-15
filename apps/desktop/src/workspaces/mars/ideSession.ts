/** Mars hosted-IDE ownership — the code-server instance follows the
 *  session|workspace scope: a bound workspace auto-starts it, a scope
 *  change or unmount releases it, and a late `ide_start` resolution can
 *  never bind an instance to a scope the user has already left.
 *
 *  The Rust host serialises start/stop under one lock: a `stop()` issued
 *  while `start()` is still waiting for readiness blocks until the spawn
 *  completes and then kills it — no unmanaged child can linger.  This
 *  session adds the UI half of the contract: every `bindScope`/`release`
 *  takes a generation ticket at entry, so a bind superseded while its
 *  cleanup was still awaiting never issues a start, and a start that
 *  resolves late is dropped unless its ticket is still current —
 *  regardless of whether the scope string matches again (A→B→A).
 *  `release()` is idempotent; stopping a dead instance is a no-op on the
 *  host and a swallowed rejection here. */

import type { IdeInfo } from "../../service/tauriBridge";

export interface IdeBridgeLike {
  start(workspace: string): Promise<IdeInfo>;
  stop(): Promise<void>;
}

export class MarsIdeSession {
  info: IdeInfo | null = null;
  busy = false;
  error: string | null = null;
  listener: (() => void) | null = null;
  private scopeKey = "";
  private gen = 0;

  constructor(private readonly bridge: IdeBridgeLike) {}

  private notify() {
    this.listener?.();
  }

  /** Stop whatever the host currently runs and clear the recorded info —
   *  does not take a ticket; callers own the generation. */
  private async kill(): Promise<void> {
    this.info = null;
    this.notify();
    try {
      await this.bridge.stop();
    } catch {
      /* already dead */
    }
  }

  /** Bind to `key = sessionId|workspacePath`. The hosted IDE is always-on:
   *  any scope change kills the current instance, and a non-empty
   *  `workspace` starts one for the new scope. A bind superseded before
   *  its start issues no spawn; a start resolving late is killed and
   *  dropped. A failed start records `error`; re-binding the same scope
   *  retries it. */
  async bindScope(key: string, workspace: string): Promise<void> {
    if (key === this.scopeKey && this.error === null) return;
    this.scopeKey = key;
    this.error = null;
    const ticket = ++this.gen;
    await this.kill();
    if (this.gen !== ticket) return;
    this.busy = Boolean(workspace);
    this.notify();
    if (!workspace) return;
    try {
      const info = await this.bridge.start(workspace);
      if (this.gen !== ticket) {
        // Released or rebound mid-start — the host already serialised a
        // kill; belt-and-braces stop is idempotent. Never surface it.
        try { await this.bridge.stop(); } catch { /* already dead */ }
        return;
      }
      this.info = info;
    } catch (e) {
      if (this.gen === ticket) this.error = String(e instanceof Error ? e.message : e);
    } finally {
      if (this.gen === ticket) this.busy = false;
      this.notify();
    }
  }

  /** Invalidate every outstanding ticket and kill the hosted instance
   *  (or a just-resolved stale start). Safe to call on unmount/exit. */
  async release(): Promise<void> {
    this.gen++;
    this.error = null;
    this.busy = false;
    await this.kill();
  }
}
