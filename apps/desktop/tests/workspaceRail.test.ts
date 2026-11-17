import { test } from "vitest";
import assert from "node:assert/strict";
import { MockAgentServiceClient } from "../src/service/mockAgentService";
import { createRecordSession } from "../src/app/sessionWorkspace";
import { sendNewRecord } from "../src/app/delegate";
import { sendForView } from "../src/app/WorkspaceRail";
import { isOmniSession } from "../src/app/sessionWorkspace";
import { ideFilePrefix, splitIdeContext } from "../src/app/ideContext";
import { openRecord } from "../src/app/WorkspaceRail";
import type { RunCtx } from "../src/app/runCtx";

const identity = { tenantId: "t", userId: "u", sessionId: "s-boot" };

async function boundSource(service: MockAgentServiceClient, sessionId: string, path: string) {
  await service.createSession({ sessionId, requestId: `c-${sessionId}` });
  await service.upsertWorkspace({
    sessionId, requestId: `w-${sessionId}`,
    workspaceId: path, displayName: "proj", path,
  });
  await service.assignSessionWorkspace({
    sessionId, requestId: `a-${sessionId}`, workspaceId: path,
  });
}

test("a home new-record session inherits the user-bound project dir", async () => {
  const service = new MockAgentServiceClient([], identity);
  await boundSource(service, "session-mars-old", "/proj/a");
  const fresh = await createRecordSession(service as never, "mars", "session-mars-old");
  const resolved = await service.resolveSessionWorkspace({ sessionId: fresh });
  assert.equal(resolved.workspaceId, "/proj/a");
  assert.equal(resolved.path, "/proj/a");
});

test("an auto-managed source root is not inherited — the record resolves its own", async () => {
  const service = new MockAgentServiceClient([], identity);
  await service.createSession({ sessionId: "session-mars-old", requestId: "c0" });
  // Resolving the unbound source registers+binds its app-managed auto root.
  const source = await service.resolveSessionWorkspace({ sessionId: "session-mars-old" });
  assert.ok(source.workspaceId.startsWith("ws-auto-"));
  const fresh = await createRecordSession(service as never, "mars", "session-mars-old");
  const resolved = await service.resolveSessionWorkspace({ sessionId: fresh });
  assert.notEqual(resolved.workspaceId, source.workspaceId);
  assert.equal(resolved.workspaceId, `ws-auto-${fresh}`);
});

function fakeCtx(service: MockAgentServiceClient, startRun: (req: { sessionId: string; requestText: string }) => Promise<{ runId: string }>) {
  const selected: string[] = [];
  const ctx = {
    service,
    sessionId: "session-mars-old",
    activeRun: null,
    controller: { startRun, selectRun: () => {} },
    selectSession: (id: string) => { selected.push(id); },
  } as unknown as RunCtx;
  return { ctx, selected };
}

test("a failed home send never selects the record session", async () => {
  const service = new MockAgentServiceClient([], identity);
  await boundSource(service, "session-mars-old", "/proj/a");
  const { ctx, selected } = fakeCtx(service, () => Promise.reject(new Error("admission refused")));
  await assert.rejects(sendNewRecord(ctx, "mars", "mars", "do thing", [], {}));
  // The current session, view and session-keyed draft are untouched —
  // the typed text stays retryable under the old session's draft key.
  assert.deepEqual(selected, []);
});

test("an admitted home send files on the record session, then selects it", async () => {
  const service = new MockAgentServiceClient([], identity);
  await boundSource(service, "session-mars-old", "/proj/a");
  const seen: string[] = [];
  const { ctx, selected } = fakeCtx(service, async (req) => {
    seen.push(req.sessionId);
    return { runId: "run-new" };
  });
  const sent = await sendNewRecord(ctx, "mars", "mars", "do thing", [], {});
  assert.equal(sent.runId, "run-new");
  assert.ok(seen[0].startsWith("session-mars-"));
  assert.notEqual(seen[0], "session-mars-old");
  assert.deepEqual(selected, [sent.sessionId]);
});

test("re-picking the current record opens the thread, not just the session", () => {
  const selected: string[] = [];
  const views: string[] = [];
  const ctx = { selectSession: (id: string) => selected.push(id) };
  openRecord(ctx, (v) => views.push(v), "session-mars-old");
  // sessionId identical to the current one — the view transition must
  // not depend on the sessionId-change effect.
  assert.deepEqual(selected, ["session-mars-old"]);
  assert.deepEqual(views, ["thread"]);
});

test("the send-time context prefixes the request on both views", async () => {
  const service = new MockAgentServiceClient([], identity);
  await boundSource(service, "session-mars-old", "/proj/a");
  const seen: string[] = [];
  const { ctx } = fakeCtx(service, async (req) => { seen.push(req.requestText); return { runId: "r" }; });
  const file = () => Promise.resolve("[IDE 当前文件] /proj/a/main.py");
  await sendForView(ctx, "mars", "home", "mars", "解释下代码", [], {}, file);
  await sendForView(ctx, "mars", "thread", "mars", "解释下代码", [], {}, file);
  assert.equal(seen[0], "[IDE 当前文件] /proj/a/main.py\n解释下代码");
  assert.equal(seen[1], "[IDE 当前文件] /proj/a/main.py\n解释下代码");
});

test("no resolved context — the request goes out untouched", async () => {
  const service = new MockAgentServiceClient([], identity);
  await boundSource(service, "session-mars-old", "/proj/a");
  const seen: string[] = [];
  const { ctx } = fakeCtx(service, async (req) => { seen.push(req.requestText); return { runId: "r" }; });
  await sendForView(ctx, "mars", "thread", "mars", "解释下代码", [], {}, () => Promise.resolve(""));
  await sendForView(ctx, "mars", "thread", "mars", "解释下代码", [], {});
  assert.equal(seen[0], "解释下代码");
  assert.equal(seen[1], "解释下代码");
});

test("omni visibility — claimed records stay on their workstation", () => {
  assert.equal(isOmniSession({ planet: "mars" }), false);
  assert.equal(isOmniSession({ planet: "venus" }), false);
  assert.equal(isOmniSession({ planet: "sun" }), true);
  assert.equal(isOmniSession({ planet: "" }), true);
  // A delegated child session is unclaimed by construction — the
  // @-bridge keeps it Omni-visible.
  assert.equal(isOmniSession({}), true);
});

test("ide context round-trip — the record keeps the file, the bubble keeps the text", () => {
  const sent = `${ideFilePrefix("/proj/a/main.py")}解释下代码`;
  const req = splitIdeContext(sent);
  assert.equal(req.file, "/proj/a/main.py");
  assert.equal(req.text, "解释下代码");
  // Untagged records render verbatim — no chip, no stripping.
  const plain = splitIdeContext("解释下代码");
  assert.equal(plain.file, "");
  assert.equal(plain.text, "解释下代码");
});
