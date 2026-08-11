import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";
import {
  AgentServiceClientError,
} from "../src/types/service";
import {
  LocalAgentServiceClient,
  type LocalAgentServiceClientOptions,
} from "../src/service/localAgentServiceClient";
import {
  TauriSidecarTransport,
  type TauriSidecarBridge,
  type TauriSidecarProcess,
} from "../src/service/tauriSidecarTransport";
import type {
  LocalRpcParams,
  LocalRpcRequest,
  LocalRpcResponse,
  LocalTransport,
  LocalTransportMethod,
} from "../src/service/localTransport";

function response(id: string, result: unknown): LocalRpcResponse {
  return { id, ok: true, result };
}

class FakeSidecarProcess implements TauriSidecarProcess {
  readonly requests: LocalRpcRequest[] = [];
  closed = false;
  private readonly lineListeners = new Set<(line: string) => void>();
  private readonly exitListeners = new Set<(error?: unknown) => void>();
  onRequest?: (request: LocalRpcRequest, process: FakeSidecarProcess) => void;

  writeLine(line: string): void {
    const request = JSON.parse(line) as LocalRpcRequest;
    this.requests.push(request);
    this.onRequest?.(request, this);
  }

  onStdoutLine(listener: (line: string) => void): () => void {
    this.lineListeners.add(listener);
    return () => this.lineListeners.delete(listener);
  }

  onExit(listener: (error?: unknown) => void): () => void {
    this.exitListeners.add(listener);
    return () => this.exitListeners.delete(listener);
  }

  emit(responseValue: LocalRpcResponse): void {
    const line = `${JSON.stringify(responseValue)}\n`;
    for (const listener of this.lineListeners) listener(line);
  }

  emitRaw(line: string): void {
    for (const listener of this.lineListeners) listener(line);
  }

  emitExit(error?: unknown): void {
    for (const listener of this.exitListeners) listener(error);
  }

  async close(): Promise<void> {
    this.closed = true;
  }
}

class FakeBridge implements TauriSidecarBridge {
  readonly process = new FakeSidecarProcess();
  spawnCount = 0;

  async spawn(): Promise<TauriSidecarProcess> {
    this.spawnCount += 1;
    return this.process;
  }
}

class ChildProcessSidecar implements TauriSidecarProcess {
  private readonly child;
  private readonly lineListeners = new Set<(line: string) => void>();
  private buffer = "";

  constructor(database: string, workspace: string) {
    const repositoryRoot = resolve(process.cwd(), "../..");
    this.child = spawn(
      process.env.TSAGENT_PYTHON ?? "python3",
      [
        "-m",
        "agent.service.local_sidecar",
        "--database",
        database,
        "--workspace-root",
        workspace,
      ],
      {
        cwd: repositoryRoot,
        env: {
          ...process.env,
          PYTHONPATH: [repositoryRoot, process.env.PYTHONPATH].filter(Boolean).join(":"),
          TSAGENT_LLM_TIMEOUT: "0.05",
        },
        stdio: ["pipe", "pipe", "pipe"],
      },
    );
    this.child.stdout?.setEncoding("utf8");
    this.child.stdout?.on("data", (chunk: string) => {
      this.buffer += chunk;
      let newline = this.buffer.indexOf("\n");
      while (newline >= 0) {
        const line = this.buffer.slice(0, newline);
        this.buffer = this.buffer.slice(newline + 1);
        for (const listener of this.lineListeners) listener(line);
        newline = this.buffer.indexOf("\n");
      }
    });
    this.child.stderr?.on("data", () => undefined);
  }

  writeLine(line: string): Promise<void> {
    return new Promise((resolveWrite, rejectWrite) => {
      if (!this.child.stdin || this.child.stdin.destroyed) {
        rejectWrite(new Error("sidecar stdin is closed"));
        return;
      }
      this.child.stdin.write(line, "utf8", (error) => (error ? rejectWrite(error) : resolveWrite()));
    });
  }

  onStdoutLine(listener: (line: string) => void): () => void {
    this.lineListeners.add(listener);
    return () => this.lineListeners.delete(listener);
  }

  onExit(listener: (error?: unknown) => void): () => void {
    const onExit = (code: number | null, signal: NodeJS.Signals | null) => {
      listener(code === 0 ? undefined : `exit ${String(code ?? signal ?? "unknown")}`);
    };
    this.child.once("exit", onExit);
    return () => this.child.removeListener("exit", onExit);
  }

  async close(): Promise<void> {
    this.lineListeners.clear();
    if (this.child.exitCode !== null || this.child.signalCode !== null) return;
    this.child.kill();
    await new Promise<void>((resolveExit) => this.child.once("exit", () => resolveExit()));
  }
}

class StubTransport implements LocalTransport {
  readonly calls: Array<{ method: LocalTransportMethod; params: LocalRpcParams }> = [];
  readonly responses = new Map<LocalTransportMethod, unknown[]>();
  closed = false;

  enqueue(method: LocalTransportMethod, value: unknown): void {
    const queue = this.responses.get(method) ?? [];
    queue.push(value);
    this.responses.set(method, queue);
  }

  async request<TRequest extends LocalRpcParams, TResponse>(
    method: LocalTransportMethod,
    params: TRequest,
  ): Promise<TResponse> {
    this.calls.push({ method, params });
    const queue = this.responses.get(method) ?? [];
    if (queue.length === 0) throw new Error(`No stub response for ${method}`);
    const value = queue.shift();
    if (value instanceof Error) throw value;
    return value as TResponse;
  }

  async close(): Promise<void> {
    this.closed = true;
  }
}

const deterministicClientOptions: LocalAgentServiceClientOptions = {
  userId: "user-1",
  requestIdFactory: (operation, sequence) => `${operation}-lookup-${sequence}`,
};

function snapshot(status = "CANCELLING"): Record<string, unknown> {
  return {
    tenant_id: "tenant-1",
    session_id: "session-1",
    run_id: "run-1",
    request_id: "start-1",
    status,
    request_text: "test request",
    active_workflow_id: "workflow-1",
    created_at: "2026-08-11T00:00:00Z",
    updated_at: "2026-08-11T00:00:01Z",
    completed_workflow_ids: [],
    pending_workflow_ids: ["workflow-1"],
    artifacts: [],
    verifier_summary: { status: "waiting", checks: "—", stdout: "—", detail: "waiting" },
    resume_summary: null,
    failure_summary: null,
    output: null,
    revision: 2,
  };
}

test("LC01 health round-trip", async () => {
  const bridge = new FakeBridge();
  const transport = new TauriSidecarTransport(bridge, {
    requestIdFactory: (sequence, method) => `${method}-${sequence}`,
  });
  const request = transport.request("health", {});
  await new Promise((resolve) => setImmediate(resolve));
  bridge.process.emit(response("health-1", { status: "ok", protocol_version: "desktop-local-jsonl-v1", service: "ready" }));
  assert.deepEqual(await request, { status: "ok", protocol_version: "desktop-local-jsonl-v1", service: "ready" });
  await transport.close();
});

test("LC02 concurrent requests correlate by response ID, not response order", async () => {
  const bridge = new FakeBridge();
  const transport = new TauriSidecarTransport(bridge, {
    requestIdFactory: (sequence, method) => `${method}-${sequence}`,
  });
  const first = transport.request("get_run", { request: "first" });
  const second = transport.request("get_run", { request: "second" });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(bridge.process.requests.length, 2);
  bridge.process.emit(response("get_run-2", { value: "second" }));
  bridge.process.emit(response("get_run-1", { value: "first" }));
  assert.deepEqual(await first, { value: "first" });
  assert.deepEqual(await second, { value: "second" });
  await transport.close();
});

test("LC03 unknown and duplicate responses never corrupt a pending request", async () => {
  const protocolErrors: string[] = [];
  const bridge = new FakeBridge();
  const transport = new TauriSidecarTransport(bridge, {
    requestIdFactory: (sequence, method) => `${method}-${sequence}`,
    onProtocolError: (error) => protocolErrors.push(error.message),
  });
  const pending = transport.request("health", {});
  await new Promise((resolve) => setImmediate(resolve));
  bridge.process.emit(response("unknown", { bad: true }));
  bridge.process.emit(response("health-1", { ok: true }));
  bridge.process.emit(response("health-1", { duplicate: true }));
  assert.deepEqual(await pending, { ok: true });
  assert.equal(protocolErrors.length, 2);
  await transport.close();
});

test("LC04 malformed stdout rejects all pending requests with a stable protocol error", async () => {
  const bridge = new FakeBridge();
  const transport = new TauriSidecarTransport(bridge, {
    requestIdFactory: (sequence, method) => `${method}-${sequence}`,
  });
  const first = transport.request("health", {});
  const second = transport.request("health", {});
  await new Promise((resolve) => setImmediate(resolve));
  bridge.process.emitRaw("not-json\n");
  await assert.rejects(first, (error: unknown) => error instanceof AgentServiceClientError && error.code === "INTERNAL_ERROR");
  await assert.rejects(second, (error: unknown) => error instanceof AgentServiceClientError && error.code === "INTERNAL_ERROR");
  await transport.close();
});

test("LC05 sidecar exit rejects every pending request", async () => {
  const bridge = new FakeBridge();
  const transport = new TauriSidecarTransport(bridge, {
    requestIdFactory: (sequence, method) => `${method}-${sequence}`,
  });
  const pending = transport.request("health", {});
  await new Promise((resolve) => setImmediate(resolve));
  bridge.process.emitExit("SIGKILL");
  await assert.rejects(pending, (error: unknown) => error instanceof AgentServiceClientError && error.code === "PROVIDER_UNAVAILABLE");
  await transport.close();
});

test("LC06 request timeout is bounded and does not hang", async () => {
  const bridge = new FakeBridge();
  const transport = new TauriSidecarTransport(bridge, {
    requestTimeoutMs: 5,
    requestIdFactory: (sequence, method) => `${method}-${sequence}`,
  });
  await assert.rejects(transport.request("health", {}), (error: unknown) => error instanceof AgentServiceClientError && error.code === "PROVIDER_UNAVAILABLE");
  await transport.close();
});

test("LC07 getRun projects the public snapshot and maps CANCELLING exactly", async () => {
  const transport = new StubTransport();
  transport.enqueue("get_run", snapshot("CANCELLING"));
  const client = new LocalAgentServiceClient(transport, deterministicClientOptions);
  const result = await client.getRun({ tenantId: "tenant-1", sessionId: "session-1", runId: "run-1" });
  assert.equal(result.status, "cancelling");
  assert.equal(result.runId, "run-1");
  assert.equal(transport.calls[0]?.params.user_id, "user-1");
  assert.equal(transport.calls[0]?.params.request_id, "get_run-lookup-1");
});

test("LC08 listArtifacts projects opaque references without exposing paths", async () => {
  const transport = new StubTransport();
  transport.enqueue("list_artifacts", [
    {
      artifact_id: "artifact-1",
      artifact_type: "python",
      digest: "sha256:abc",
      reference: "workspace://run-1/output/result.py",
      exists: true,
      verified: true,
      run_id: "run-1",
      display_name: "output/result.py",
      size: 12,
      created_revision: 3,
      created_at: "2026-08-11T00:00:01Z",
    },
  ]);
  const client = new LocalAgentServiceClient(transport, deterministicClientOptions);
  const [artifact] = await client.listArtifacts({ tenantId: "tenant-1", sessionId: "session-1", runId: "run-1" });
  assert.equal(artifact?.type, "python");
  assert.equal(artifact?.reference, "workspace://run-1/output/result.py");
  assert.equal(artifact?.displayName, "output/result.py");
});

test("LC09 readEvents enforces the exclusive cursor and deduplicates within a batch", async () => {
  const transport = new StubTransport();
  transport.enqueue("read_events", [
    { event_id: "event-2", sequence_number: 2, event_type: "run_started", tenant_id: "tenant-1", session_id: "session-1", run_id: "run-1", workflow_id: null, stage_id: null, task_id: null, run_revision: 2, timestamp: "t2", payload: {} },
    { event_id: "event-1", sequence_number: 1, event_type: "run_created", tenant_id: "tenant-1", session_id: "session-1", run_id: "run-1", workflow_id: null, stage_id: null, task_id: null, run_revision: 1, timestamp: "t1", payload: {} },
    { event_id: "event-2", sequence_number: 2, event_type: "run_started", tenant_id: "tenant-1", session_id: "session-1", run_id: "run-1", workflow_id: null, stage_id: null, task_id: null, run_revision: 2, timestamp: "t2", payload: {} },
  ]);
  const client = new LocalAgentServiceClient(transport, deterministicClientOptions);
  const events = await client.readEvents({ tenantId: "tenant-1", sessionId: "session-1", runId: "run-1", afterSequence: 0 });
  assert.deepEqual(events.map((event) => event.eventId), ["event-1", "event-2"]);
  assert.equal(transport.calls[0]?.params.after_sequence, 0);
});

test("LC10 startRun maps identity and returns the durable handle", async () => {
  const transport = new StubTransport();
  transport.enqueue("start_run", {
    tenant_id: "tenant-1",
    session_id: "session-1",
    run_id: "run-1",
    request_id: "start-1",
    status: "CREATED",
    revision: 1,
    created_at: "2026-08-11T00:00:00Z",
    updated_at: "2026-08-11T00:00:00Z",
  });
  const client = new LocalAgentServiceClient(transport, deterministicClientOptions);
  const handle = await client.startRun({ tenantId: "tenant-1", sessionId: "session-1", requestId: "start-1", requestText: "hello" });
  assert.equal(handle.status, "pending");
  assert.equal(transport.calls[0]?.params.user_id, "user-1");
  assert.equal(transport.calls[0]?.params.request_text, "hello");
});

test("LC11 resumeRun maps all public resume actions", async () => {
  const transport = new StubTransport();
  transport.enqueue("resume_run", {
    tenant_id: "tenant-1",
    session_id: "session-1",
    run_id: "run-1",
    request_id: "resume-1",
    status: "RUNNING",
    revision: 4,
    created_at: "2026-08-11T00:00:00Z",
    updated_at: "2026-08-11T00:00:04Z",
  });
  const client = new LocalAgentServiceClient(transport, deterministicClientOptions);
  const handle = await client.resumeRun({ tenantId: "tenant-1", sessionId: "session-1", runId: "run-1", resumeRequestId: "resume-1", checkpointId: "checkpoint-1", action: "REPLAY_FROM_STAGE" });
  assert.equal(handle.status, "active");
  assert.equal(transport.calls[0]?.params.action, "REPLAY_FROM_STAGE");
});

test("LC12 cancelRun preserves CANCELLING and TIMED_OUT remains distinct", async () => {
  const transport = new StubTransport();
  transport.enqueue("cancel_run", snapshot("CANCELLING"));
  transport.enqueue("get_run", snapshot("TIMED_OUT"));
  const client = new LocalAgentServiceClient(transport, deterministicClientOptions);
  const cancelling = await client.cancelRun({ tenantId: "tenant-1", sessionId: "session-1", runId: "run-1", requestId: "cancel-1", requestedBy: "desktop" });
  const timedOut = await client.getRun({ tenantId: "tenant-1", sessionId: "session-1", runId: "run-1" });
  assert.equal(cancelling.status, "cancelling");
  assert.equal(timedOut.status, "timed_out");
});

test("LC13 ServiceError mapping remains stable", async () => {
  const transport = new StubTransport();
  transport.enqueue("get_run", new AgentServiceClientError({ code: "RUN_NOT_FOUND", message: "run was not found", retryable: false }));
  const client = new LocalAgentServiceClient(transport, deterministicClientOptions);
  await assert.rejects(
    client.getRun({ tenantId: "tenant-1", sessionId: "session-1", runId: "missing" }),
    (error: unknown) => error instanceof AgentServiceClientError && error.code === "RUN_NOT_FOUND" && error.message === "run was not found",
  );
});

test("LC14 sidecar failure never falls back to Mock", async () => {
  let spawnCount = 0;
  const bridge: TauriSidecarBridge = {
    async spawn(): Promise<TauriSidecarProcess> {
      spawnCount += 1;
      throw new Error("sidecar unavailable");
    },
  };
  const transport = new TauriSidecarTransport(bridge);
  const client = new LocalAgentServiceClient(transport, deterministicClientOptions);
  await assert.rejects(client.health(), (error: unknown) => error instanceof AgentServiceClientError && error.code === "PROVIDER_UNAVAILABLE");
  assert.equal(spawnCount, 1);
  await transport.close();
});

test("LC15 shutdown closes the transport without creating cancellation", async () => {
  const transport = new StubTransport();
  transport.enqueue("shutdown", { status: "shutting_down" });
  const client = new LocalAgentServiceClient(transport, deterministicClientOptions);
  await client.shutdown();
  assert.equal(transport.closed, true);
  assert.equal(transport.calls[0]?.method, "shutdown");
  assert.deepEqual(transport.calls[0]?.params, {});
});

test("LC16 real Python sidecar JSONL smoke", async () => {
  const directory = await mkdtemp(join(tmpdir(), "tsagent-desktop3-"));
  const bridge: TauriSidecarBridge = {
    async spawn(): Promise<TauriSidecarProcess> {
      return new ChildProcessSidecar(join(directory, "runtime.sqlite"), join(directory, "workspace"));
    },
  };
  const transport = new TauriSidecarTransport(bridge, { requestTimeoutMs: 30_000 });
  const client = new LocalAgentServiceClient(transport, { userId: "user-1" });
  try {
    const health = await client.health();
    assert.equal(health.protocolVersion, "desktop-local-jsonl-v1");
    const handle = await client.startRun({
      tenantId: "tenant-1",
      sessionId: "session-1",
      requestId: "desktop-smoke-start",
      requestText: "生成一个最小结果",
    });
    assert.ok(handle.runId);
    const snapshot = await client.getRun({
      tenantId: "tenant-1",
      sessionId: "session-1",
      runId: handle.runId,
    });
    assert.equal(snapshot.runId, handle.runId);
    await client.shutdown();
  } finally {
    await client.close();
    await rm(directory, { recursive: true, force: true });
  }
});
