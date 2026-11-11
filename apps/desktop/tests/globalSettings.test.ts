import { test } from "node:test";
import assert from "node:assert/strict";
import { resolveProviderSelection } from "../src/app/GlobalSettings";

/** A13: 表单回归 — 保存值 ↔ 重开选择使用后端词表与保存字段。 */

const base = {
  providerMode: "ollama",
  activeProvider: "ollama",
  activeModel: "phi4",
  deepseekModel: "deepseek-chat",
  ollamaModel: "phi4",
};

test("non-first ollama model reopens to itself", () => {
  const sel = resolveProviderSelection(base as never);
  assert.equal(sel.mode, "ollama");          // 后端词表，不是 "local"
  assert.equal(sel.providerId, "ollama");
  assert.equal(sel.modelSel, "phi4");        // 非首项模型恢复，不跳回首项
});

test("saved deepseek mode restores deepseek model", () => {
  const sel = resolveProviderSelection({
    ...base, providerMode: "deepseek",
    deepseekModel: "deepseek-reasoner",
  } as never);
  assert.equal(sel.mode, "deepseek");
  assert.equal(sel.modelSel, "deepseek-reasoner");
});
