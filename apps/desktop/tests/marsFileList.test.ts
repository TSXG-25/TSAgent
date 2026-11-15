import { test } from "node:test";
import assert from "node:assert/strict";
import { WorkspaceFileList } from "../src/workspaces/mars/fileListSession";

const f = (path: string) => ({ path, size: 1, modifiedAt: "2026-01-01T00:00:00Z" });

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

test("same-session rebind refetches under the new workspace scope", async () => {
  const calls: string[] = [];
  const list = new WorkspaceFileList(async (sid) => {
    calls.push(sid);
    return [f(`file-for-${calls.length}`)];
  });
  await list.bindScope("s1|/ws/a", "s1");
  assert.equal(list.files[0].path, "file-for-1");
  await list.bindScope("s1|/ws/b", "s1"); // same session, rebound root
  assert.equal(list.files[0].path, "file-for-2");
  assert.deepEqual(calls, ["s1", "s1"]);
});

test("a late response for the old root cannot land on the new scope", async () => {
  const dA = deferred<ReturnType<typeof f>[]>();
  const dB = deferred<ReturnType<typeof f>[]>();
  const pending = [dA, dB];
  const list = new WorkspaceFileList(async () => pending.shift()!.promise);
  const loadA = list.bindScope("s1|/ws/a", "s1");
  const loadB = list.bindScope("s1|/ws/b", "s1");
  dB.resolve([f("b.txt")]);
  await loadB;
  assert.equal(list.files[0].path, "b.txt");
  dA.resolve([f("a.txt")]); // A resolves late — must be dropped
  await loadA;
  assert.equal(list.files[0].path, "b.txt");
  assert.equal(list.error, null);
});

test("resolution completing retries a previously failed load", async () => {
  let fail = true;
  const list = new WorkspaceFileList(async () => {
    if (fail) throw new Error("backend not ready");
    return [f("ok.txt")];
  });
  await list.bindScope("s1|", "s1");
  assert.equal(list.error, "backend not ready");
  fail = false;
  // workspacePath arrives ("" → "/ws/a") — scope change retries and heals.
  await list.bindScope("s1|/ws/a", "s1");
  assert.equal(list.error, null);
  assert.equal(list.files[0].path, "ok.txt");
});

test("rebinding the same live scope is a no-op", async () => {
  let calls = 0;
  const list = new WorkspaceFileList(async () => { calls++; return [f("x")]; });
  await list.bindScope("s1|/ws/a", "s1");
  await list.bindScope("s1|/ws/a", "s1");
  assert.equal(calls, 1);
});
