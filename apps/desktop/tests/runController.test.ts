import { AgentServiceClientError } from "../src/types/service";
import { UnavailableDesktopAgentServiceClient } from "../src/service/clientFactory";
import assert from "node:assert/strict";
import test from "node:test";
import { RunController } from "../src/features/runs/runController";
import type {
  PauseRunRequest,
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

function event(sequenceNumber: number, eventId: string, eventType: string, payload: Record<string, unknown> = {}): RunEvent {
  return {
    eventId,
    sequenceNumber,
    eventType,
    tenantId: identity.tenantId,
    sessionId: identity.sessionId,
    runId: "run-1",
    runRevision: sequenceNumber,
    timestamp: `2026-08-11T00:00:0${sequenceNumber}Z`,
    payload: { title: eventType, description: eventType, tone: "info", ...payload },
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

class FakeAgentServiceClient extends UnavailableDesktopAgentServiceClient {
  readonly snapshots = new Map<string, RunSnapshot>();
  readonly events = new Map<string, RunEvent[]>();
  readonly eventCursors: number[] = [];

  constructor(initial: RunSnapshot | null, initialEvents: RunEvent[]) {
    super("local", identity, new AgentServiceClientError({ code: "PROVIDER_UNAVAILABLE", message: "unavailable in test", retryable: true }));
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
    return (this.events.get(request.runId) ?? [])
      .filter((item) => item.sequenceNumber > request.afterSequence)
      .slice(0, request.limit ?? Number.MAX_SAFE_INTEGER);
  }

  // Never resolves — the controller's chained subscription parks here, so
  // eventCursors only records the explicit readEvents calls under test.
  async subscribeEvents(): Promise<RunEvent[]> {
    return new Promise<RunEvent[]>(() => {});
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

test("4B04 dismissError clears the single controller-owned error", async () => {
  class UnavailableClient extends FakeAgentServiceClient {
    override async ready(): Promise<void> {
      throw new AgentServiceClientError({ code: "PROVIDER_UNAVAILABLE", message: "sidecar down", retryable: true });
    }
  }
  const controller = new RunController(new UnavailableClient(null, []), { pollIntervalMs: 1_000 });
  const observed: Array<string | null> = [];
  controller.subscribe((state) => observed.push(state.error?.code ?? null));
  await controller.initialize();
  assert.equal(controller.getState().error?.code, "PROVIDER_UNAVAILABLE");
  controller.dismissError();
  assert.equal(controller.getState().error, null);
  assert.equal(observed.at(-1), null);
  controller.dispose();
});

test("4B05 local stage events project a runtime workflow", async () => {
  const client = new FakeAgentServiceClient(snapshot("active"), [
    event(1, "event-1", "run_created"),
    event(2, "event-2", "stage_started", { stage_id: "planning", phase: "planning" }),
    event(3, "event-3", "stage_completed", { stage_id: "planning", phase: "planning", task_count: 1 }),
    event(4, "event-4", "stage_started", { stage_id: "loop", phase: "execution" }),
    // planning re-entered on a later goal round — latest event wins.
    event(5, "event-5", "stage_started", { stage_id: "planning", phase: "planning" }),
    event(7, "event-7", "stage_completed", { stage_id: "planning", phase: "planning", task_count: 2 }),
  ]);
  const controller = new RunController(client, { pollIntervalMs: 1000 });
  await controller.initialize();

  const run = controller.getState().runs[0];
  assert.equal(run?.activeWorkflowId, "runtime");
  assert.equal(run?.workflows.length, 1);
  assert.equal(run?.workflows[0]?.status, "active");
  assert.deepEqual(
    run?.workflows[0]?.stages.map((stage) => [stage.stageId, stage.status, stage.duration]),
    [["planning", "completed", "00:02"], ["loop", "running", ""]],
  );
  controller.stopAllPolling();
});

test("4B06 chained subscribe applies returned events without re-reading", async () => {
  const pushed = event(4, "event-4", "assistant_delta", { delta: "hello" });
  class PushOnceClient extends FakeAgentServiceClient {
    subscribeCalls = 0;
    async subscribeEvents(): Promise<RunEvent[]> {
      this.subscribeCalls += 1;
      if (this.subscribeCalls === 1) return [pushed];
      return new Promise<RunEvent[]>(() => {});
    }
  }
  const client = new PushOnceClient(snapshot("active"), [event(1, "event-1", "run_created")]);
  const controller = new RunController(client, { pollIntervalMs: 10 });
  await controller.initialize();
  const readsAfterHydrate = client.eventCursors.length;
  await new Promise((resolve) => setTimeout(resolve, 40));
  const run = controller.getState().runs[0];
  assert.ok(run?.events.some((e) => e.eventId === "event-4"), "pushed event merged");
  assert.equal(client.eventCursors.length, readsAfterHydrate, "readEvents not re-called after subscribe returned events");
  controller.stopAllPolling();
});

test("4B07 pause requests mint distinct idempotency ids", async () => {
  const requests: string[] = [];
  class PauseClient extends FakeAgentServiceClient {
    async pauseRun(request: PauseRunRequest): Promise<RunHandle> {
      requests.push(request.requestId);
      return { tenantId: identity.tenantId, sessionId: identity.sessionId, runId: "run-1", requestId: request.requestId, status: "blocked", revision: 2, createdAt: "", updatedAt: "" };
    }
  }
  const client = new PauseClient(snapshot("active"), [event(1, "event-1", "run_created")]);
  const controller = new RunController(client, { pollIntervalMs: 1000 });
  await controller.initialize();
  await controller.pauseRun("run-1");
  await controller.pauseRun("run-1");
  assert.equal(requests.length, 2);
  assert.notEqual(requests[0], requests[1]);
  assert.match(requests[0]!, /^pause-run-1-/);
  controller.stopAllPolling();
});

test("4B-hydrate drains every event batch when reopening a completed run", async () => {
  const many = Array.from({ length: 250 }, (_, i) =>
    event(i + 1, `event-${i + 1}`, i === 249 ? "run_completed" : "run_step"));
  const client = new FakeAgentServiceClient(snapshot("completed"), many);
  const controller = new RunController(client, { pollIntervalMs: 1000 });
  await controller.initialize();

  const view = controller.getState().runs[0];
  assert.equal(view?.events.length, 250, "completed run's timeline must include events beyond the first batch");
  assert.ok(client.eventCursors.length >= 3, "hydrate must page readEvents until the stream is drained");
  assert.deepEqual(client.eventCursors.slice(0, 3), [-1, 100, 200]);
  controller.stopAllPolling();
});
