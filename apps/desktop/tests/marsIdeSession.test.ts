import { test } from "node:test";
import assert from "node:assert/strict";
import { MarsIdeSession, type IdeBridgeLike } from "../src/workspaces/mars/ideSession";
import type { IdeInfo } from "../src/service/tauriBridge";

function fakeBridge(opts: { holdStart?: boolean } = {}) {
  const calls: string[] = [];
  let releaseStart: ((info: IdeInfo) => void) | null = null;
  const bridge: IdeBridgeLike = {
    start: (workspace: string) => {
      calls.push(`start:${workspace}`);
      if (!opts.holdStart) {
        return Promise.resolve({ url: "http://127.0.0.1:4000", port: 4000, workspace });
      }
      return new Promise<IdeInfo>((res) => { releaseStart = res; });
    },
    stop: () => {
      calls.push("stop");
      return Promise.resolve();
    },
  };
  return {
    bridge,
    calls,
    resolveStart(workspace = "/ws/a") {
      releaseStart?.({ url: "http://127.0.0.1:4000", port: 4000, workspace });
    },
  };
}

test("toggle starts the hosted IDE bound to the workspace root", async () => {
  const { bridge, calls } = fakeBridge();
  const ide = new MarsIdeSession(bridge);
  await ide.bindScope("s1|/ws/a");
  await ide.toggle("/ws/a");
  // bindScope from the initial empty scope releases once (idempotent
  // stop on a host with nothing running) before the start.
  assert.deepEqual(calls, ["stop", "start:/ws/a"]);
  assert.equal(ide.info?.workspace, "/ws/a");
  assert.equal(ide.busy, false);
});

test("toggle again stops the running instance", async () => {
  const { bridge, calls } = fakeBridge();
  const ide = new MarsIdeSession(bridge);
  await ide.toggle("/ws/a");
  await ide.toggle("/ws/a");
  assert.deepEqual(calls, ["start:/ws/a", "stop"]);
  assert.equal(ide.info, null);
});

test("scope change while start is in flight drops the late binding", async () => {
  // The user leaves session/workspace mid-start: bindScope releases the
  // host-side instance, and when the start finally resolves its info is
  // discarded instead of being shown under the new scope.
  const { bridge, calls, resolveStart } = fakeBridge({ holdStart: true });
  const ide = new MarsIdeSession(bridge);
  await ide.bindScope("s1|/ws/a");
  const pending = ide.toggle("/ws/a");
  const rebinding = ide.bindScope("s2|/ws/b");
  await rebinding;
  resolveStart("/ws/a");
  await pending;
  assert.equal(ide.info, null);
  // stop ran for the release plus once more for the stale resolution —
  // idempotent on the host, and no start is left unaccounted.
  assert.ok(calls.filter((c) => c === "stop").length >= 1);
  assert.ok(calls.includes("start:/ws/a"));
});

test("release mid-start invalidates the in-flight ticket", async () => {
  const { bridge, calls, resolveStart } = fakeBridge({ holdStart: true });
  const ide = new MarsIdeSession(bridge);
  await ide.bindScope("s1|/ws/a");
  calls.length = 0;
  const pending = ide.toggle("/ws/a");
  await ide.release();
  resolveStart("/ws/a");
  await pending;
  assert.equal(ide.info, null);
  assert.ok(calls.includes("stop"));
});

test("A→B→A rebinding does not re-accept the stale A start", async () => {
  const { bridge, calls, resolveStart } = fakeBridge({ holdStart: true });
  const ide = new MarsIdeSession(bridge);
  await ide.bindScope("s1|/ws/a");
  calls.length = 0;
  const pending = ide.toggle("/ws/a");
  await ide.bindScope("s2|/ws/b");
  await ide.bindScope("s1|/ws/a"); // same scope string as the ticket
  resolveStart("/ws/a");
  await pending;
  assert.equal(ide.info, null);
  assert.ok(calls.includes("stop"));
});

test("release on unmount kills the instance and forgets info", async () => {
  const { bridge, calls } = fakeBridge();
  const ide = new MarsIdeSession(bridge);
  await ide.toggle("/ws/a");
  assert.ok(ide.info);
  await ide.release();
  assert.equal(ide.info, null);
  assert.equal(calls.at(-1), "stop");
});

test("start failure surfaces to the caller and leaves no instance", async () => {
  const failing: IdeBridgeLike = {
    start: () => Promise.reject(new Error("code-server did not report a listen port within 15s")),
    stop: () => Promise.resolve(),
  };
  const ide = new MarsIdeSession(failing);
  await assert.rejects(() => ide.toggle("/ws/a"), /listen port/);
  assert.equal(ide.info, null);
  assert.equal(ide.busy, false);
});
