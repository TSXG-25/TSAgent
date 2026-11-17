import { test } from "vitest";
import assert from "node:assert/strict";

/** Uranus DAG canvas model — definition_json ↔ canvas round-trip,
 *  layout for coordinate-less graphs, edge validation, execution
 *  status overlay mapping. */

const {
  parseGraphDefinition,
  serializeGraphDefinition,
  nextNodeId,
  executionStatusByNode,
  applyNodeDeltas,
  applyEdgeDeltas,
  DraftSaveGate,
  overlayStepsFor,
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

test("applyNodeDeltas writes select changes back (controlled contract)", () => {
  const canvas = parseGraphDefinition(JSON.stringify({
    nodes: [
      { id: "n1", type: "task", request_text: "a" },
      { id: "n2", type: "task", request_text: "b" },
    ],
    edges: [],
  }));
  // 打开图后点击 n2 — select 增量驱动右侧配置。
  const { selectNext } = applyNodeDeltas(canvas, [
    { kind: "select", id: "n2", selected: true },
  ]);
  assert.equal(selectNext, "n2");
  // 点击边/空白 — 只有反选增量，节点选择清空。
  const desel = applyNodeDeltas(canvas, [
    { kind: "select", id: "n2", selected: false },
  ]);
  assert.equal(desel.selectNext, null);
  // 选中后被删除 — 不产生对已删节点的选择。
  const gone = applyNodeDeltas(canvas, [
    { kind: "remove", id: "n2" },
    { kind: "select", id: "n2", selected: true },
  ]);
  assert.equal(gone.selectNext, null);
  assert.equal(gone.graph.nodes.length, 1);
});

test("applyEdgeDeltas: select writes back so Delete removes the right edge", () => {
  const canvas = parseGraphDefinition(JSON.stringify({
    nodes: [
      { id: "n1", type: "task", request_text: "a" },
      { id: "n2", type: "task", request_text: "b" },
      { id: "n3", type: "task", request_text: "c" },
    ],
    edges: [{ from: "n1", to: "n2" }, { from: "n2", to: "n3" }],
  }));
  let sel = new Set<string>();
  // 选择 n2->n3 —— selected 写回后 Delete 才能命中。
  let r = applyEdgeDeltas(canvas, [{ kind: "select", id: "n2->n3", selected: true }], sel);
  sel = r.edgeSel;
  assert.deepEqual([...sel], ["n2->n3"]);
  r = applyEdgeDeltas(r.graph, [{ kind: "remove", id: "n2->n3" }], sel);
  assert.deepEqual(r.graph.edges, [{ from: "n1", to: "n2" }]);
  assert.equal(r.edgeSel.size, 0);
});

test("DraftSaveGate rejects receipts after edits or graph switch", () => {
  const gate = new DraftSaveGate();
  // 基线：提交后无编辑 — 回执可提交身份。
  const clean = gate.submit("g-a");
  assert.equal(gate.accept(clean, "g-a"), true);
  // 保存 A 期间继续编辑 — 编辑推进版本，回执被拒：不把新草稿误标已保存。
  const t1 = gate.submit("g-a");
  gate.touch();
  assert.equal(gate.accept(t1, "g-a"), false);
  // 保存 A 期间打开 B — 身份不匹配，回执不把 openGraphId 改回 A。
  const t2 = gate.submit("g-a");
  assert.equal(gate.accept(t2, "g-b"), false);
  // 重新提交则新票据生效。
  const t3 = gate.submit("g-b");
  assert.equal(gate.accept(t3, "g-b"), true);
});

test("overlayStepsFor only paints onto the launched definition", () => {
  const canvas = parseGraphDefinition(JSON.stringify({
    nodes: [{ id: "n1", type: "task", request_text: "old task" }],
    edges: [],
  }));
  const definition = JSON.stringify(serializeGraphDefinition(canvas));
  const execA = { executionId: "e-a", steps: [{ nodeId: "n1", taskId: "t", runId: "r", sessionId: "s", status: "COMPLETED" }] };
  const bound = { execId: "e-a", definition };
  // 本端发起的执行 + 同一画布 → 叠加。
  assert.equal(overlayStepsFor({ execution: execA, execSel: "e-a", bound, canvas }).get("n1")?.status, "COMPLETED");
  // 编辑 n1 文本后 — 定义不一致，旧 COMPLETED 不得贴上。
  const edited = { ...canvas, nodes: canvas.nodes.map((n) => ({ ...n, requestText: "new task" })) };
  assert.equal(overlayStepsFor({ execution: execA, execSel: "e-a", bound, canvas: edited }).size, 0);
  // 历史执行（无发起绑定）— 无叠加，只在底部展示。
  assert.equal(overlayStepsFor({ execution: execA, execSel: "e-a", bound: null, canvas }).size, 0);
  // 选中 B 但拿到 A 的响应 — 执行 ID 不匹配，不展示。
  const boundB = { execId: "e-b", definition };
  assert.equal(overlayStepsFor({ execution: execA, execSel: "e-b", bound: boundB, canvas }).size, 0);
  // 绑定 B 而执行是 A — 同样拒绝。
  assert.equal(overlayStepsFor({ execution: execA, execSel: "e-a", bound: boundB, canvas }).size, 0);
});
