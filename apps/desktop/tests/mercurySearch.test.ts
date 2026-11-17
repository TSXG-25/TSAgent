import { test } from "vitest";
import assert from "node:assert/strict";
import { MercurySearchSession, type MercurySearchResult } from "../src/workspaces/mercury/searchSession";

/** A26: Mercury search request ownership — delayed-provider probes. */

function deferred() {
  let resolve!: (v: MercurySearchResult) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<MercurySearchResult>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

const src = (n: number) => ({ title: `s${n}`, url: `https://x/${n}` });

function paneTitle(session: MercurySearchSession, i: number): unknown {
  const source = session.panes[i].source;
  assert.ok(source !== null, `pane ${i} should hold a source`);
  return source.title;
}

test("stop invalidates the in-flight response", async () => {
  const calls: Array<ReturnType<typeof deferred>> = [];
  const session = new MercurySearchSession(() => {
    const d = deferred();
    calls.push(d);
    return d.promise;
  });
  session.panes = Array.from({ length: 4 }, () => ({ source: null, content: null }));

  const first = session.run("old query");
  assert.equal(session.running, true);
  session.stop();
  assert.equal(session.running, false);

  // 旧响应晚到 — 不得更新视图，也不得清掉新请求的运行状态。
  const second = session.run("new query");
  assert.equal(session.running, true);
  calls[0].resolve({ sources: [src(1)], provider: "p", content: "stale" });
  assert.equal(await first, false);
  assert.equal(session.running, true);          // 新请求不受影响
  assert.equal(session.panes[0].source, null);  // 旧结果未写入

  calls[1].resolve({ sources: [src(9)], provider: "p", content: "new" });
  assert.equal(await second, true);
  assert.equal(paneTitle(session, 0), "s9");
});

test("older response never overwrites a newer search", async () => {
  const calls: Array<ReturnType<typeof deferred>> = [];
  const session = new MercurySearchSession(() => {
    const d = deferred();
    calls.push(d);
    return d.promise;
  });

  const first = session.run("q1");
  const second = session.run("q2");
  // 乱序返回 — 先回新请求。
  calls[1].resolve({ sources: [src(2)], provider: "p", content: "two" });
  assert.equal(await second, true);
  assert.equal(paneTitle(session, 0), "s2");

  calls[0].resolve({ sources: [src(1)], provider: "p", content: "one" });
  assert.equal(await first, false);             // 被丢弃
  assert.equal(paneTitle(session, 0), "s2");
});

test("fewer sources clear the extra panes", async () => {
  const calls: Array<ReturnType<typeof deferred>> = [];
  const session = new MercurySearchSession(() => {
    const d = deferred();
    calls.push(d);
    return d.promise;
  });
  session.panes = Array.from({ length: 4 }, () => ({ source: null, content: null }));

  const first = session.run("q1");
  calls[0].resolve({ sources: [src(1), src(2), src(3), src(4)], provider: "p", content: "x" });
  await first;
  assert.equal(session.panes.filter((p) => p.source).length, 4);

  const second = session.run("q2");
  calls[1].resolve({ sources: [src(9)], provider: "p", content: "y" });
  await second;
  assert.equal(session.panes.filter((p) => p.source).length, 1);
  assert.equal(session.panes[1].source, null);
  assert.equal(session.panes[3].source, null);
});

test("empty and unavailable outcomes are distinct", async () => {
  const calls: Array<ReturnType<typeof deferred>> = [];
  const session = new MercurySearchSession(() => {
    const d = deferred();
    calls.push(d);
    return d.promise;
  });

  const first = session.run("q1");
  calls[0].resolve({ sources: [], provider: "p", content: "" });
  assert.equal(await first, true);
  assert.equal(session.status, "empty");

  const second = session.run("q2");
  calls[1].reject(new Error("RESEARCH_TOOL_UNAVAILABLE: duckduckgo-mcp-server is not installed"));
  assert.equal(await second, true);
  assert.equal(session.status, "unavailable");
  assert.match(session.err ?? "", /not installed/);
});


test("session switch drops the previous session's response", async () => {
  const calls: Array<ReturnType<typeof deferred>> = [];
  const session = new MercurySearchSession(() => {
    const d = deferred();
    calls.push(d);
    return d.promise;
  });
  session.bind("session-a");
  const first = session.run("q");
  assert.equal(session.sessionId, "session-a");

  session.bind("session-b");            // 切到 B — A 的在途响应失效
  calls[0].resolve({ sources: [src(1)], provider: "p", content: "a" });
  assert.equal(await first, false);     // 结果面板和日志都不更新
  assert.equal(session.panes[0].source, null);
  assert.equal(session.sourceCount, 0);
});
