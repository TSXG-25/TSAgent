import { createMockRuns, createNewRun } from "../mocks/runData";
import type { RunView } from "../types";
import {
  AgentServiceClientError,
  type ArtifactSummary,
  type CancelRunRequest,
  type DesktopAgentServiceClient,
  type DesktopIdentity,
  type EventStreamRequest,
  type GetRunRequest,
  type ListArtifactsRequest,
  type ResumeRunRequest,
  type RunEvent,
  type RunHandle,
  type RunSnapshot,
  type StartRunRequest,
  type ServiceErrorCode,
} from "../types/service";
import {
  mergeRunEvents,
  toRunView,
  toServiceArtifact,
  toServiceEvent,
  toServiceSnapshot,
} from "./viewMapper";

const LOCAL_TENANT_ID = "tenant-local";
const DEFAULT_IDENTITY: DesktopIdentity = {
  tenantId: LOCAL_TENANT_ID,
  userId: "user-local",
  sessionId: "session-desktop-mock",
};

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function digestText(value: string): string {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return `fnv1a-${(hash >>> 0).toString(16).padStart(8, "0")}`;
}

function keyForRequest(tenantId: string, requestId: string): string {
  return `${tenantId}\u0000${requestId}`;
}

function keyForResume(tenantId: string, runId: string, resumeRequestId: string): string {
  return `${tenantId}\u0000${runId}\u0000${resumeRequestId}`;
}

function keyForCancel(tenantId: string, runId: string, requestId: string): string {
  return `${tenantId}\u0000${runId}\u0000${requestId}`;
}

function cancelDigest(request: CancelRunRequest): string {
  return digestText(
    [request.tenantId, request.sessionId, request.runId, request.requestedBy].join("\u0000"),
  );
}

function clientError(
  code: ServiceErrorCode,
  message: string,
  options: Omit<ConstructorParameters<typeof AgentServiceClientError>[0], "code" | "message" | "retryable"> & {
    retryable?: boolean;
  } = {},
): AgentServiceClientError {
  return new AgentServiceClientError({ ...options, code, message, retryable: options.retryable ?? false });
}

function toHandle(snapshot: RunSnapshot): RunHandle {
  return {
    tenantId: snapshot.tenantId,
    sessionId: snapshot.sessionId,
    runId: snapshot.runId,
    requestId: snapshot.requestId,
    status: snapshot.status,
    revision: snapshot.revision,
    createdAt: snapshot.createdAt,
    updatedAt: snapshot.updatedAt,
  };
}

function event(
  snapshot: RunSnapshot,
  sequenceNumber: number,
  eventId: string,
  eventType: string,
  title: string,
  description: string,
  tone: "info" | "success" | "warning" | "error" | "neutral",
): RunEvent {
  return {
    eventId,
    sequenceNumber,
    eventType,
    tenantId: snapshot.tenantId,
    sessionId: snapshot.sessionId,
    runId: snapshot.runId,
    workflowId: snapshot.activeWorkflowId,
    runRevision: snapshot.revision,
    timestamp: "刚刚",
    payload: { title, description, tone },
  };
}

export class MockAgentServiceClient implements DesktopAgentServiceClient {
  readonly mode = "mock" as const;
  readonly identity: DesktopIdentity;
  private readonly snapshots = new Map<string, RunSnapshot>();
  private readonly artifacts = new Map<string, ArtifactSummary[]>();
  private readonly events = new Map<string, RunEvent[]>();
  private readonly requestIndex = new Map<string, { digest: string; runId: string }>();
  private readonly resumeIndex = new Map<string, { checkpointId: string; action: ResumeRunRequest["action"]; runId: string }>();
  private readonly cancelIndex = new Map<string, { digest: string; runId: string }>();
  private readonly oldestRetainedSequence = new Map<string, number>();
  private nextRunSequence = 2050;

  constructor(seed: RunView[] = createMockRuns(), identity: DesktopIdentity = DEFAULT_IDENTITY) {
    this.identity = { ...identity };
    seed.forEach((run, index) => this.addSeedRun(run, index));
  }

  async ready(): Promise<void> {
    // The mock has no external process to probe, but it still participates in
    // the same composition-root readiness contract as the local client.
  }

  /** Composition-root helper for the first render; the UI still gets data from this adapter. */
  listRunViewsSync(): RunView[] {
    return [...this.snapshots.values()].map((snapshot) =>
      toRunView(snapshot, this.artifacts.get(snapshot.runId) ?? [], this.events.get(snapshot.runId) ?? []),
    );
  }

  listRunsSync(): RunSnapshot[] {
    return [...this.snapshots.values()].map((snapshot) => clone(snapshot));
  }

  async listRuns(): Promise<RunSnapshot[]> {
    return this.listRunsSync();
  }

  async startRun(request: StartRunRequest): Promise<RunHandle> {
    if (!request.tenantId || !request.sessionId || !request.requestId || !request.requestText.trim()) {
      throw clientError("INVALID_REQUEST", "A tenant, session, request ID, and request text are required.", {
        requestId: request.requestId,
      });
    }

    const digest = request.requestDigest ?? digestText(request.requestText);
    const requestKey = keyForRequest(request.tenantId, request.requestId);
    const existing = this.requestIndex.get(requestKey);
    if (existing) {
      if (existing.digest !== digest) {
        throw clientError("IDEMPOTENCY_CONFLICT", "The request ID is already used for different request content.", {
          requestId: request.requestId,
        });
      }
      const existingRun = this.snapshots.get(existing.runId);
      if (existingRun) return toHandle(clone(existingRun));
    }

    const run = createNewRun(request.requestText, this.nextRunSequence);
    this.nextRunSequence += 1;
    const snapshot = toServiceSnapshot(run, {
      tenantId: request.tenantId,
      requestId: request.requestId,
      revision: 1,
    });
    snapshot.sessionId = request.sessionId;
    snapshot.updatedAt = "刚刚";
    snapshot.createdAt = "刚刚";
    const serviceEvents = run.events.map((item, index) => toServiceEvent(item, snapshot, snapshot.revision, index + 1));

    this.snapshots.set(snapshot.runId, snapshot);
    this.artifacts.set(snapshot.runId, run.artifacts.map((item) => toServiceArtifact(item, snapshot)));
    this.events.set(snapshot.runId, serviceEvents);
    this.oldestRetainedSequence.set(snapshot.runId, 1);
    this.requestIndex.set(requestKey, { digest, runId: snapshot.runId });
    return toHandle(clone(snapshot));
  }

  async getRun(request: GetRunRequest): Promise<RunSnapshot> {
    return clone(this.getScopedRun(request));
  }

  async cancelRun(request: CancelRunRequest): Promise<RunSnapshot> {
    if (!request.requestId.trim() || !request.requestedBy.trim()) {
      throw clientError("INVALID_REQUEST", "A cancellation request ID and requester are required.", {
        requestId: request.requestId,
        runId: request.runId,
      });
    }

    const snapshot = this.getScopedRun(request);
    const cancelKey = keyForCancel(request.tenantId, request.runId, request.requestId);
    const digest = cancelDigest(request);
    const previous = this.cancelIndex.get(cancelKey);
    if (previous) {
      if (previous.digest !== digest) {
        throw clientError("IDEMPOTENCY_CONFLICT", "The cancellation request ID is already used for different cancellation content.", {
          requestId: request.requestId,
          runId: request.runId,
        });
      }
      return clone(this.snapshots.get(previous.runId) ?? snapshot);
    }

    if (snapshot.status === "cancelling") {
      throw clientError("RUN_ALREADY_CANCELLING", "Cancellation is already converging for this Run.", {
        requestId: request.requestId,
        runId: request.runId,
      });
    }
    if (snapshot.status === "cancelled") {
      throw clientError("ALREADY_CANCELLED", "This Run is already cancelled.", { runId: request.runId });
    }
    if (snapshot.status === "completed") {
      throw clientError("ALREADY_COMPLETED", "This Run is already completed.", { runId: request.runId });
    }
    if (snapshot.status !== "active") {
      throw clientError("RUN_NOT_CANCELLABLE", "This Run is not currently cancellable.", { runId: request.runId });
    }

    const cancelling = withCancellationStatus(snapshot, "cancelling");
    this.snapshots.set(snapshot.runId, cancelling);
    this.cancelIndex.set(cancelKey, { digest, runId: snapshot.runId });
    this.appendEvent(
      cancelling,
      "run_cancelling",
      "Cancellation requested",
      "Durable cancellation accepted; waiting for a safe boundary.",
      "warning",
    );

    globalThis.setTimeout(() => {
      const current = this.snapshots.get(snapshot.runId);
      if (!current || current.status !== "cancelling") return;
      const cancelled = withCancellationStatus(current, "cancelled");
      this.snapshots.set(snapshot.runId, cancelled);
      this.appendEvent(
        cancelled,
        "run_cancelled",
        "Run cancelled",
        "Cancellation converged at a safe boundary. Verified artifacts are preserved.",
        "warning",
      );
    }, 120);

    return clone(cancelling);
  }

  async resumeRun(request: ResumeRunRequest): Promise<RunHandle> {
    const snapshot = this.getScopedRun(request);
    const resumeKey = keyForResume(request.tenantId, request.runId, request.resumeRequestId);
    const previous = this.resumeIndex.get(resumeKey);
    if (previous) {
      if (previous.checkpointId !== request.checkpointId || previous.action !== request.action) {
        throw clientError("IDEMPOTENCY_CONFLICT", "The resume request ID is already used for a different resume action.", {
          requestId: request.resumeRequestId,
          runId: request.runId,
        });
      }
      return toHandle(clone(this.snapshots.get(previous.runId) ?? snapshot));
    }

    if (snapshot.status === "completed") {
      throw clientError("ALREADY_COMPLETED", "This Run is already completed.", { runId: request.runId });
    }
    if (snapshot.status === "active") {
      throw clientError("RUN_ALREADY_ACTIVE", "This Run is already active.", { runId: request.runId });
    }
    if (snapshot.status !== "blocked" || snapshot.resumeSummary?.outcome !== "ready") {
      throw clientError("RESUME_NOT_ALLOWED", "This Run has no recoverable checkpoint.", { runId: request.runId });
    }

    const nextRevision = snapshot.revision + 1;
    const resumedSnapshot: RunSnapshot = {
      ...snapshot,
      status: "completed",
      revision: nextRevision,
      updatedAt: "刚刚",
      completedWorkflows: snapshot.workflows.map((workflow) => workflow.workflowId),
      pendingWorkflows: [],
      workflows: snapshot.workflows.map((workflow) => ({
        ...workflow,
        status: "completed",
        progress: 100,
        stages: workflow.stages.map((stage) => ({
          ...stage,
          status: stage.stageId === "verification" ? "verified" : "completed",
          duration: stage.stageId === "verification" ? "00:35" : stage.duration,
          tasks: stage.tasks.map((task) => ({ ...task, status: "completed" })),
        })),
      })),
      resumeSummary: {
        ...snapshot.resumeSummary,
        action: request.action,
        outcome: "completed",
        resumedAt: "刚刚",
      },
      verifierSummary: {
        status: "verified",
        checks: "3 / 3 checks",
        stdout: "49",
        detail: "exit_code、stdout 与 artifact integrity 均已通过。",
      },
      conversation: [
        ...snapshot.conversation,
        {
          messageId: `${snapshot.runId}.resume`,
          role: "assistant",
          content: `已从 ${request.checkpointId} 恢复执行。跳过已完成的 Analysis，重新进入 Implementation 并完成 Verification。`,
          at: "刚刚",
        },
      ],
    };

    this.snapshots.set(snapshot.runId, resumedSnapshot);
    this.artifacts.set(
      snapshot.runId,
      (this.artifacts.get(snapshot.runId) ?? []).map((artifact) => ({
        ...artifact,
        verified: true,
        createdRevision: nextRevision,
        createdAt: "刚刚",
        preview:
          artifact.displayName === "output/verification.json"
            ? artifact.preview?.replace('"awaiting_resume"', '"verified"').replace('"checks": []', '"checks": ["exit_code", "stdout", "artifact"]')
            : artifact.displayName === "logs/stdout.txt"
              ? "49\n"
              : artifact.preview,
      })),
    );

    const runEvents = this.events.get(snapshot.runId) ?? [];
    const nextSequence = (runEvents[runEvents.length - 1]?.sequenceNumber ?? 0) + 1;
    this.events.set(snapshot.runId, [
      ...runEvents,
      event(
        resumedSnapshot,
        nextSequence,
        `${snapshot.runId}.resumed`,
        "resume.selected",
        "Resume exact accepted",
        `${request.checkpointId} · resumed without replaying Analysis.`,
        "info",
      ),
      event(
        resumedSnapshot,
        nextSequence + 1,
        `${snapshot.runId}.verified`,
        "run.verified",
        "Run verified",
        "exit_code 0 · stdout matched expected value 49.",
        "success",
      ),
    ]);
    this.resumeIndex.set(resumeKey, {
      checkpointId: request.checkpointId,
      action: request.action,
      runId: request.runId,
    });
    return toHandle(clone(resumedSnapshot));
  }

  private appendEvent(
    snapshot: RunSnapshot,
    eventType: string,
    title: string,
    description: string,
    tone: "info" | "success" | "warning" | "error" | "neutral",
  ): void {
    const runEvents = this.events.get(snapshot.runId) ?? [];
    const nextSequence = (runEvents[runEvents.length - 1]?.sequenceNumber ?? 0) + 1;
    this.events.set(snapshot.runId, [
      ...runEvents,
      event(snapshot, nextSequence, `${snapshot.runId}.${eventType}.${nextSequence}`, eventType, title, description, tone),
    ]);
  }

  async listArtifacts(request: ListArtifactsRequest): Promise<ArtifactSummary[]> {
    this.getScopedRun(request);
    return clone(this.artifacts.get(request.runId) ?? []);
  }

  async readEvents(request: EventStreamRequest): Promise<RunEvent[]> {
    this.getScopedRun(request);
    if (!Number.isInteger(request.afterSequence) || request.afterSequence < -1) {
      throw clientError("CURSOR_INVALID", "afterSequence must be an integer greater than or equal to -1.", {
        runId: request.runId,
      });
    }

    const oldest = this.oldestRetainedSequence.get(request.runId) ?? 1;
    // -1 is the explicit initial-load sentinel used by the desktop client.
    // It must remain valid even when the retained window starts at sequence 1.
    if (request.afterSequence !== -1 && request.afterSequence < oldest - 1) {
      throw clientError("EVENT_CURSOR_EXPIRED", "The event cursor is older than the retained event window.", {
        runId: request.runId,
      });
    }

    const events = (this.events.get(request.runId) ?? [])
      .filter((item) => item.sequenceNumber > request.afterSequence)
      .sort((left, right) => left.sequenceNumber - right.sequenceNumber);
    return clone(typeof request.limit === "number" ? events.slice(0, Math.max(0, request.limit)) : events);
  }

  /** Test/demo hook for exercising EVENT_CURSOR_EXPIRED without a real store. */
  setOldestRetainedSequence(runId: string, sequenceNumber: number): void {
    this.oldestRetainedSequence.set(runId, Math.max(1, Math.floor(sequenceNumber)));
  }

  private addSeedRun(run: RunView, index: number): void {
    const snapshot = toServiceSnapshot(run, {
      tenantId: LOCAL_TENANT_ID,
      requestId: `seed-${run.runId}`,
      revision: index + 1,
    });
    const serviceEvents = run.events.map((item, eventIndex) => toServiceEvent(item, snapshot, snapshot.revision, eventIndex + 1));
    const serviceArtifacts = run.artifacts.map((item) => toServiceArtifact(item, snapshot));
    this.snapshots.set(run.runId, snapshot);
    this.events.set(run.runId, serviceEvents);
    this.artifacts.set(run.runId, serviceArtifacts);
    this.oldestRetainedSequence.set(run.runId, 1);
    this.requestIndex.set(keyForRequest(snapshot.tenantId, snapshot.requestId), {
      digest: digestText(snapshot.requestText),
      runId: run.runId,
    });
  }

  private getScopedRun(request: { tenantId: string; sessionId: string; runId: string }): RunSnapshot {
    const snapshot = this.snapshots.get(request.runId);
    if (!snapshot || snapshot.tenantId !== request.tenantId || snapshot.sessionId !== request.sessionId) {
      // Keep scope mismatches indistinguishable from missing Runs.
      throw clientError("RUN_NOT_FOUND", "Run not found.");
    }
    return snapshot;
  }
}

function withCancellationStatus(
  snapshot: RunSnapshot,
  status: "cancelling" | "cancelled",
): RunSnapshot {
  return {
    ...snapshot,
    status,
    revision: snapshot.revision + 1,
    updatedAt: "刚刚",
    workflows: snapshot.workflows.map((workflow) => ({
      ...workflow,
      status: workflow.status === "active" ? status : workflow.status,
      stages: workflow.stages.map((stage) => ({
        ...stage,
        status: stage.status === "running" ? "interrupted" : stage.status,
        tasks: stage.tasks.map((task) => ({
          ...task,
          status: task.status === "running" ? "interrupted" : task.status,
        })),
      })),
    })),
  };
}
