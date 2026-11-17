import {
  AgentServiceClientError,
  type DesktopAgentServiceClient,
  type EventStreamRequest,
  type GetRunRequest,
  type ResumeRunRequest,
  type RunEvent,
  type RunSnapshot,
  type ServiceErrorDTO,
  type StartRunRequest,
} from "../../types/service";
import type { RunView } from "../../types";
import { foldAssistantNotes, foldLiveDelta, mergeRunEvents, projectRuntimeWorkflow, toRunView } from "../../service/viewMapper";

export interface RunControllerState {
  runs: RunView[];
  activeRunId: string;
  isLoading: boolean;
  error: ServiceErrorDTO | null;
  operationState: "idle" | "starting" | "resuming" | "cancelling" | "pausing";
}

export interface RunControllerOptions {
  pollIntervalMs?: number;
}

type Listener = (state: RunControllerState) => void;

interface CursorState {
  lastSequence: number;
  eventIds: Set<string>;
}

const DEFAULT_POLL_INTERVAL_MS = 250;

function serviceError(error: unknown): ServiceErrorDTO {
  if (error instanceof AgentServiceClientError) return error.toDTO();
  return {
    code: "INTERNAL_ERROR",
    message: "AgentService returned an unexpected error.",
    retryable: false,
  };
}

function isLiveStatus(status: RunView["status"]): boolean {
  return status === "pending" || status === "active" || status === "cancelling";
}

function locator(snapshot: Pick<RunSnapshot, "sessionId" | "runId">): GetRunRequest {
  return {
    sessionId: snapshot.sessionId,
    runId: snapshot.runId,
  };
}

/**
 * Application-layer coordinator for one desktop AgentService client.
 *
 * It owns only UI hydration concerns: durable cursors, event de-duplication,
 * snapshot projection and polling lifecycle. Runtime decisions remain in the
 * Python AgentService and are consumed as Snapshot/Event facts.
 */
export class RunController {
  private readonly runs = new Map<string, RunView>();
  private readonly cursors = new Map<string, CursorState>();
  private readonly listeners = new Set<Listener>();
  private readonly pollTimers = new Map<string, { stop(): void }>();
  private pollingStopped = false;
  private readonly inFlight = new Set<string>();
  private readonly pollIntervalMs: number;
  private activeRunId = "";
  private currentError: ServiceErrorDTO | null = null;
  // What raised currentError — a background read clears only an error it
  // raised itself; operation errors stay until dismissed or superseded.
  private errorSource: string | null = null;
  private readonly controlOps = new Map<string, "resuming" | "cancelling" | "pausing">();
  private startingCount = 0;
  private get operationState(): RunControllerState["operationState"] {
    if (this.startingCount > 0) return "starting";
    return this.controlOps.get(this.activeRunId) ?? "idle";
  }
  // Per-session catalog batches — the store is a union of every loaded
  // scope, so a reload invalidates only that session's in-flight batch:
  // a late response for session A can never commit onto session B.
  private readonly scopeSeq = new Map<string, number>();
  private readonly loadingScopes = new Set<string>();
  private loadedOnce = false;

  constructor(
    private readonly client: DesktopAgentServiceClient,
    options: RunControllerOptions = {},
  ) {
    this.pollIntervalMs = options.pollIntervalMs ?? DEFAULT_POLL_INTERVAL_MS;
    if (!Number.isFinite(this.pollIntervalMs) || this.pollIntervalMs <= 0) {
      throw new Error("pollIntervalMs must be a positive finite number");
    }
  }

  getState(): RunControllerState {
    return {
      runs: [...this.runs.values()],
      activeRunId: this.activeRunId,
      isLoading: this.loadingScopes.size > 0 || !this.loadedOnce,
      error: this.currentError,
      operationState: this.operationState,
    };
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    listener(this.getState());
    return () => this.listeners.delete(listener);
  }

  /** (Re)load one session scope's run catalog. Scopes are independent:
   *  loading session B replaces only session-B entries in the shared store;
   *  other scopes' runs and their live polls are untouched. */
  async loadScope(sessionId: string): Promise<void> {
    const seq = (this.scopeSeq.get(sessionId) ?? 0) + 1;
    this.scopeSeq.set(sessionId, seq);
    this.loadingScopes.add(sessionId);
    this.emit();
    try {
      await this.client.ready();
      const snapshots = await this.client.listRuns({ sessionId });
      if (seq !== this.scopeSeq.get(sessionId)) return;
      for (const [runId, view] of this.runs) {
        if (view.sessionId === sessionId) {
          this.runs.delete(runId);
          this.cursors.delete(runId);
        }
      }
      const views = await Promise.all(snapshots.map((snapshot) => this.hydrateRun(snapshot, -1)));
      if (seq !== this.scopeSeq.get(sessionId)) return;
      for (const view of views) this.runs.set(view.runId, view);
      if (!this.runs.has(this.activeRunId)) this.activeRunId = views[0]?.runId ?? "";
      this.clearErrorFrom(`scope:${sessionId}`);
      this.emit();
      for (const view of views) this.startPollingIfLive(view);
    } catch (error) {
      if (seq === this.scopeSeq.get(sessionId)) this.setError(error, `scope:${sessionId}`);
    } finally {
      if (seq === this.scopeSeq.get(sessionId)) {
        this.loadingScopes.delete(sessionId);
        this.loadedOnce = true;
        this.emit();
      }
    }
  }

  /** Tenant-wide catalog — Omni's aggregate view, or one planet's runs
   *  across all its sessions (delegated child runs included). Batches are
   *  gated per originPlanet key and evict only covered sessions' stale
   *  entries; other scopes' slices stay untouched. */
  async loadTenantCatalog(originPlanet?: string): Promise<void> {
    const key = `tenant:${originPlanet ?? "*"}`;
    const seq = (this.scopeSeq.get(key) ?? 0) + 1;
    this.scopeSeq.set(key, seq);
    this.loadingScopes.add(key);
    this.emit();
    try {
      await this.client.ready();
      const snapshots = await this.client.listRuns({
        sessionId: "",
        originPlanet,
      });
      if (seq !== this.scopeSeq.get(key)) return;
      const covered = new Map<string, Set<string>>();
      for (const snapshot of snapshots) {
        const set = covered.get(snapshot.sessionId) ?? new Set<string>();
        set.add(snapshot.runId);
        covered.set(snapshot.sessionId, set);
      }
      for (const [runId, view] of this.runs) {
        const owned = covered.get(view.sessionId);
        if (owned && !owned.has(runId)) {
          this.runs.delete(runId);
          this.cursors.delete(runId);
        }
      }
      const views = await Promise.all(snapshots.map((snapshot) => this.hydrateRun(snapshot, -1)));
      if (seq !== this.scopeSeq.get(key)) return;
      for (const view of views) this.runs.set(view.runId, view);
      if (!this.runs.has(this.activeRunId)) this.activeRunId = views[0]?.runId ?? "";
      this.clearErrorFrom(key);
      this.emit();
      for (const view of views) this.startPollingIfLive(view);
    } catch (error) {
      if (seq === this.scopeSeq.get(key)) this.setError(error, key);
    } finally {
      if (seq === this.scopeSeq.get(key)) {
        this.loadingScopes.delete(key);
        this.loadedOnce = true;
        this.emit();
      }
    }
  }

  async startRun(request: StartRunRequest): Promise<RunView> {
    // Parallel acceptance: every submission is independently admitted; the
    // returned view is always the Run this request created, never an
    // unrelated in-flight Run.
    this.startingCount += 1;
    this.emit();
    this.dismissError();
    try {
      const handle = await this.client.startRun(request);
      const requestLocator = locator(handle);
      const snapshot = await this.client.getRun(requestLocator);
      const view = await this.hydrateRun(snapshot, -1);
      this.promote(view.runId, view);
      this.activeRunId = view.runId;
      this.emit();
      this.startPollingIfLive(view);
      return view;
    } catch (error) {
      this.setError(error);
      throw error;
    } finally {
      this.startingCount -= 1;
      this.emit();
    }
  }

  async resumeRun(runId: string): Promise<RunView | undefined> {
    const current = this.runs.get(runId);
    if (!current || !current.resume || current.resume.action === null) return current;
    if (this.controlOps.has(runId)) return current;

    const request: ResumeRunRequest = {
      sessionId: current.sessionId,
      runId: current.runId,
      resumeRequestId: `${current.runId}.resume-${crypto.randomUUID()}`,
      checkpointId: current.resume.checkpointId,
      action: current.resume.action,
    };
    this.controlOps.set(runId, "resuming");
    this.dismissError();
    this.emit();
    try {
      await this.client.resumeRun(request);
      return await this.refreshRun(runId);
    } catch (error) {
      this.setError(error);
      throw error;
    } finally {
      this.controlOps.delete(runId);
      this.emit();
    }
  }

  async cancelRun(runId: string): Promise<RunView | undefined> {
    const current = this.runs.get(runId);
    if (!current || current.status !== "active" || this.controlOps.has(runId)) return current;

    this.controlOps.set(runId, "cancelling");
    this.dismissError();
    this.emit();
    try {
      const snapshot = await this.client.cancelRun({
        sessionId: current.sessionId,
        runId: current.runId,
        requestId: `cancel-${current.runId}`,
        requestedBy: this.client.identity.userId,
      });
      return await this.applySnapshot(snapshot, current);
    } catch (error) {
      this.setError(error);
      throw error;
    } finally {
      this.controlOps.delete(runId);
      this.emit();
    }
  }

  async pauseRun(runId: string): Promise<RunView | undefined> {
    const current = this.runs.get(runId);
    if (!current || current.status !== "active" || this.controlOps.has(runId)) return current;
    this.controlOps.set(runId, "pausing");
    this.dismissError();
    this.emit();
    try {
      await this.client.pauseRun({
        sessionId: current.sessionId,
        runId: current.runId,
        requestedBy: this.client.identity.userId,
      });
      this.inFlight.delete(runId);
      return await this.refreshRun(runId);
    } catch (error) {
      this.setError(error);
      throw error;
    } finally {
      this.controlOps.delete(runId);
      this.emit();
    }
  }

  async refreshRun(runId: string, sessionId?: string, prefetchedEvents?: RunEvent[]): Promise<RunView | undefined> {
    const current = this.runs.get(runId);
    if (this.inFlight.has(runId)) return current;

    const runSessionId = sessionId ?? current?.sessionId;
    if (!runSessionId) {
      const error = new AgentServiceClientError({
        code: "INVALID_REQUEST",
        message: "refreshRun requires a session locator for a run that is not in the catalog",
        retryable: false,
      });
      this.setError(error, `run:${runId}`);
      throw error;
    }
    this.inFlight.add(runId);
    try {
      const request = locator({
        sessionId: runSessionId,
        runId,
      });
      const snapshot = await this.client.getRun(request);
      return await this.applySnapshot(snapshot, current, prefetchedEvents);
    } catch (error) {
      this.setError(error, `run:${runId}`);
      throw error;
    } finally {
      this.inFlight.delete(runId);
    }
  }

  selectRun(runId: string): void {
    if (!this.runs.has(runId)) return;
    this.activeRunId = runId;
    this.emit();
  }

  stopAllPolling(): void {
    this.pollingStopped = true;
    for (const runId of this.pollTimers.keys()) this.stopPolling(runId);
  }

  dispose(): void {
    this.stopAllPolling();
    this.listeners.clear();
  }

  private async applySnapshot(
    snapshot: RunSnapshot,
    current: RunView | undefined,
    prefetchedEvents?: RunEvent[],
  ): Promise<RunView> {
    const request = locator(snapshot);
    const afterSequence = this.cursors.get(snapshot.runId)?.lastSequence ?? -1;
    const [artifacts, incomingEvents] = await Promise.all([
      this.client.listArtifacts(request),
      prefetchedEvents ?? this.client.readEvents({ ...request, afterSequence } satisfies EventStreamRequest),
    ]);

    const cursor = this.cursors.get(snapshot.runId) ?? { lastSequence: afterSequence, eventIds: new Set<string>() };
    const unseenEvents = incomingEvents.filter((event) => !cursor.eventIds.has(event.eventId));
    for (const event of incomingEvents) cursor.eventIds.add(event.eventId);
    cursor.lastSequence = Math.max(afterSequence, ...incomingEvents.map((event) => event.sequenceNumber));
    this.cursors.set(snapshot.runId, cursor);

    const next = toRunView(snapshot, artifacts, []);
    next.events = mergeRunEvents(current?.events ?? [], unseenEvents);
    next.conversation = foldLiveDelta(
      foldAssistantNotes(next.conversation, next.events),
      next.events,
    );
    this.projectRuntimeWorkflowIfMissing(next);
    // The store is a union across loaded scopes keyed by runId — every
    // view carries its own session locator, so a refreshed run lands in
    // its owning scope without a per-session write gate.
    this.runs.set(snapshot.runId, next);
    this.clearErrorFrom(`run:${snapshot.runId}`);
    this.emit();
    if (isLiveStatus(next.status)) this.startPollingIfLive(next);
    else this.stopPolling(snapshot.runId);
    return next;
  }

  private async hydrateRun(snapshot: RunSnapshot, afterSequence: number): Promise<RunView> {
    const request = locator(snapshot);
    const [artifacts, events] = await Promise.all([
      this.client.listArtifacts(request),
      this.readAllEvents(request, afterSequence),
    ]);
    const view = toRunView(snapshot, artifacts, events);
    this.projectRuntimeWorkflowIfMissing(view);
    const eventIds = new Set(events.map((event) => event.eventId));
    this.cursors.set(snapshot.runId, {
      lastSequence: Math.max(afterSequence, ...events.map((event) => event.sequenceNumber)),
      eventIds,
    });
    return view;
  }

  /** Drains the event stream — the sidecar may end a batch at a terminal
   *  event (run_blocked) even when history continues, so a short page is not
   *  end-of-stream.  Page until an empty batch or no cursor progress. */
  private async readAllEvents(request: Omit<EventStreamRequest, "afterSequence">, afterSequence: number): Promise<RunEvent[]> {
    const pageSize = request.limit ?? 100;
    const events: RunEvent[] = [];
    let cursor = afterSequence;
    for (;;) {
      const batch = await this.client.readEvents({ ...request, afterSequence: cursor, limit: pageSize });
      if (batch.length === 0) return events;
      events.push(...batch);
      const last = batch[batch.length - 1].sequenceNumber;
      if (last <= cursor) return events;
      cursor = last;
    }
  }

  /** Local snapshots carry no workflow records — the durable stage events
   *  are the source of truth (mock snapshots ship their own workflows). */
  private projectRuntimeWorkflowIfMissing(view: RunView): void {
    if (view.workflows.length > 0) return;
    view.workflows = projectRuntimeWorkflow(view.events);
    if (!view.activeWorkflowId && view.workflows.length > 0) {
      view.activeWorkflowId = view.workflows[0].workflowId;
    }
  }

  private promote(runId: string, view: RunView): void {
    this.runs.delete(runId);
    this.runs.set(runId, view);
  }

  private startPollingIfLive(view: RunView): void {
    if (!isLiveStatus(view.status) || this.pollTimers.has(view.runId) || this.pollingStopped) return;
    // Chained long-poll: subscribeEvents holds up to ~8s until new durable
    // events arrive, so live runs push instead of waiting on the interval.
    // On transport failure the loop backs off by pollIntervalMs and retries.
    let running = true;
    const timer = { stop: () => { running = false; } };
    this.pollTimers.set(view.runId, timer);
    const runId = view.runId;
    void (async () => {
      while (running) {
        try {
          const cursor = this.cursors.get(runId);
          const current = this.runs.get(runId) ?? view;
          const pushed = await this.client.subscribeEvents({
            sessionId: current.sessionId,
            runId,
            afterSequence: cursor?.lastSequence ?? -1,
          } satisfies EventStreamRequest);
          if (!running) break;
          await this.refreshRun(runId, undefined, pushed);
        } catch {
          if (!running) break;
          await new Promise((resolve) => globalThis.setTimeout(resolve, this.pollIntervalMs));
          try { await this.refreshRun(runId); } catch { /* surfaced via setError */ }
        }
      }
    })();
  }

  private stopPolling(runId: string): void {
    const timer = this.pollTimers.get(runId);
    if (!timer) return;
    timer.stop();
    this.pollTimers.delete(runId);
  }

  dismissError(): void {
    if (!this.currentError) return;
    this.currentError = null;
    this.errorSource = null;
    this.emit();
  }

  private setError(error: unknown, source: string | null = null): void {
    this.currentError = serviceError(error);
    this.errorSource = source;
    this.emit();
  }

  private clearErrorFrom(source: string): void {
    if (this.errorSource !== source) return;
    this.currentError = null;
    this.errorSource = null;
  }

  private emit(): void {
    const state = this.getState();
    for (const listener of this.listeners) listener(state);
  }
}
