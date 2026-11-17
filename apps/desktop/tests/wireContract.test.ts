import { test } from "vitest";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { LocalAgentServiceClient } from "../src/service/localAgentServiceClient";

/** A16: Python wire DTOs are the source of truth — this fixture is
 *  asserted on BOTH sides.  A deliberate field/enum change on either
 *  side must fail a test. */
const fixture = JSON.parse(
  readFileSync("../../tests/fixtures/wire_contract.json", "utf8"),
) as {
  methods: Record<string, {
    request: Record<string, unknown>;
    request_fields: string[];
    response_fields: string[];
    ts_result_fields: string[];
    // Real dispatcher-serialized output, populated for covered methods.
    response?: any;
  }>;
};

const snake = (camel: string) =>
  camel.replace(/[A-Z]/g, (c) => `_${c.toLowerCase()}`);

class SpyTransport {
  calls: Array<{ method: string; params: unknown }> = [];
  responses: Record<string, unknown> = {};
  async request(method: string, params: unknown) {
    this.calls.push({ method, params });
    const r = this.responses[method];
    return typeof r === "function" ? (r as () => unknown)() : r;
  }
  async close() {}
}

function client(transport: SpyTransport) {
  return new LocalAgentServiceClient(transport as never, {
    identity: { tenantId: "t", userId: "u", sessionId: "s" },
  });
}

test("start_run params match the wire fixture", async () => {
  const t = new SpyTransport();
  t.responses.start_run = {
    tenant_id: "t", session_id: "s", run_id: "run-1", task_id: "task-1",
    status: "CREATED", request_id: "r1", created_at: "x", updated_at: "x",
    revision: 1,
  };
  const c = client(t);
  const result = await c.startRun({
    sessionId: "s", requestId: "r1", requestText: "x",
    taskId: "task-1", workspaceRoot: "/w", originPlanet: "mars",
    contextRefs: ["file:a"],
    metadata: { agent_profile: { model_ref: "m" }, delegation_depth: 1 },
  });
  assert.equal(t.calls.length, 1);
  const params = t.calls[0].params as Record<string, unknown>;
  const expected = fixture.methods.start_run.request;
  assert.deepEqual(Object.keys(params).sort(), Object.keys(expected).sort());
  assert.equal(result.runId, "run-1");
  assert.equal(result.status, "pending"); // TS maps wire status to its own enum
});

test("get_run params match the wire fixture", async () => {
  const t = new SpyTransport();
  t.responses.get_run = {
    tenant_id: "t", session_id: "s", run_id: "run-1", task_id: "task-1",
    status: "RUNNING", request_id: "r2", request_text: "x",
    revision: 1, created_at: "x", updated_at: "x",
    active_workflow_id: "", completed_workflow_ids: [],
    pending_workflow_ids: [], artifacts: [], verifier_summary: null,
    output: null, failure_summary: null, resume_summary: null,
  };
  const c = client(t);
  const result = await c.getRun({ sessionId: "s", runId: "run-1" });
  const params = t.calls[0].params as Record<string, unknown>;
  const expected = fixture.methods.get_run.request;
  assert.deepEqual(Object.keys(params).sort(), Object.keys(expected).sort());
  assert.equal(result.runId, "run-1");
  assert.equal(result.status, "active"); // TS maps wire status to its own enum
});

test("list_approvals params match the wire fixture", async () => {
  const t = new SpyTransport();
  t.responses.list_approvals = [{
    approval_id: "ap-1", run_id: "run-1", action: "x", scope: "",
    params: {}, risk_level: "low", status: "PENDING",
    decided_at: "", decided_by: "", created_at: "x",
  }];
  const c = client(t);
  const result = await c.listApprovals({ tenantId: "t", sessionId: "s", runId: "" } as never);
  const params = t.calls[0].params as Record<string, unknown>;
  const expected = fixture.methods.list_approvals.request;
  assert.deepEqual(Object.keys(params).sort(), Object.keys(expected).sort());
  assert.equal(result[0].approvalId, "ap-1");
  assert.equal(result[0].status, "PENDING");
});

test("decide_approval params and values match the wire fixture", async () => {
  const t = new SpyTransport();
  // The fixture's response is the real dispatcher-serialized output —
  // the Python test asserts the produced values, so a Python-side
  // semantic change (approve→deny) is caught upstream.
  t.responses.decide_approval = fixture.methods.decide_approval.response;
  const c = client(t);
  const result = await c.decideApproval({
    tenantId: "t", sessionId: "s", runId: "run-1",
    approvalId: "ap-1", decision: "approve", requestId: "r4",
  } as never);
  const params = t.calls[0].params as Record<string, unknown>;
  const expected = fixture.methods.decide_approval.request;
  assert.deepEqual(Object.keys(params).sort(), Object.keys(expected).sort());
  const produced = fixture.methods.decide_approval.response;
  assert.equal(result.approvalId, produced.approval_id);
  assert.equal(result.status, produced.status); // "APPROVED" verbatim
});

test("delegate_run params and mapped fields match the fixture", async () => {
  const t = new SpyTransport();
  t.responses.delegate_run = {
    task_id: "task-1", handoff_id: "h-1", session_id: "s",
    target_planet: "mars", workspace_root: "/w",
    agent_profile: { model_ref: "m" }, request_text: "[Agent: p] x",
  };
  const c = client(t);
  const result = await c.delegateRun({
    sessionId: "s", requestId: "r5",
    parentRunId: "run-1", requestText: "x",
    targetPlanet: "mars", profileId: "p1", contextRefs: [],
  });
  const params = t.calls[0].params as Record<string, unknown>;
  const expected = fixture.methods.delegate_run.request;
  assert.deepEqual(Object.keys(params).sort(), Object.keys(expected).sort());
  const mapped = result as unknown as Record<string, unknown>;
  for (const field of fixture.methods.delegate_run.ts_result_fields) {
    assert.ok(field in mapped, `mapped field missing: ${field}`);
  }
  assert.equal(result.agentProfile?.model_ref, "m");
});

test("run_graph steps carry node status per the fixture", async () => {
  const t = new SpyTransport();
  // Same serialized output the Python dispatch test produces.
  t.responses.run_graph = fixture.methods.run_graph.response;
  const c = client(t);
  const steps = await c.runGraph({
    sessionId: "s", requestId: "r6",
    graphId: "g-1", parentRunId: "run-1",
  });
  const params = t.calls[0].params as Record<string, unknown>;
  const expected = fixture.methods.run_graph.request;
  assert.deepEqual(Object.keys(params).sort(), Object.keys(expected).sort());
  const produced = fixture.methods.run_graph.response[0];
  assert.equal(steps[0].nodeId, produced.node_id);
  assert.equal(steps[0].status, produced.status); // "PENDING" verbatim
});

test("ts_result_fields derive from wire response_fields", () => {
  // Every TS-mapped field must have a wire counterpart — a field the
  // client surfaces that the wire never sends is drift.
  for (const [method, entry] of Object.entries(fixture.methods)) {
    for (const camel of entry.ts_result_fields) {
      assert.ok(
        entry.response_fields.includes(snake(camel)),
        `${method}: ts_result_fields.${camel} has no wire counterpart`,
      );
    }
  }
});
