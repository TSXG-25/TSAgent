import {
  AgentServiceClientError,
  type DesktopAgentServiceClient,
  type EventStreamRequest,
  type GetRunRequest,
  type RunSnapshot,
  type ServiceErrorDTO,
  type StartRunRequest,
} from "../../types/service";
import type { RunView } from "../../types";
import { mergeRunEvents, toRunView } from "../../service/viewMapper";

export interface RunControllerState {
  runs: RunView[];
  activeRunId: string;
  isLoading: boolean;
  error: ServiceErrorDTO | null;
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

function locator(snapshot: Pick<RunSnapshot, "tenantId" | "sessionId" | "runId">): GetRunRequest {
  return {
    tenantId: snapshot.tenantId,
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
  private readonly pollTimers = new Map<string, ReturnType<typeof globalThis.setInterval>>();
  private readonly inFlight = new Set<string>();
  private readonly pollIntervalMs: number;
  private activeRunId = "";
  private loading = true;
  private currentError: ServiceErrorDTO | null = null;
  private initialization: Promise<void> | null = null;

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
      isLoading: this.loading,
      error: this.currentError,
    };
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    listener(this.getState());
    return () => this.listeners.delete(listener);
  }

  async initialize(): Promise<void> {
    if (this.initialization) return this.initialization;
    this.initialization = this.initializeCatalog();
    return this.initialization;
  }

  async startRun(request: StartRunRequest): Promise<RunView> {
    this.clearError();
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
    }
  }

  async refreshRun(runId: string): Promise<RunView | undefined> {
    const current = this.runs.get(runId);
    if (!current || this.inFlight.has(runId)) return current;

    this.inFlight.add(runId);
    try {
      const request = locator({ tenantId: current.tenantId ?? this.client.identity.tenantId, sessionId: current.sessionId, runId });
      const afterSequence = this.cursors.get(runId)?.lastSequence ?? -1;
      const [snapshot, artifacts, incomingEvents] = await Promise.all([
        this.client.getRun(request),
        this.client.listArtifacts(request),
        this.client.readEvents({ ...request, afterSequence } satisfies EventStreamRequest),
      ]);

      const cursor = this.cursors.get(runId) ?? { lastSequence: afterSequence, eventIds: new Set<string>() };
      const unseenEvents = incomingEvents.filter((event) => !cursor.eventIds.has(event.eventId));
      for (const event of incomingEvents) cursor.eventIds.add(event.eventId);
      cursor.lastSequence = Math.max(afterSequence, ...incomingEvents.map((event) => event.sequenceNumber));
      this.cursors.set(runId, cursor);

      const next = toRunView(snapshot, artifacts, []);
      next.events = mergeRunEvents(current.events, unseenEvents);
      this.runs.set(runId, next);
      this.currentError = null;
      this.emit();
      if (isLiveStatus(next.status)) this.startPollingIfLive(next);
      else this.stopPolling(runId);
      return next;
    } catch (error) {
      this.setError(error);
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
    for (const runId of this.pollTimers.keys()) this.stopPolling(runId);
  }

  private async initializeCatalog(): Promise<void> {
    this.loading = true;
    this.currentError = null;
    this.emit();
    try {
      await this.client.ready();
      const snapshots = await this.client.listRuns();
      this.runs.clear();
      this.cursors.clear();
      const views = await Promise.all(snapshots.map((snapshot) => this.hydrateRun(snapshot, -1)));
      for (const view of views) this.runs.set(view.runId, view);
      this.activeRunId = views.some((view) => view.runId === this.activeRunId)
        ? this.activeRunId
        : views[0]?.runId ?? "";
      this.emit();
      for (const view of views) this.startPollingIfLive(view);
    } catch (error) {
      this.setError(error);
    } finally {
      this.loading = false;
      this.emit();
    }
  }

  private async hydrateRun(snapshot: RunSnapshot, afterSequence: number): Promise<RunView> {
    const request = locator(snapshot);
    const [artifacts, events] = await Promise.all([
      this.client.listArtifacts(request),
      this.client.readEvents({ ...request, afterSequence } satisfies EventStreamRequest),
    ]);
    const view = toRunView(snapshot, artifacts, events);
    const eventIds = new Set(events.map((event) => event.eventId));
    this.cursors.set(snapshot.runId, {
      lastSequence: Math.max(afterSequence, ...events.map((event) => event.sequenceNumber)),
      eventIds,
    });
    return view;
  }

  private promote(runId: string, view: RunView): void {
    this.runs.delete(runId);
    this.runs.set(runId, view);
  }

  private startPollingIfLive(view: RunView): void {
    if (!isLiveStatus(view.status) || this.pollTimers.has(view.runId)) return;
    const timer = globalThis.setInterval(() => {
      void this.refreshRun(view.runId).catch(() => undefined);
    }, this.pollIntervalMs);
    this.pollTimers.set(view.runId, timer);
  }

  private stopPolling(runId: string): void {
    const timer = this.pollTimers.get(runId);
    if (!timer) return;
    globalThis.clearInterval(timer);
    this.pollTimers.delete(runId);
  }

  private clearError(): void {
    if (!this.currentError) return;
    this.currentError = null;
    this.emit();
  }

  private setError(error: unknown): void {
    this.currentError = serviceError(error);
    this.emit();
  }

  private emit(): void {
    const state = this.getState();
    for (const listener of this.listeners) listener(state);
  }
}
