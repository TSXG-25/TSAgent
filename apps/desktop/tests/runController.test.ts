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

function event(sequenceNumber: number, eventId: string, eventType: string): RunEvent {
  return {
    eventId,
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

function snapshot(status: RunSnapshot["status"]): RunSnapshot {
  return {
    ...identity,
    runId: "run-1",
    requestId: "request-1",
    requestText: "write a result",
    status,
    revision: status === "completed" ? 3 : 2,
    createdAt: "2026-08-11T00:00:00Z",
    updatedAt: "2026-08-11T00:00:02Z",
    completedWorkflows: [],
    pendingWorkflows: ["workflow-1"],
    workflows: [],
    conversation: [],
    verifierSummary: { status: status === "completed" ? "verified" : "waiting", checks: "—", stdout: "—", detail: "test" },
    ...(status === "completed"
      ? {
          output: {
            runId: "run-1",
            revision: 3,
            text: "durable answer",
            evidenceIds: ["evidence-1"],
            artifactIds: [],
            createdAt: "2026-08-11T00:00:02Z",
          },
        }
      : {}),
  };
}

class FakeAgentServiceClient implements DesktopAgentServiceClient {
  readonly mode = "local" as const;
  readonly identity = identity;
  readonly snapshots = new Map<string, RunSnapshot>();
  readonly events = new Map<string, RunEvent[]>();
  readonly eventCursors: number[] = [];

  constructor(initial: RunSnapshot | null, initialEvents: RunEvent[]) {
    if (initial) this.snapshots.set(initial.runId, initial);
    this.events.set("run-1", initialEvents);
  }

  async ready(): Promise<void> {}

  async listRuns(): Promise<RunSnapshot[]> {
    return [...this.snapshots.values()];
  }

  async startRun(request: StartRunRequest): Promise<RunHandle> {
    const next = snapshot("active");
    next.requestId = request.requestId;
    next.requestText = request.requestText;
    this.snapshots.set(next.runId, next);
    this.events.set(next.runId, [event(1, "event-1", "run_created")]);
    return {
      tenantId: next.tenantId,
      sessionId: next.sessionId,
      runId: next.runId,
      requestId: request.requestId,
      status: "active",
      revision: next.revision,
      createdAt: next.createdAt,
      updatedAt: next.updatedAt,
    };
  }

  async getRun(request: GetRunRequest): Promise<RunSnapshot> {
    const value = this.snapshots.get(request.runId);
    if (!value) throw new Error("missing run");
    return value;
  }

  async cancelRun(_request: CancelRunRequest): Promise<RunSnapshot> {
    return this.snapshots.get("run-1")!;
  }

  async resumeRun(_request: ResumeRunRequest): Promise<RunHandle> {
    return this.startRun({ tenantId: identity.tenantId, sessionId: identity.sessionId, requestId: "resume-1", requestText: "resume" });
  }

  async listArtifacts(_request: ListArtifactsRequest): Promise<ArtifactSummary[]> {
    return [];
  }

  async readEvents(request: EventStreamRequest): Promise<RunEvent[]> {
    this.eventCursors.push(request.afterSequence);
    return (this.events.get(request.runId) ?? []).filter((item) => item.sequenceNumber > request.afterSequence);
  }
}

test("4B01 controller hydrates a Run and advances the exclusive event cursor", async () => {
  const client = new FakeAgentServiceClient(snapshot("active"), [event(1, "event-1", "run_created")]);
  const controller = new RunController(client, { pollIntervalMs: 1000 });
  await controller.initialize();

  assert.equal(controller.getState().runs[0]?.status, "active");
  assert.deepEqual(controller.getState().runs[0]?.events.map((item) => item.eventId), ["event-1"]);
  assert.deepEqual(client.eventCursors, [-1]);
  controller.stopAllPolling();
});

test("4B02 controller de-duplicates events and displays authoritative durable RunOutput", async () => {
  const client = new FakeAgentServiceClient(snapshot("active"), [event(1, "event-1", "run_created")]);
  const controller = new RunController(client, { pollIntervalMs: 1000 });
  await controller.initialize();

  client.snapshots.set("run-1", snapshot("completed"));
  client.events.set("run-1", [
    event(1, "event-1", "run_created"),
    event(2, "event-2", "run_completed"),
    event(2, "event-2", "run_completed"),
  ]);
  await controller.refreshRun("run-1");

  const run = controller.getState().runs[0];
  assert.equal(run?.status, "completed");
  assert.equal(run?.output?.text, "durable answer");
  assert.deepEqual(run?.events.map((item) => item.eventId), ["event-1", "event-2"]);
  assert.deepEqual(client.eventCursors, [-1, 1]);
  controller.stopAllPolling();
});

test("4B03 startRun returns a hydrated active Run without waiting for terminal completion", async () => {
  const client = new FakeAgentServiceClient(null, []);
  const controller = new RunController(client, { pollIntervalMs: 1000 });
  await controller.initialize();

  const run = await controller.startRun({
    tenantId: identity.tenantId,
    sessionId: identity.sessionId,
    requestId: "request-2",
    requestText: "write a result",
  });

  assert.equal(run.status, "active");
  assert.equal(controller.getState().activeRunId, "run-1");
  assert.equal(controller.getState().isLoading, false);
  controller.stopAllPolling();
});
