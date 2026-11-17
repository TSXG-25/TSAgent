import { test } from "vitest";
import assert from "node:assert/strict";
import { SettingsForm } from "../src/app/settingsForm";

/** A13: 表单回归 — 保存值 ↔ 重开选择使用后端词表与保存字段。 */

const base = {
  providerMode: "ollama",
  activeProvider: "ollama",
  activeModel: "phi4",
  deepseekModel: "deepseek-chat",
  ollamaModel: "phi4",
};

test("non-first ollama model reopens to itself", () => {
  const form = new SettingsForm();
  form.load(base as never);
  assert.equal(form.mode, "ollama");          // 后端词表，不是 "local"
  assert.equal(form.providerId, "ollama");
  assert.equal(form.modelSel, "phi4");        // 非首项模型恢复，不跳回首项
});

test("saved deepseek mode restores deepseek model", () => {
  const form = new SettingsForm();
  form.load({ ...base, providerMode: "deepseek", deepseekModel: "deepseek-reasoner" } as never);
  assert.equal(form.mode, "deepseek");
  assert.equal(form.modelSel, "deepseek-reasoner");
});

test("selecting deepseek non-first model writes deepseekModel, preserves ollamaModel", () => {
  const form = new SettingsForm();
  form.load(base as never);                    // 已保存 ollamaModel=phi4
  form.selectProvider("deepseek");             // Provider 事件
  form.selectModel("deepseek-reasoner");       // 模型事件 — 非首项
  const req = form.toSaveRequest();
  assert.equal(req.providerMode, "deepseek");
  assert.equal(req.deepseekModel, "deepseek-reasoner"); // 写入 deepseek 字段
  assert.equal(req.ollamaModel, "phi4");                // ollama 原值保留
});

test("custom model edit goes to the active provider's field", () => {
  const form = new SettingsForm();
  form.load(base as never);
  form.selectModel("__custom__");
  form.editCustomModel("qwen3:8b");
  const req = form.toSaveRequest();
  assert.equal(req.ollamaModel, "qwen3:8b");
  assert.equal(req.deepseekModel, "deepseek-chat");
});
