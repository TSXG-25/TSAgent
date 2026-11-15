/** Mars hosted-IDE ownership — scope-guarded start/stop over the Tauri
 *  bridge so a late `ide_start` resolution can never bind an instance to
 *  a session/workspace the user has already left.
 *
 *  The Rust host serialises start/stop under one lock: a `stop()` issued
 *  while `start()` is still waiting for readiness blocks until the spawn
 *  completes and then kills it — no unmanaged child can linger.  This
 *  session adds the UI half of the contract: after a start resolves the
 *  result is dropped unless the scope that requested it is still active.
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
  listener: (() => void) | null = null;
  private scopeKey = "";
  private startingFor = "";

  constructor(private readonly bridge: IdeBridgeLike) {}

  private notify() {
    this.listener?.();
  }

  /** Re-bind to a session|workspace scope. A change releases the hosted
   *  instance — and any start still in flight is marked stale so its
   *  late resolution is dropped rather than shown under the new scope. */
  async bindScope(key: string): Promise<void> {
    if (key === this.scopeKey) return;
    this.scopeKey = key;
    await this.release();
  }

  /** Kill the hosted instance (or a just-resolved stale start) and
   *  forget the recorded info. Safe to call on unmount and exit. */
  async release(): Promise<void> {
    this.info = null;
    this.notify();
    try {
      await this.bridge.stop();
    } catch {
      /* already dead */
    }
  }

  /** Start the IDE for `workspace`, or stop it when already running.
   *  Start failures propagate to the caller for display. */
  async toggle(workspace: string): Promise<void> {
    if (this.info) {
      this.busy = true;
      this.notify();
      await this.release();
      this.busy = false;
      this.notify();
      return;
    }
    if (this.busy) return;
    this.busy = true;
    this.startingFor = this.scopeKey;
    this.notify();
    try {
      const info = await this.bridge.start(workspace);
      if (this.scopeKey !== this.startingFor) {
        // Scope changed mid-start — the host already serialised a kill;
        // belt-and-braces stop is idempotent. Never surface the instance.
        await this.release();
        return;
      }
      this.info = info;
    } finally {
      this.busy = false;
      this.notify();
    }
  }
}
