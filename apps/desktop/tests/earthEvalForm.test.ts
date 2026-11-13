import { test } from "node:test";
import assert from "node:assert/strict";
import { MockAgentServiceClient } from "../src/service/mockAgentService";
import { EARTH_VERDICTS, buildEvalRequest, composerContext, defaultParamValues, evalCandidateRef } from "../src/workspaces/earth/earthEvalForm";

/** A23: Earth 实验裁决合同 — 人工裁决词表由单一合同约束 UI/Mock/Store；
 *  滑块参数作为运行输入进入 Composer，同时是 eval 记录的 candidate 身份。 */

const ids = { tenantId: "t", sessionId: "s", requestId: "r", runId: "run-1" };

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
      buildEvalRequest({ ...ids, requestId: `req-${verdict}`, verdict, vals, notes: "" }),
    );
    assert.equal(stored.verdict, verdict);
  }
  const listed = await mock.listEvaluations({ tenantId: "t", sessionId: "s", requestId: "r-list" });
  assert.deepEqual(
    EARTH_VERDICTS.map((v) => listed.find((e) => e.notes.includes("params") && e.verdict === v)?.verdict),
    [...EARTH_VERDICTS],
  );
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
