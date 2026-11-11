import { test } from "node:test";
import assert from "node:assert/strict";
import { AgentServiceClientError } from "../src/types/service";
import type { ListApprovalsRequest, ListParticipantsRequest } from "../src/types/service";
import { UnavailableDesktopAgentServiceClient } from "../src/service/clientFactory";
import { fetchSessionSnapshot } from "../src/workspaces/sun/sessionSnapshot";

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
