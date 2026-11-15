import assert from "node:assert/strict";
import { test } from "node:test";
import { MockAgentServiceClient } from "../src/service/mockAgentService";
import { resolveBoundPath } from "../src/app/sessionWorkspace";

const identity = { tenantId: "t", userId: "u", sessionId: "s-boot" };

test("resolveBoundPath joins the session's durable workspace binding to its path", async () => {
  const service = new MockAgentServiceClient([], identity);
  await service.createSession({ tenantId: "t", userId: "u", sessionId: "s-a", requestId: "r0" });
  await service.upsertWorkspace({
    tenantId: "t", sessionId: "s-a", requestId: "r1",
    workspaceId: "ws-a", displayName: "A", path: "/tmp/dir-a",
  });
  await service.assignSessionWorkspace({
    tenantId: "t", sessionId: "s-a", requestId: "r2", workspaceId: "ws-a",
  });
  assert.equal(await resolveBoundPath(service, "s-a"), "/tmp/dir-a");
  // A different session stays unbound — no fallback to another session's dir.
  assert.equal(await resolveBoundPath(service, "s-b"), "");
});

test("rebind moves the resolved root; unbinding clears it", async () => {
  const service = new MockAgentServiceClient([], identity);
  await service.createSession({ tenantId: "t", userId: "u", sessionId: "s-a", requestId: "r0" });
  await service.upsertWorkspace({
    tenantId: "t", sessionId: "s-a", requestId: "r1",
    workspaceId: "ws-a", displayName: "A", path: "/tmp/dir-a",
  });
  await service.upsertWorkspace({
    tenantId: "t", sessionId: "s-a", requestId: "r2",
    workspaceId: "ws-b", displayName: "B", path: "/tmp/dir-b",
  });
  await service.assignSessionWorkspace({ tenantId: "t", sessionId: "s-a", requestId: "r3", workspaceId: "ws-a" });
  await service.assignSessionWorkspace({ tenantId: "t", sessionId: "s-a", requestId: "r4", workspaceId: "ws-b" });
  assert.equal(await resolveBoundPath(service, "s-a"), "/tmp/dir-b");
  await service.assignSessionWorkspace({ tenantId: "t", sessionId: "s-a", requestId: "r5", workspaceId: "" });
  assert.equal(await resolveBoundPath(service, "s-a"), "");
});
