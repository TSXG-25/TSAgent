import assert from "node:assert/strict";
import test from "node:test";
import type { WorkspaceFileContent, WorkspaceFileRequest } from "../src/types/service";
import { ForgeDocumentSession } from "../src/workspaces/mars/documentSession";

/** Delayed-promise fake: reads/writes are resolved explicitly by the test. */
class FakeDocumentService {
  files = new Map<string, string>();
  writes: WorkspaceFileRequest[] = [];
  private pendingReads = new Map<string, Array<(c: WorkspaceFileContent) => void>>();
  private pendingWrites: Array<{ request: WorkspaceFileRequest; resolve: (c: WorkspaceFileContent) => void }> = [];

  readWorkspaceFile(request: WorkspaceFileRequest): Promise<WorkspaceFileContent> {
    return new Promise((resolve) => {
      const key = `${request.sessionId}:${request.path}`;
      const list = this.pendingReads.get(key) ?? [];
      list.push(resolve);
      this.pendingReads.set(key, list);
    });
  }

  writeWorkspaceFile(request: WorkspaceFileRequest): Promise<WorkspaceFileContent> {
    return new Promise((resolve) => this.pendingWrites.push({ request, resolve }));
  }

  resolveRead(sessionId: string, path: string) {
    const key = `${sessionId}:${path}`;
    const resolve = this.pendingReads.get(key)?.shift();
    assert.ok(resolve, `no pending read for ${key}`);
    const text = this.files.get(key) ?? "";
    resolve({ path, text, digest: `d-${text}`, redacted: false });
  }

  resolveNextWrite() {
    const pending = this.pendingWrites.shift();
    assert.ok(pending, "no pending write");
    const key = `${pending.request.sessionId}:${pending.request.path}`;
    this.files.set(key, pending.request.content ?? "");
    pending.resolve({ path: pending.request.path, text: "saved", digest: `d-${pending.request.content}`, redacted: false });
  }
}

function sessionFor(service: FakeDocumentService, getId: () => { tenantId: string; sessionId: string }) {
  return new ForgeDocumentSession(service, getId);
}

test("late read for a superseded selection cannot overwrite the current document", async () => {
  const svc = new FakeDocumentService();
  svc.files.set("s1:a.txt", "AAA");
  svc.files.set("s1:b.txt", "BBB");
  const session = sessionFor(svc, () => ({ tenantId: "t", sessionId: "s1" }));

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
  svc.files.set("s1:a.txt", "AAA");
  svc.files.set("s1:b.txt", "BBB");
  const session = sessionFor(svc, () => ({ tenantId: "t", sessionId: "s1" }));

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

test("save is refused when the bound workspace is no longer active", async () => {
  const svc = new FakeDocumentService();
  // Same name + same content in both workspaces — only identity differs.
  svc.files.set("w1:same.txt", "identical");
  svc.files.set("w2:same.txt", "identical");
  let current = { tenantId: "t", sessionId: "w1" };
  const session = sessionFor(svc, () => current);

  const open = session.open("same.txt");
  svc.resolveRead("w1", "same.txt");
  await open;
  session.edit("local edits");

  current = { tenantId: "t", sessionId: "w2" };
  await assert.rejects(session.save(), /WORKSPACE_CHANGED/);
  assert.equal(svc.writes.length, 0);
  assert.equal(svc.files.get("w2:same.txt"), "identical");
  assert.equal(session.doc?.identity.sessionId, "w1");
});

test("edits during save stay dirty and keep the new text", async () => {
  const svc = new FakeDocumentService();
  svc.files.set("s1:a.txt", "v1");
  const session = sessionFor(svc, () => ({ tenantId: "t", sessionId: "s1" }));

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
