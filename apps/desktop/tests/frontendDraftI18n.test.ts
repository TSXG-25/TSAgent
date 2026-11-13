import { test } from "node:test";
import assert from "node:assert/strict";

/** FE-01: 草稿契约（tenant/session/target 键、sessionStorage、受理后清空、
 *  同键共享）与 i18n 默认/持久化/回退的离线回归。 */

// minimal storage shim — Node runner has no DOM storage
const makeStorage = () => {
  const m = new Map<string, string>();
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), removeItem: (k: string) => void m.delete(k) };
};
(globalThis as Record<string, unknown>).sessionStorage = makeStorage();
(globalThis as Record<string, unknown>).localStorage = makeStorage();

const { draftKey, loadDraft, saveDraft, clearDraft } = await import("../src/app/draftStore");
const { getLang, setLang, t, tk } = await import("../src/app/i18n");

test("draftKey scopes by tenant, session and target", () => {
  const a = draftKey("t1", "s1", "sun");
  assert.notEqual(a, draftKey("t1", "s2", "sun"));
  assert.notEqual(a, draftKey("t1", "s1", "mars"));
  assert.notEqual(a, draftKey("t2", "s1", "sun"));
});

test("draft persists text + attachment refs in sessionStorage", () => {
  const key = draftKey("t1", "s-a", "sun");
  saveDraft(key, { text: "hello", files: ["file:/tmp/a.py"] });
  const again = loadDraft(key);             // fresh read — hits storage
  assert.equal(again.text, "hello");
  assert.deepEqual(again.files, ["file:/tmp/a.py"]);
});

test("clearDraft removes the stored draft — the post-accept path", () => {
  const key = draftKey("t1", "s-b", "sun");
  saveDraft(key, { text: "wip", files: [] });
  clearDraft(key);
  assert.equal(loadDraft(key).text, "");
  assert.equal(sessionStorage.getItem(`tsagent:draft:${key}`), null);
});

test("drafts of different targets stay isolated", () => {
  saveDraft(draftKey("t1", "s-c", "sun"), { text: "sun-draft", files: [] });
  saveDraft(draftKey("t1", "s-c", "mercury"), { text: "merc-draft", files: [] });
  assert.equal(loadDraft(draftKey("t1", "s-c", "sun")).text, "sun-draft");
  assert.equal(loadDraft(draftKey("t1", "s-c", "mercury")).text, "merc-draft");
});

test("i18n defaults to english on non-Chinese systems and persists choice", () => {
  Object.defineProperty(globalThis, "navigator", { value: { language: "en-US" }, configurable: true });
  assert.equal(getLang(), "en");
  setLang("zh");
  assert.equal(getLang(), "zh");
  Object.defineProperty(globalThis, "navigator", { value: { language: "zh-CN" }, configurable: true });
  localStorage.removeItem("tsagent:lang");
  assert.equal(getLang(), "zh");
});

test("t() interpolates params; tk() returns undefined for unknown codes", () => {
  setLang("zh");
  assert.equal(t("composer.delegated", { target: "mars" }), "已委派 → mars");
  assert.equal(tk("err.MADE_UP"), undefined);
  assert.equal(tk("err.INVALID_REQUEST"), "请求无效");
});
