import { test } from "node:test";
import assert from "node:assert/strict";
import { MarsIdeSession, type IdeBridgeLike } from "../src/workspaces/mars/ideSession";
import type { IdeInfo } from "../src/service/tauriBridge";

const tick = () => new Promise<void>((r) => setImmediate(r));

function fakeBridge(opts: { holdStart?: boolean } = {}) {
  const calls: string[] = [];
  const pending: Array<(info: IdeInfo) => void> = [];
  let failWith: string | null = null;
  const bridge: IdeBridgeLike = {
    start: (workspace: string) => {
      calls.push(`start:${workspace}`);
      if (failWith) return Promise.reject(new Error(failWith));
      if (!opts.holdStart) {
        return Promise.resolve({ url: "http://127.0.0.1:4000", port: 4000, workspace });
      }
      return new Promise<IdeInfo>((res) => { pending.push(res); });
    },
    stop: () => {
      calls.push("stop");
      return Promise.resolve();
    },
  };
  return {
    bridge,
    calls,
    failWith: (msg: string | null) => { failWith = msg; },
    /** Resolve the oldest in-flight start as `workspace`'s info. Waits
     *  until a start has actually been issued. */
    async resolveNext(workspace: string) {
      while (!pending.length) await tick();
      pending.shift()!({ url: "http://127.0.0.1:4000", port: 4000, workspace });
    },
  };
}

test("a bound scope auto-starts the hosted IDE — always-on, no toggle", async () => {
  const { bridge, calls } = fakeBridge();
  const ide = new MarsIdeSession(bridge);
  await ide.bindScope("s1|/ws/a", "/ws/a");
  // bindScope from the initial empty scope releases once (idempotent
  // stop on a host with nothing running) before the auto-start.
  assert.deepEqual(calls, ["stop", "start:/ws/a"]);
  assert.equal(ide.info?.workspace, "/ws/a");
  assert.equal(ide.busy, false);
  assert.equal(ide.error, null);
});

test("a scope with no workspace releases the instance and starts nothing", async () => {
  const { bridge, calls } = fakeBridge();
  const ide = new MarsIdeSession(bridge);
  await ide.bindScope("s1|/ws/a", "/ws/a");
  assert.ok(ide.info);
  calls.length = 0;
  await ide.bindScope("s2|", "");
  assert.deepEqual(calls, ["stop"]);
  assert.equal(ide.info, null);
  assert.equal(ide.busy, false);
});

test("rebinding A→B restarts on the new workspace", async () => {
  const { bridge, calls } = fakeBridge();
  const ide = new MarsIdeSession(bridge);
  await ide.bindScope("s1|/ws/a", "/ws/a");
  calls.length = 0;
  await ide.bindScope("s1|/ws/b", "/ws/b"); // same session, rebound root
  assert.deepEqual(calls, ["stop", "start:/ws/b"]);
  assert.equal(ide.info?.workspace, "/ws/b");
});

test("a bind superseded during cleanup never issues a start", async () => {
  const { bridge, calls, resolveNext } = fakeBridge({ holdStart: true });
  const ide = new MarsIdeSession(bridge);
  const startA = ide.bindScope("s1|/ws/a", "/ws/a");
  const startB = ide.bindScope("s2|/ws/b", "/ws/b");
  await startA;
  // A's ticket was stale before its cleanup finished — no spawn at all.
  assert.ok(!calls.includes("start:/ws/a"));
  await resolveNext("/ws/b");
  await startB;
  assert.equal(ide.info?.workspace, "/ws/b");
});

test("a late start resolution under a stale ticket is dropped", async () => {
  const { bridge, calls, resolveNext } = fakeBridge({ holdStart: true });
  const ide = new MarsIdeSession(bridge);
  const startA = ide.bindScope("s1|/ws/a", "/ws/a");
  await tick(); // A's cleanup done — start(/ws/a) issued and held
  const startB = ide.bindScope("s2|/ws/b", "/ws/b");
  await tick(); // B's cleanup done — start(/ws/b) issued and held
  await resolveNext("/ws/a"); // resolves late — stale ticket, dropped
  await resolveNext("/ws/b");
  await startA;
  await startB;
  assert.equal(ide.info?.workspace, "/ws/b");
  // stops: A's cleanup, B's cleanup, the belt-and-braces stale-A kill.
  assert.ok(calls.filter((c) => c === "stop").length >= 3);
});

test("release mid-start invalidates the in-flight ticket", async () => {
  const { bridge, calls, resolveNext } = fakeBridge({ holdStart: true });
  const ide = new MarsIdeSession(bridge);
  const pending = ide.bindScope("s1|/ws/a", "/ws/a");
  await tick(); // start issued and held
  const releasing = ide.release();
  await resolveNext("/ws/a");
  await pending;
  await releasing;
  assert.equal(ide.info, null);
  assert.ok(calls.filter((c) => c === "stop").length >= 2);
});

test("A→B→A rebinding does not re-accept the stale A start", async () => {
  const { bridge, calls, resolveNext } = fakeBridge({ holdStart: true });
  const ide = new MarsIdeSession(bridge);
  const startA1 = ide.bindScope("s1|/ws/a", "/ws/a");
  await tick();
  const startB = ide.bindScope("s2|/ws/b", "/ws/b");
  await tick();
  const startA2 = ide.bindScope("s1|/ws/a", "/ws/a"); // same scope string as the first ticket
  await tick();
  await resolveNext("/ws/a"); // the stale first A — dropped
  await resolveNext("/ws/b"); // the interrupted B — dropped
  await resolveNext("/ws/a"); // the live rebound A — accepted
  await startA1;
  await startB;
  await startA2;
  assert.equal(ide.info?.workspace, "/ws/a");
  assert.ok(calls.filter((c) => c === "stop").length >= 3);
});

test("release on unmount kills the instance and forgets info", async () => {
  const { bridge, calls } = fakeBridge();
  const ide = new MarsIdeSession(bridge);
  await ide.bindScope("s1|/ws/a", "/ws/a");
  assert.ok(ide.info);
  await ide.release();
  assert.equal(ide.info, null);
  assert.equal(calls.at(-1), "stop");
});

test("start failure records the error and leaves no instance", async () => {
  const { bridge, calls, failWith } = fakeBridge();
  failWith("listen port timeout");
  const ide = new MarsIdeSession(bridge);
  await ide.bindScope("s1|/ws/a", "/ws/a");
  assert.equal(ide.info, null);
  assert.equal(ide.busy, false);
  assert.equal(ide.error, "listen port timeout");
  assert.ok(calls.includes("start:/ws/a"));
});

test("a failed scope retries the auto-start on rebind", async () => {
  const { bridge, calls, failWith } = fakeBridge();
  failWith("listen port timeout");
  const ide = new MarsIdeSession(bridge);
  await ide.bindScope("s1|/ws/a", "/ws/a");
  failWith(null);
  // Same scope re-bound after the failure retries the auto-start.
  await ide.bindScope("s1|/ws/a", "/ws/a");
  assert.equal(ide.error, null);
  assert.equal(ide.info?.workspace, "/ws/a");
  assert.deepEqual(calls.filter((c) => c === "start:/ws/a").length, 2);
});
