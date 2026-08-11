import assert from "node:assert/strict";
import test from "node:test";
import { RunController } from "../src/features/runs/runController";
import type {
  ArtifactSummary,
  CancelRunRequest,
  DesktopAgentServiceClient,
  DesktopIdentity,
  EventStreamRequest,
  GetRunRequest,
  ListArtifactsRequest,
  ResumeRunRequest,
  RunEvent,
  RunHandle,
  RunSnapshot,
  StartRunRequest,
} from "../src/types/service";

const identity: DesktopIdentity = {
  tenantId: "tenant-1",
  userId: "user-1",
  sessionId: "session-1",
};

function event(sequenceNumber: number, eventType: string): RunEvent {
  return {
    eventId: `event-${sequenceNumber}`,
    sequenceNumber,
    eventType,
    tenantId: identity.tenantId,
    sessionId: identity.sessionId,
    runId: "run-1",
    runRevision: sequenceNumber,
    timestamp: `2026-08-11T00:00:0${sequenceNumber}Z`,
    payload: { title: eventType, description: eventType, tone: "info" },
  };
}

function artifact(displayName: string, revision: number): ArtifactSummary {
  return {
    artifactId: `artifact-${displayName}`,
    runId: "run-1",
    type: "text",
    displayName,
    reference: `workspace://run-1/${displayName}`,
    digest: `sha256:${displayName}-${revision}`,
    size: 12,
    exists: true,
    verified: true,
    producer: { workflowId: "workflow-a", stageId: "stage-a" },
    createdRevision: revision,
    createdAt: "2026-08-11T00:00:02Z",
  };
}

function snapshot(
  status: RunSnapshot["status"],
  options: Pick<RunSnapshot, "resumeSummary" | "failureSummary" | "output"> = {},
): RunSnapshot {
  return {
    ...identity,
    runId: "run-1",
    requestId: "request-1",
    requestText: "test operation",
    status,
    revision: 2,
    createdAt: "2026-08-11T00:00:00Z",
    updatedAt: "2026-08-11T00:00:02Z",
    completedWorkflowIds: [],
    pendingWorkflowIds: ["workflow-a", "workflow-b"],
    workflows: [],
    conversation: [],
    verifierSummary: { status: "waiting", checks: "—", stdout: "—", detail: "waiting" },
    ...options,
  };
}

class OperationsClient implements DesktopAgentServiceClient {
  readonly mode = "local" as const;
  readonly identity = identity;
  snapshot: RunSnapshot;
  artifacts: ArtifactSummary[] = [];
  events: RunEvent[] = [event(1, "run_created")];
  readonly cancelRequests: CancelRunRequest[] = [];
  readonly resumeRequests: ResumeRunRequest[] = [];
  readonly startRequests: StartRunRequest[] = [];
  readonly eventCursors: number[] = [];

  constructor(initial: RunSnapshot) {
    this.snapshot = initial;
  }

  async ready(): Promise<void> {}

  async listRuns(): Promise<RunSnapshot[]> {
    return [this.snapshot];
  }

  async startRun(request: StartRunRequest): Promise<RunHandle> {
    this.startRequests.push(request);
    return this.handle();
  }

  async getRun(_request: GetRunRequest): Promise<RunSnapshot> {
    return this.snapshot;
  }

  async cancelRun(request: CancelRunRequest): Promise<RunSnapshot> {
    this.cancelRequests.push(request);
    this.snapshot = {
      ...this.snapshot,
      status: "cancelling",
      revision: this.snapshot.revision + 1,
      updatedAt: "2026-08-11T00:00:03Z",
    };
    this.events = [...this.events, event(2, "run_cancelling")];
    return this.snapshot;
  }

  async resumeRun(request: ResumeRunRequest): Promise<RunHandle> {
    this.resumeRequests.push(request);
    this.snapshot = {
      ...this.snapshot,
      status: "completed",
      revision: this.snapshot.revision + 1,
      completedWorkflowIds: ["workflow-a", "workflow-b"],
      pendingWorkflowIds: [],
      updatedAt: "2026-08-11T00:00:04Z",
      output: {
        runId: "run-1",
        revision: this.snapshot.revision + 1,
        text: "resumed durable output",
        evidenceIds: ["evidence-1"],
        artifactIds: this.artifacts.map((item) => item.artifactId),
        createdAt: "2026-08-11T00:00:04Z",
      },
    };
    this.events = [...this.events, event(2, "run_completed")];
    return this.handle();
  }

  async listArtifacts(_request: ListArtifactsRequest): Promise<ArtifactSummary[]> {
    return this.artifacts.map((item) => ({ ...item }));
  }

  async readEvents(request: EventStreamRequest): Promise<RunEvent[]> {
    this.eventCursors.push(request.afterSequence);
    return this.events.filter((item) => item.sequenceNumber > request.afterSequence);
  }

  private handle(): RunHandle {
    return {
      tenantId: this.snapshot.tenantId,
      sessionId: this.snapshot.sessionId,
      runId: this.snapshot.runId,
      requestId: this.snapshot.requestId,
      status: this.snapshot.status === "completed" ? "completed" : "active",
      revision: this.snapshot.revision,
      createdAt: this.snapshot.createdAt,
      updatedAt: this.snapshot.updatedAt,
    };
  }
}

async function initialized(client: OperationsClient): Promise<RunController> {
  const controller = new RunController(client, { pollIntervalMs: 1000 });
  await controller.initialize();
  return controller;
}

test("B201 completed Run exposes durable RunOutput", async () => {
  const client = new OperationsClient(snapshot("completed", {
    output: {
      runId: "run-1",
      revision: 2,
      text: "authoritative answer",
      evidenceIds: [],
      artifactIds: [],
      createdAt: "2026-08-11T00:00:02Z",
    },
  }));
  const controller = await initialized(client);
  assert.equal(controller.getState().runs[0]?.output?.text, "authoritative answer");
  controller.dispose();
});

test("B202 artifact revision refresh exposes verified metadata", async () => {
  const client = new OperationsClient(snapshot("active"));
  client.artifacts = [artifact("analysis.md", 2)];
  const controller = await initialized(client);
  client.artifacts = [artifact("analysis.md", 2), artifact("raw_data.json", 3)];
  client.snapshot = { ...client.snapshot, revision: 3 };
  await controller.refreshRun("run-1");
  assert.deepEqual(controller.getState().runs[0]?.artifacts.map((item) => [item.path, item.digest, item.status]), [
    ["analysis.md", "sha256:analysis.md-2", "verified"],
    ["raw_data.json", "sha256:raw_data.json-3", "verified"],
  ]);
  controller.dispose();
});

test("B203 recoverable Run delegates Resume exactly once and restarts hydration", async () => {
  const client = new OperationsClient(snapshot("blocked", {
    resumeSummary: {
      checkpointId: "checkpoint-1",
      action: "RESUME_EXACT",
      reason: "recoverable",
      sourceStage: "stage-b",
      outcome: "ready",
    },
  }));
  const controller = await initialized(client);
  await Promise.all([controller.resumeRun("run-1"), controller.resumeRun("run-1")]);
  assert.equal(client.resumeRequests.length, 1);
  assert.equal(controller.getState().runs[0]?.status, "completed");
  controller.dispose();
});

test("B204 Resume sends the backend-projected action without local workflow replay", async () => {
  const client = new OperationsClient(snapshot("blocked", {
    resumeSummary: {
      checkpointId: "checkpoint-1",
      action: "REPLAY_FROM_STAGE",
      reason: "stage verifier requested replay",
      sourceStage: "stage-b",
      outcome: "ready",
    },
  }));
  const controller = await initialized(client);
  await controller.resumeRun("run-1");
  assert.equal(client.resumeRequests[0]?.action, "REPLAY_FROM_STAGE");
  assert.equal(client.startRequests.length, 0);
  controller.dispose();
});

test("B205 active Cancel is idempotent and first renders CANCELLING", async () => {
  const client = new OperationsClient(snapshot("active"));
  const controller = await initialized(client);
  const first = controller.cancelRun("run-1");
  const second = controller.cancelRun("run-1");
  await Promise.all([first, second]);
  assert.equal(client.cancelRequests.length, 1);
  assert.equal(client.cancelRequests[0]?.requestId, "cancel-run-1");
  assert.equal(controller.getState().runs[0]?.status, "cancelling");
  controller.dispose();
});

test("B206 run_cancelled refresh preserves verified artifacts", async () => {
  const client = new OperationsClient(snapshot("active"));
  client.artifacts = [artifact("analysis.md", 2)];
  const controller = await initialized(client);
  await controller.cancelRun("run-1");
  client.snapshot = { ...client.snapshot, status: "cancelled", revision: 4 };
  client.events = [...client.events, event(3, "run_cancelled")];
  await controller.refreshRun("run-1");
  assert.equal(controller.getState().runs[0]?.status, "cancelled");
  assert.equal(controller.getState().runs[0]?.artifacts[0]?.status, "verified");
  assert.equal(client.cancelRequests.length, 1);
  controller.dispose();
});

test("B207 timeout remains distinct from Failed and Cancelled", async () => {
  const client = new OperationsClient(snapshot("timed_out"));
  const controller = await initialized(client);
  assert.equal(controller.getState().runs[0]?.status, "timed_out");
  assert.notEqual(controller.getState().runs[0]?.status, "failed");
  assert.notEqual(controller.getState().runs[0]?.status, "cancelled");
  controller.dispose();
});

test("B208 blocked/failed state exposes only stable public failure summary", async () => {
  const client = new OperationsClient(snapshot("blocked", {
    failureSummary: {
      code: "RESEARCH_TOOL_UNAVAILABLE",
      message: "当前没有可核验的外部最新来源，因此不能可靠回答这项时效性问题。",
      retryable: false,
    },
  }));
  const controller = await initialized(client);
  const run = controller.getState().runs[0];
  assert.equal(run?.status, "blocked");
  assert.equal(run?.failure?.code, "RESEARCH_TOOL_UNAVAILABLE");
  assert.equal(run?.failure?.message.includes("sqlite"), false);
  assert.equal(run?.failure?.message.includes("traceback"), false);
  controller.dispose();
});

test("B209 reconnect refresh has no implicit Resume or Cancel side effect", async () => {
  const client = new OperationsClient(snapshot("active"));
  const controller = await initialized(client);
  await controller.refreshRun("run-1");
  await controller.refreshRun("run-1");
  assert.equal(client.resumeRequests.length, 0);
  assert.equal(client.cancelRequests.length, 0);
  controller.dispose();
});
