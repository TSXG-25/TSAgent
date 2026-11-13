import { test } from "node:test";
import assert from "node:assert/strict";
import { VENUS_PRESETS, styleContext } from "../src/workspaces/venus/venusStyle";
import { mediaDelegationRequest } from "../src/workspaces/neptune/neptuneOps";
import { accessRows } from "../src/workspaces/jupiter/jupiterAccess";

/** A25: 工作台能力 — 声明的操作真实进入请求；
 *  未安装能力明示；检查栏反映记录真实字段而非固定文案。 */

test("venus: style.md and applied preset ride the generation request", () => {
  const ctx = styleContext("- 语气克制\n- 结构先行", "editorial");
  assert.ok(ctx.includes("[style preset: Editorial]"));
  assert.ok(ctx.includes("[style.md]\n- 语气克制"));
  // 无 preset / 空 style 时不产生占位前缀
  assert.equal(styleContext("   ", null), "");
  assert.equal(styleContext("- a", null), "[style.md]\n- a");
  assert.ok(VENUS_PRESETS.some((p) => p.id === "editorial" && p.name === "Editorial"));
});

test("neptune: media delegation binds the artifact's producing run", () => {
  const req = mediaDelegationRequest("output/poster.svg", "run-77");
  assert.ok(req.includes("output/poster.svg"));
  assert.ok(req.includes("run-77"));  // 所属范围 — 不是裸路径委派
});

test("jupiter: access rows derive from real artifact fields", () => {
  const rows = accessRows({
    sessionId: "s", runId: "run-1", artifactId: "a1",
    artifactType: "text/markdown", reference: "doc/a.md", digest: "d",
    exists: true, verified: false, producerTaskId: "t", updatedAt: "u",
  });
  assert.deepEqual(
    rows.map((r) => [r.label, r.value]),
    [["exists", "✓"], ["verified", "—"], ["type", "text/markdown"], ["run", "run-1"]],
  );
});

test("jupiter: fact rows show scope/source/revision, not fake permissions", () => {
  const rows = accessRows({
    category: "pref", key: "tone", value: "calm", scope: "session",
    evidenceId: "ev-1", sourceKind: "run", sourceRef: "r", revision: 4,
    createdAt: "c",
  });
  const labels = rows.map((r) => r.label);
  assert.deepEqual(labels, ["scope", "source", "revision", "evidence"]);
  assert.ok(!labels.includes("write") && !labels.includes("execute"));
});
