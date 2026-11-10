import assert from "node:assert/strict";
import test from "node:test";
import type { WorkspaceFileContent, WorkspaceFileRequest } from "../src/types/service";
import { ForgeDocumentSession } from "../src/workspaces/mars/documentSession";

/** Delayed-promise fake with a mutable session→workspace binding, like the
 *  real Service where the session head can be re-pointed at another root. */
class FakeDocumentService {
  files = new Map<string, string>();
  private bound = new Map<string, string>();
  private pendingReads = new Map<string, Array<(c: WorkspaceFileContent) => void>>();
  private pendingWrites: Array<{
    request: WorkspaceFileRequest;
    resolve: (c: WorkspaceFileContent) => void;
    reject: (e: unknown) => void;
  }> = [];

  bind(sessionId: string, workspaceRoot: string) {
    this.bound.set(sessionId, workspaceRoot);
  }

  get pendingWriteCount() {
    return this.pendingWrites.length;
  }

  private rootFor(request: WorkspaceFileRequest) {
    return this.bound.get(request.sessionId) ?? request.sessionId;
  }

  readWorkspaceFile(request: WorkspaceFileRequest): Promise<WorkspaceFileContent> {
    return new Promise((resolve) => {
      const key = `${request.sessionId}:${request.path}`;
      const list = this.pendingReads.get(key) ?? [];
      list.push(resolve);
      this.pendingReads.set(key, list);
    });
  }

  writeWorkspaceFile(request: WorkspaceFileRequest): Promise<WorkspaceFileContent> {
    return new Promise((resolve, reject) => this.pendingWrites.push({ request, resolve, reject }));
  }

  resolveRead(sessionId: string, path: string) {
    const key = `${sessionId}:${path}`;
    const resolve = this.pendingReads.get(key)?.shift();
    assert.ok(resolve, `no pending read for ${key}`);
    const root = this.bound.get(sessionId) ?? sessionId;
    const text = this.files.get(`${root}:${path}`) ?? "";
    resolve({ path, text, digest: `d-${text}`, redacted: false, workspaceRoot: root });
  }

  resolveNextWrite() {
    const pending = this.pendingWrites.shift();
    assert.ok(pending, "no pending write");
    const { request } = pending;
    const root = this.rootFor(request);
    if (request.workspaceRoot && request.workspaceRoot !== root) {
      pending.reject(new Error(`WORKSPACE_CHANGED: document not bound to ${root}`));
      return;
    }
    this.files.set(`${root}:${request.path}`, request.content ?? "");
    pending.resolve({ path: request.path, text: "saved", digest: `d-${request.content}`, redacted: false, workspaceRoot: root });
  }
}

test("late read for a superseded selection cannot overwrite the current document", async () => {
  const svc = new FakeDocumentService();
  svc.bind("s1", "w1");
  svc.files.set("w1:a.txt", "AAA");
  svc.files.set("w1:b.txt", "BBB");
  const session = new ForgeDocumentSession(svc, () => ({ tenantId: "t", sessionId: "s1" }));

  const openA = session.open("a.txt");
  const openB = session.open("b.txt");
  svc.resolveRead("s1", "b.txt");
  await openB;
  assert.equal(session.doc?.path, "b.txt");
  assert.equal(session.content, "BBB");

  svc.resolveRead("s1", "a.txt");
  await openA;
  assert.equal(session.doc?.path, "b.txt");
  assert.equal(session.content, "BBB");
});

test("cancelled switch keeps the dirty draft; confirm discards and loads next", async () => {
  const svc = new FakeDocumentService();
  svc.bind("s1", "w1");
  svc.files.set("w1:a.txt", "AAA");
  svc.files.set("w1:b.txt", "BBB");
  const session = new ForgeDocumentSession(svc, () => ({ tenantId: "t", sessionId: "s1" }));

  const open = session.open("a.txt");
  svc.resolveRead("s1", "a.txt");
  await open;
  session.edit("draft edits");
  assert.ok(session.dirty);

  assert.equal(await session.open("b.txt"), false);
  assert.equal(session.doc?.path, "a.txt");
  assert.equal(session.content, "draft edits");

  const confirmed = session.open("b.txt", { discardDraft: true });
  svc.resolveRead("s1", "b.txt");
  assert.equal(await confirmed, true);
  assert.equal(session.doc?.path, "b.txt");
  assert.equal(session.dirty, false);
});

test("same-session workspace rebind rejects the stale write", async () => {
  const svc = new FakeDocumentService();
  // 同名同内容的两个文件，只有绑定的工作区不同。
  svc.files.set("w1:same.txt", "identical");
  svc.files.set("w2:same.txt", "identical");
  svc.bind("s1", "w1");
  const session = new ForgeDocumentSession(svc, () => ({ tenantId: "t", sessionId: "s1" }));

  const open = session.open("same.txt");
  svc.resolveRead("s1", "same.txt");
  await open;
  assert.equal(session.doc?.workspaceRoot, "w1");
  session.edit("local edits");

  svc.bind("s1", "w2");
  const saving = session.save();
  assert.equal(svc.pendingWriteCount, 1);
  svc.resolveNextWrite();
  await assert.rejects(saving, /WORKSPACE_CHANGED/);
  assert.equal(svc.files.get("w2:same.txt"), "identical");
  assert.equal(session.content, "local edits");
});

test("edits during save stay dirty and keep the new text", async () => {
  const svc = new FakeDocumentService();
  svc.bind("s1", "w1");
  svc.files.set("w1:a.txt", "v1");
  const session = new ForgeDocumentSession(svc, () => ({ tenantId: "t", sessionId: "s1" }));

  const open = session.open("a.txt");
  svc.resolveRead("s1", "a.txt");
  await open;
  session.edit("v2");
  const saving = session.save();
  session.edit("v3 typed while saving");
  svc.resolveNextWrite();
  await saving;

  assert.equal(session.doc?.digest, "d-v2");
  assert.equal(session.dirty, true);
  assert.equal(session.content, "v3 typed while saving");
});
