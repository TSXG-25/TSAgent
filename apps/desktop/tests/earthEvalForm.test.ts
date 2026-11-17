import { test } from "vitest";
import assert from "node:assert/strict";
import { MockAgentServiceClient } from "../src/service/mockAgentService";
import { EARTH_VERDICTS, buildEvalRequest, composerContext, defaultParamValues, evalCandidateRef, parseRunParams } from "../src/workspaces/earth/earthEvalForm";

/** A23: Earth 实验裁决合同 — 人工裁决词表由单一合同约束 UI/Mock/Store；
 *  滑块参数作为运行输入进入 Composer，同时是 eval 记录的 candidate 身份。 */

const ids = { sessionId: "s", runId: "run-1" };

test("every public verdict builds a request carrying that verdict", () => {
  const vals = defaultParamValues();
  for (const verdict of EARTH_VERDICTS) {
    const req = buildEvalRequest({ ...ids, verdict, vals, notes: "ok" });
    assert.equal(req.verdict, verdict);
    assert.equal(req.candidateRef, `candidate:${EARTH_VERDICTS && "thyroid=50,cortisol=42,insulin=58,leptin=50,dopamine=61"}`);
  }
});

test("param snapshot reaches composer context and eval candidateRef", () => {
  const vals = { ...defaultParamValues(), dopamine: 99 };
  assert.equal(
    composerContext(vals),
    "[bio-proxy params: thyroid=50,cortisol=42,insulin=58,leptin=50,dopamine=99]",
  );
  assert.equal(evalCandidateRef(vals), "candidate:thyroid=50,cortisol=42,insulin=58,leptin=50,dopamine=99");
});

test("mock accepts each public verdict and lists it back unchanged", async () => {
  const mock = new MockAgentServiceClient();
  const vals = defaultParamValues();
  for (const verdict of EARTH_VERDICTS) {
    const stored = await mock.recordEvaluation(
      buildEvalRequest({ ...ids, verdict, vals, notes: "" }),
    );
    assert.equal(stored.verdict, verdict);
  }
  const listed = await mock.listEvaluations({ sessionId: "s", requestId: "r-list" });
  assert.deepEqual(
    EARTH_VERDICTS.map((v) => listed.find((e) => e.notes.includes("params") && e.verdict === v)?.verdict),
    [...EARTH_VERDICTS],
  );
});

test("verdict params come from the run's own request, not live sliders", () => {
  // 提交时 dopamine=61 — 之后滑块改到 99 也不能污染原 Run 的裁决。
  const submitted = `${composerContext({ ...defaultParamValues(), dopamine: 61 })}\nsim body`;
  const bound = parseRunParams(submitted);
  assert.equal(bound?.dopamine, 61);
  const req = buildEvalRequest({ ...ids, verdict: "keep_candidate", vals: bound, notes: "looks stable" });
  assert.equal(req.candidateRef, "candidate:thyroid=50,cortisol=42,insulin=58,leptin=50,dopamine=61");
  // 填写备注时快照仍保留在 notes 中。
  assert.ok(req.notes?.includes('"dopamine":61'));
  assert.ok(req.notes?.includes("looks stable"));
});

test("foreign run without the params prefix does not fabricate sliders", () => {
  assert.equal(parseRunParams("plain request, no snapshot"), null);
  const req = buildEvalRequest({ ...ids, verdict: "inconclusive", vals: null, notes: "" });
  assert.equal(req.candidateRef, "candidate:run-1");
  assert.ok(!req.notes?.includes("dopamine"));
});

test("mock scopes evaluations to the requesting session", async () => {
  const mock = new MockAgentServiceClient([], { tenantId: "t", userId: "u", sessionId: "s" });
  await mock.recordEvaluation(buildEvalRequest({ ...ids, verdict: "keep_candidate", vals: defaultParamValues(), notes: "" }));
  // 同会话可见；另一会话不可见。
  assert.equal((await mock.listEvaluations({ sessionId: "s", requestId: "r1" })).length, 1);
  assert.equal((await mock.listEvaluations({ sessionId: "session-b", requestId: "r2" })).length, 0);
  // allSessions 与真实服务一致 — 跨会话列出同一 tenant 的记录。
  assert.equal((await mock.listEvaluations({ sessionId: "session-b", requestId: "r3", allSessions: true })).length, 1);
});

test("mock rejects a verdict outside the human vocabulary", async () => {
  const mock = new MockAgentServiceClient();
  await assert.rejects(
    mock.recordEvaluation(
      buildEvalRequest({ ...ids, verdict: "pass" as never, vals: defaultParamValues(), notes: "" }),
    ),
    (err: unknown) => err instanceof Error && "code" in err && (err as { code: string }).code === "INVALID_REQUEST",
  );
});
