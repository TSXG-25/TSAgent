import { test } from "node:test";
import assert from "node:assert/strict";
import { RunController } from "../src/features/runs/runController";
import { submitToPlanet, delegateToPlanet } from "../src/app/delegate";
import { workspaceRuns, selectWorkspaceRun, getWorkspaceRunId } from "../src/app/workspaceSelection";
import { MockAgentServiceClient } from "../src/service/mockAgentService";
import { UnavailableDesktopAgentServiceClient } from "../src/service/clientFactory";
import { AgentServiceClientError } from "../src/types/service";
import type { RunCtx } from "../src/app/runCtx";
import type { RunView } from "../src/types";
import type {
  CancelRunRequest, DesktopIdentity, GetRunRequest, RunHandle, RunSnapshot, StartRunRequest,
} from "../src/types/service";

/** WS-01: 并行接纳 + 任务归属 — 每次提交独立接纳、originPlanet 归属、
 *  操作状态按 Run 维护、显式委派与普通发送分离。 */

const identity: DesktopIdentity = { tenantId: "t1", userId: "u1", sessionId: "s1" };

function snapshotFor(runId: string, requestId: string): RunSnapshot {
  return {
    ...identity,
    runId,
    requestId,
    requestText: `req ${runId}`,
    status: "active",
    revision: 1,
    createdAt: "2026-11-14T00:00:00Z",
    updatedAt: "2026-11-14T00:00:01Z",
    completedWorkflows: [],
    pendingWorkflows: [],
    workflows: [],
    conversation: [],
  };
}

class ParallelClient extends UnavailableDesktopAgentServiceClient {
  readonly mode = "local" as const;
  readonly identity = identity;
  readonly startRequests: StartRunRequest[] = [];
  private gate: Promise<void> = Promise.resolve();

  constructor() {
    super("local", identity, new AgentServiceClientError({ code: "PROVIDER_UNAVAILABLE", message: "t", retryable: false }));
  }

  hold(promise: Promise<void>): void { this.gate = promise; }

  async ready(): Promise<void> {}
  async listRuns(_scope: { tenantId: string; sessionId: string }): Promise<RunSnapshot[]> { return []; }

  async startRun(request: StartRunRequest): Promise<RunHandle> {
    this.startRequests.push(request);
    await this.gate;
    return {
      tenantId: request.tenantId,
      sessionId: request.sessionId,
      runId: `run-${request.requestId}`,
      requestId: request.requestId,
      status: "active",
      revision: 1,
      createdAt: "t",
      updatedAt: "t",
    };
  }

  async getRun(request: GetRunRequest): Promise<RunSnapshot> {
    return snapshotFor(request.runId, request.runId.slice(4));
  }

  async listArtifacts(): Promise<never[]> { return []; }
  async readEvents(): Promise<never[]> { return []; }

  readonly cancelRequests: CancelRunRequest[] = [];
  async cancelRun(request: CancelRunRequest): Promise<RunSnapshot> {
    this.cancelRequests.push(request);
    return { ...snapshotFor(request.runId, "rx"), status: "cancelled" };
  }
}

function startRequest(requestId: string): StartRunRequest {
  return {
    tenantId: identity.tenantId,
    sessionId: identity.sessionId,
    requestId,
    requestText: `text ${requestId}`,
  };
}

test("WS-01: two parallel startRun calls are each admitted and return their own run", async () => {
  const client = new ParallelClient();
  let release!: () => void;
  client.hold(new Promise((r) => { release = r; }));
  const controller = new RunController(client);
  const p1 = controller.startRun(startRequest("a1"));
  const p2 = controller.startRun(startRequest("a2"));
  // Old behaviour: the second call returned the in-flight run instead of
  // submitting — both requests must reach the service.
  await Promise.resolve();
  assert.equal(client.startRequests.length, 2);
  release();
  const [v1, v2] = await Promise.all([p1, p2]);
  assert.equal(v1.runId, "run-a1");
  assert.equal(v2.runId, "run-a2");
  controller.dispose();
});

test("WS-01: control ops are per-run — cancelling another run during a start proceeds", async () => {
  const client = new ParallelClient();
  let release!: () => void;
  client.hold(new Promise((r) => { release = r; }));
  const controller = new RunController(client);
  const pending = controller.startRun(startRequest("s1"));
  await Promise.resolve();
  // A run already in the controller can be controlled while a start flies —
  // the old global operationState would refuse this cancel.
  const seeded = controller.refreshRun("run-x", "s1");
  release();
  await pending;
  const view = await seeded;
  assert.equal(view?.status, "active");
  await controller.cancelRun("run-x");
  assert.equal(client.cancelRequests.length, 1);
  assert.equal(client.cancelRequests[0].runId, "run-x");
  controller.dispose();
});

function runView(runId: string, sessionId: string, originPlanet?: string): RunView {
  return {
    runId, sessionId, originPlanet,
    status: "completed", request: `r ${runId}`, activeWorkflowId: "",
    createdAt: "t", updatedAt: "t", duration: "—",
    workflows: [], artifacts: [], events: [], conversation: [],
    verifier: { status: "waiting", checks: "", stdout: "", detail: "" },
  };
}

function ctxWith(runs: RunView[], service?: MockAgentServiceClient): RunCtx {
  const svc = service ?? new MockAgentServiceClient([], identity);
  return {
    sessionId: "s1",
    selectSession: () => {},
    ensurePlanet: async () => "s1",
    sessionForPlanet: () => "s1",
    selectSessionFor: () => {},
    service: svc,
    runtimeLabel: "mock",
    controller: new RunController(svc),
    runs,
    activeRun: null,
    workflow: undefined,
    requestDraft: "",
    setRequestDraft: () => {},
    createRun: () => {},
    selectRun: () => {},
    openRun: () => {},
    openNewTask: () => {},
    refreshActiveRun: () => {},
    cancelActiveRun: () => {},
    resumeActiveRun: () => {},
    canCancel: false,
    canResume: false,
    isCancelling: false,
    isResuming: false,
  };
}

test("WS-01: workspaceRuns partitions by durable origin_planet, unclassified excluded", () => {
  const runs = [
    runView("r1", "s1", "mercury"),
    runView("r2", "s1", "venus"),
    runView("r3", "s1"),            // no origin evidence → 未分类
    runView("r4", "s2", "mercury"), // other session
  ];
  const ctx = ctxWith(runs);
  assert.deepEqual(workspaceRuns(ctx, "mercury").map((r) => r.runId), ["r1"]);
  assert.deepEqual(workspaceRuns(ctx, "venus").map((r) => r.runId), ["r2"]);
  assert.deepEqual(workspaceRuns(ctx, "sun"), []);
});

test("WS-01: submitToPlanet always records originPlanet and admits independent tasks", async () => {
  const service = new MockAgentServiceClient([], identity);
  const ctx = ctxWith([], service);
  const r1 = await submitToPlanet(ctx, "mercury", "task one");
  const r2 = await submitToPlanet(ctx, "mercury", "task two");
  assert.notEqual(r1, r2);
  const snaps = await service.listRuns({ tenantId: identity.tenantId, sessionId: "s1" });
  const s1 = snaps.find((s) => s.runId === r1);
  const s2 = snaps.find((s) => s.runId === r2);
  assert.equal(s1?.originPlanet, "mercury");
  assert.equal(s2?.originPlanet, "mercury");
  ctx.controller.dispose();
});

test("WS-01: delegateToPlanet anchors on the explicit parent, not the session active run", async () => {
  const service = new MockAgentServiceClient([], identity);
  const ctx = ctxWith([], service);
  const parent = runView("run-parent", "s1", "sun");
  const taskId = await delegateToPlanet(ctx, parent, "neptune", "child task");
  assert.ok(taskId);
  const tasks = await service.listTasks({ tenantId: identity.tenantId, sessionId: "s1" });
  const child = tasks.find((task) => task.taskId === taskId);
  assert.equal(child?.originPlanet, "neptune");
  assert.equal(child?.parentTaskId, "task-run-parent");
  ctx.controller.dispose();
});

test("WS-01: workspace selection is keyed by tenant/session/planet", () => {
  selectWorkspaceRun("t1", "s1", "mercury", "run-m");
  selectWorkspaceRun("t1", "s1", "venus", "run-v");
  selectWorkspaceRun("t1", "s2", "mercury", "run-other-session");
  assert.equal(getWorkspaceRunId("t1", "s1", "mercury"), "run-m");
  assert.equal(getWorkspaceRunId("t1", "s1", "venus"), "run-v");
  assert.equal(getWorkspaceRunId("t1", "s2", "mercury"), "run-other-session");
  assert.equal(getWorkspaceRunId("t2", "s1", "mercury"), "");
});
