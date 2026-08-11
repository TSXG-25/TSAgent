import assert from "node:assert/strict";
import test from "node:test";
import {
  createAgentServiceClientFromConfig,
} from "../src/service/clientFactory";
import type {
  LocalRpcRequest,
  LocalRpcResponse,
} from "../src/service/localTransport";
import type {
  TauriSidecarBridge,
  TauriSidecarProcess,
} from "../src/service/tauriSidecarTransport";
import { AgentServiceClientError } from "../src/types/service";

class FakeSidecarProcess implements TauriSidecarProcess {
  readonly requests: LocalRpcRequest[] = [];
  private readonly lineListeners = new Set<(line: string) => void>();
  private readonly exitListeners = new Set<(error?: unknown) => void>();

  writeLine(line: string): void {
    const request = JSON.parse(line) as LocalRpcRequest;
    this.requests.push(request);
    const result = request.method === "health"
      ? { status: "ok", protocol_version: "desktop-local-jsonl-v1", service: "tsagent" }
      : {
          tenant_id: "tenant-1",
          session_id: "session-1",
          run_id: "run-1",
          request_id: "request-1",
          status: "CREATED",
          revision: 1,
          created_at: "2026-08-11T00:00:00Z",
          updated_at: "2026-08-11T00:00:00Z",
        };
    const response: LocalRpcResponse = { id: request.id, ok: true, result };
    queueMicrotask(() => {
      const lineValue = `${JSON.stringify(response)}\n`;
      for (const listener of this.lineListeners) listener(lineValue);
    });
  }

  onStdoutLine(listener: (line: string) => void): () => void {
    this.lineListeners.add(listener);
    return () => this.lineListeners.delete(listener);
  }

  onExit(listener: (error?: unknown) => void): () => void {
    this.exitListeners.add(listener);
    return () => this.exitListeners.delete(listener);
  }

  async close(): Promise<void> {
    this.lineListeners.clear();
    this.exitListeners.clear();
  }
}

function fakeBridge(process: FakeSidecarProcess): TauriSidecarBridge {
  return {
    async spawn(): Promise<TauriSidecarProcess> {
      return process;
    },
  };
}

test("D4A01 omitted mode is an explicit Mock client", async () => {
  const client = createAgentServiceClientFromConfig();
  assert.equal(client.mode, "mock");
  assert.equal(client.identity.sessionId, "session-desktop-mock");
  await client.ready();
  assert.ok((await client.listRuns()).length > 0);
});

test("D4A02 local mode requires tenant and user identity", async () => {
  const client = createAgentServiceClientFromConfig({ mode: "local" });
  assert.equal(client.mode, "local");
  await assert.rejects(
    client.ready(),
    (error: unknown) => error instanceof AgentServiceClientError && error.code === "INVALID_REQUEST",
  );
});

test("D4A03 local mode without a host bridge is visible and never falls back", async () => {
  const client = createAgentServiceClientFromConfig({
    mode: "local",
    identity: { tenantId: "tenant-1", userId: "user-1", sessionId: "session-1" },
  });
  assert.equal(client.mode, "local");
  await assert.rejects(
    client.ready(),
    (error: unknown) => error instanceof AgentServiceClientError && error.code === "PROVIDER_UNAVAILABLE",
  );
  await assert.rejects(client.listRuns(), (error: unknown) => error instanceof AgentServiceClientError);
});

test("D4A04 invalid mode is a configuration error, not an implicit Mock selection", async () => {
  const client = createAgentServiceClientFromConfig({ mode: "offline" });
  await assert.rejects(
    client.ready(),
    (error: unknown) => error instanceof AgentServiceClientError && error.code === "INVALID_REQUEST",
  );
  await assert.rejects(client.listRuns(), (error: unknown) => error instanceof AgentServiceClientError);
});

test("D4A05 local health and requests use one configured desktop identity", async () => {
  const process = new FakeSidecarProcess();
  const client = createAgentServiceClientFromConfig({
    mode: "local",
    identity: { tenantId: "tenant-1", userId: "user-1", sessionId: "session-1" },
    bridge: fakeBridge(process),
  });

  await client.ready();
  const handle = await client.startRun({
    tenantId: "tenant-1",
    sessionId: "session-1",
    requestId: "start-1",
    requestText: "hello",
  });

  assert.equal(client.identity.userId, "user-1");
  assert.equal(client.identity.sessionId, "session-1");
  const startRequest = process.requests.find((request) => request.method === "start_run");
  assert.deepEqual(
    {
      tenant_id: startRequest?.params.tenant_id,
      user_id: startRequest?.params.user_id,
      session_id: startRequest?.params.session_id,
    },
    { tenant_id: "tenant-1", user_id: "user-1", session_id: "session-1" },
  );
  assert.equal(handle.runId, "run-1");
  await client.close();
});

test("D4A06 local scope mismatch is rejected before transport side effects", async () => {
  const process = new FakeSidecarProcess();
  const client = createAgentServiceClientFromConfig({
    mode: "local",
    identity: { tenantId: "tenant-1", userId: "user-1", sessionId: "session-1" },
    bridge: fakeBridge(process),
  });

  await assert.rejects(
    client.startRun({
      tenantId: "tenant-2",
      sessionId: "session-2",
      requestId: "start-2",
      requestText: "must be rejected",
    }),
    (error: unknown) => error instanceof AgentServiceClientError && error.code === "IDENTITY_MISMATCH",
  );
  assert.equal(process.requests.length, 0);
  await client.close();
});
