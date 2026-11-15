/** Workspace file list ownership — every load is tagged with the scope
 *  it was issued for (`sessionId|workspacePath`), so a response can
 *  only land while that exact scope is still current. A same-session
 *  rebind is a new scope: the list refetches and the stale response for
 *  the old root is dropped. Re-binding the same scope while a request
 *  is in flight reuses it; a fresh bind retries a previously failed
 *  load. */

import type { WorkspaceFileSummary } from "../../types/service";

export class WorkspaceFileList {
  files: WorkspaceFileSummary[] = [];
  error: string | null = null;
  listener: (() => void) | null = null;
  private scopeKey = "";
  private seq = 0;

  constructor(
    private readonly list: (
      sessionId: string,
    ) => Promise<WorkspaceFileSummary[]>,
  ) {}

  private notify() {
    this.listener?.();
  }

  /** (Re)load the list for `key = sessionId|workspacePath`. The same
   *  live key with an in-flight or completed load is a no-op; any key
   *  change clears the previous root's entries immediately (the old
   *  list must not serve the new scope), refetches, and invalidates
   *  the previous request. */
  async bindScope(key: string, sessionId: string): Promise<void> {
    if (key === this.scopeKey && this.error === null) return;
    if (key !== this.scopeKey) {
      this.files = [];
      this.error = null;
      this.notify();
    }
    this.scopeKey = key;
    const seq = ++this.seq;
    try {
      const files = await this.list(sessionId);
      if (seq !== this.seq) return;
      this.files = files;
      this.error = null;
    } catch (error) {
      if (seq !== this.seq) return;
      this.error = String(error instanceof Error ? error.message : error);
    }
    this.notify();
  }
}
