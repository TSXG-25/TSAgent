import { test } from "node:test";
import assert from "node:assert/strict";
import { AgentServiceClientError } from "../src/types/service";
import type { ListApprovalsRequest, ListParticipantsRequest } from "../src/types/service";
import { UnavailableDesktopAgentServiceClient } from "../src/service/clientFactory";
import { fetchSessionSnapshot, RefreshGate } from "../src/workspaces/sun/sessionSnapshot";

/** A11: Sun 刷新只读选中 session — 审批/参与者不得混入 client identity 的会话。 */

class SpyService extends UnavailableDesktopAgentServiceClient {
  readonly scopedSessions: string[] = [];

  constructor() {
    super(
      "local",
      { tenantId: "tenant-1", userId: "user-1", sessionId: "identity-session" },
      new AgentServiceClientError({ code: "PROVIDER_UNAVAILABLE", message: "test", retryable: true }),
    );
  }

  async listSessions(): Promise<never[]> {
    return [];
  }

  async listApprovals(request?: ListApprovalsRequest): Promise<never[]> {
    this.scopedSessions.push(request!.sessionId);
    return [];
  }

  async listParticipants(request?: ListParticipantsRequest): Promise<never[]> {
    this.scopedSessions.push(request!.sessionId);
    return [];
  }
}

test("refresh scopes approvals and participants to the selected session", async () => {
  const service = new SpyService();
  const snap = await fetchSessionSnapshot(service, "selected-session");
  // 即使 client identity 指向另一会话，请求也按选中 session 发出。
  assert.deepEqual(service.scopedSessions, ["selected-session", "selected-session"]);
  assert.equal(snap.sessionId, "selected-session");
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => { resolve = res; });
  return { promise, resolve };
}

function snap(sessionId: string, tag: string) {
  return { sessionId, sessions: [], approvals: [], participants: [], tag } as never;
}

test("A→B→A: the first A response cannot overwrite the second A result", async () => {
  const gate = new RefreshGate();
  let currentSession = "A";
  const applied: string[] = [];
  // 与组件相同的提交守卫 — session 检查 + 最新序号。
  const commit = (seq: number, s: { sessionId: string; tag: string }) => {
    if (currentSession !== s.sessionId || !gate.isCurrent(seq)) return;
    applied.push(s.tag);
  };

  const a1 = deferred<{ sessionId: string; tag: string }>();
  const seqA1 = gate.begin();
  const pA1 = a1.promise.then((s) => commit(seqA1, s));
  currentSession = "B";
  const b = deferred<{ sessionId: string; tag: string }>();
  const seqB = gate.begin();
  const pB = b.promise.then((s) => commit(seqB, s));
  currentSession = "A";
  const a2 = deferred<{ sessionId: string; tag: string }>();
  const seqA2 = gate.begin();
  const pA2 = a2.promise.then((s) => commit(seqA2, s));

  // 全部迟到乱序返回 — 只有第二次 A 可提交。
  a1.resolve(snap("A", "a1-stale"));
  b.resolve(snap("B", "b-stale"));
  a2.resolve(snap("A", "a2"));
  await Promise.all([pA1, pB, pA2]);
  assert.deepEqual(applied, ["a2"]);

  gate.close();
  const seq = gate.begin();
  const late = deferred<{ sessionId: string; tag: string }>();
  const pLate = late.promise.then((s) => commit(seq, s));
  late.resolve(snap("A", "after-unmount"));
  await pLate;
  assert.deepEqual(applied, ["a2"], "unmounted refresh must never commit");
});
