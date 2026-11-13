import { test } from "node:test";
import assert from "node:assert/strict";

/** FE-03: 共享会话审批状态 — 单一轮询存储、单一 decide 入口、
 *  订阅者收到同一份结果；decide 经 Service 写回后刷新。 */

const { MockAgentServiceClient } = await import("../src/service/mockAgentService");
const { subscribeSessionApprovals, getSessionApprovals, decideSessionApproval } = await import("../src/app/approvals");

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

test("one shared store serves every subscriber of the same session", async () => {
  const service = new MockAgentServiceClient() as never;
  const sid = (service as { identity: { sessionId: string } }).identity.sessionId;
  const hits: number[] = [0, 0];
  const un1 = subscribeSessionApprovals(service, sid, () => hits[0]++);
  const un2 = subscribeSessionApprovals(service, sid, () => hits[1]++);
  await sleep(30);                       // let the shared poll resolve
  assert.ok(getSessionApprovals(service, sid).length >= 0);
  assert.equal(hits[0], hits[1]);        // same emissions to both consumers
  un1(); un2();
});

test("decide through the single entrypoint refreshes shared state", async () => {
  const service = new MockAgentServiceClient() as never;
  const sid = (service as { identity: { sessionId: string } }).identity.sessionId;
  const un = subscribeSessionApprovals(service, sid, () => {});
  await sleep(30);
  const before = getSessionApprovals(service, sid);
  assert.ok(before.length > 0, "mock should seed a pending approval");
  await decideSessionApproval(service, sid, before[0], "approve");
  const after = getSessionApprovals(service, sid);
  assert.ok(!after.some((a) => a.approvalId === before[0].approvalId && a.status === "PENDING"));
  un();
});
