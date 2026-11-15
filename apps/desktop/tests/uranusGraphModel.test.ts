import { test } from "node:test";
import assert from "node:assert/strict";

/** Uranus DAG canvas model — definition_json ↔ canvas round-trip,
 *  layout for coordinate-less graphs, edge validation, execution
 *  status overlay mapping. */

const {
  parseGraphDefinition,
  serializeGraphDefinition,
  nextNodeId,
  executionStatusByNode,
} = await import("../src/workspaces/uranus/graphModel");

test("round-trip preserves nodes, edges, profile, target planet, coordinates", () => {
  const definition = JSON.stringify({
    nodes: [
      { id: "n1", type: "task", request_text: "write script", profile_id: "p-forge", target_planet: "mars", x: 10, y: 20, custom_field: "keep" },
      { id: "n2", type: "task", request_text: "render {artifacts:n1}", x: 300, y: 20 },
      { id: "cfg", type: "input", name: "topic", value: "ocean", x: 10, y: 160 },
    ],
    edges: [{ from: "n1", to: "n2" }, { from: "cfg", to: "n1" }],
  });
  const canvas = parseGraphDefinition(definition);
  assert.equal(canvas.nodes.length, 3);
  assert.equal(canvas.edges.length, 2);
  const n1 = canvas.nodes.find((n) => n.id === "n1");
  assert.equal(n1?.profileId, "p-forge");
  assert.equal(n1?.targetPlanet, "mars");
  assert.equal(n1?.x, 10);
  const out = serializeGraphDefinition(canvas) as {
    nodes: Array<Record<string, unknown>>;
    edges: Array<{ from: string; to: string }>;
  };
  const on1 = out.nodes.find((n) => n.id === "n1");
  assert.equal(on1?.request_text, "write script");
  assert.equal(on1?.profile_id, "p-forge");
  assert.equal(on1?.target_planet, "mars");
  assert.equal(on1?.x, 10);
  assert.equal(on1?.custom_field, "keep");
  const ocfg = out.nodes.find((n) => n.id === "cfg");
  assert.equal(ocfg?.name, "topic");
  assert.equal(ocfg?.value, "ocean");
  assert.deepEqual(out.edges, [{ from: "n1", to: "n2" }, { from: "cfg", to: "n1" }]);
});

test("coordinate-less graph gets layered left-to-right layout", () => {
  const canvas = parseGraphDefinition(JSON.stringify({
    nodes: [
      { id: "n1", type: "task", request_text: "a" },
      { id: "n2", type: "task", request_text: "b" },
      { id: "n3", type: "task", request_text: "c" },
    ],
    edges: [{ from: "n1", to: "n2" }, { from: "n2", to: "n3" }],
  }));
  const [n1, n2, n3] = ["n1", "n2", "n3"].map(
    (id) => canvas.nodes.find((n) => n.id === id)!,
  );
  // 拓扑分层 — n1 最左，n3 最右。
  assert.ok(n1.x < n2.x && n2.x < n3.x);
});

test("edges referencing dropped/unknown nodes are discarded", () => {
  const canvas = parseGraphDefinition(JSON.stringify({
    nodes: [{ id: "n1", type: "task", request_text: "a" }],
    edges: [{ from: "n1", to: "ghost" }, "junk", { from: "n1", to: "n1" }],
  }));
  // n1→n1 自环保留（后端拓扑排序会判环拒绝，画布如实反映）
  assert.deepEqual(canvas.edges, [{ from: "n1", to: "n1" }]);
});

test("malformed definition parses to empty canvas", () => {
  assert.deepEqual(parseGraphDefinition("not json"), { nodes: [], edges: [] });
  assert.deepEqual(parseGraphDefinition('{"nodes": 5}'), { nodes: [], edges: [] });
});

test("nextNodeId picks lowest unused n-suffix", () => {
  const canvas = parseGraphDefinition(JSON.stringify({
    nodes: [
      { id: "n1", type: "task", request_text: "a" },
      { id: "n7", type: "task", request_text: "b" },
      { id: "weird", type: "task", request_text: "c" },
    ],
    edges: [],
  }));
  assert.equal(nextNodeId(canvas.nodes), "n8");
});

test("executionStatusByNode maps steps for canvas overlay", () => {
  const map = executionStatusByNode([
    { nodeId: "n1", taskId: "t1", runId: "r1", sessionId: "s", status: "COMPLETED", executionId: "e" },
    { nodeId: "n2", taskId: "", runId: "", sessionId: "s", status: "SKIPPED", executionId: "e", detail: "predecessor not COMPLETED" },
  ]);
  assert.equal(map.get("n1")?.status, "COMPLETED");
  assert.equal(map.get("n2")?.detail, "predecessor not COMPLETED");
  assert.equal(map.get("n3"), undefined);
  assert.equal(executionStatusByNode(undefined).size, 0);
});
