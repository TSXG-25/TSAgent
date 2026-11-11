import type { SettingsSummary } from "../types/service";

/** Provider/model form state — one controller owns the vocabulary
 *  (backend provider_mode: deepseek | ollama) so select/edit events
 *  always write the correct saved field. */

export const PROVIDERS = [
  { id: "deepseek", label: "DeepSeek", mode: "deepseek", models: ["deepseek-chat", "deepseek-reasoner"], base: "https://api.deepseek.com/v1" },
  { id: "ollama", label: "Ollama (本地)", mode: "ollama", models: ["llama3.1", "qwen2.5", "mistral", "phi4"], base: "http://localhost:11434" },
];

export class SettingsForm {
  providerId = "deepseek";
  mode = "deepseek";
  apiBase = PROVIDERS[0].base;
  deepseek = "";
  ollama = "";
  modelSel = "";

  /** Reopen mapping — restores the saved provider + model. */
  load(s: SettingsSummary): void {
    const savedModel = s.providerMode === "deepseek" ? s.deepseekModel : s.ollamaModel;
    const p = PROVIDERS.find((x) => x.mode === s.providerMode) ?? PROVIDERS[0];
    this.providerId = p.id;
    this.mode = p.mode;
    this.apiBase = p.base;
    this.deepseek = s.deepseekModel;
    this.ollama = s.ollamaModel;
    this.modelSel = p.models.includes(savedModel) ? savedModel : "__custom__";
  }

  selectProvider(id: string): void {
    const p = PROVIDERS.find((x) => x.id === id);
    if (!p) return;
    this.providerId = p.id;
    this.mode = p.mode;
    this.apiBase = p.base;
    const m = p.models[0] ?? "";
    this.modelSel = p.models.length ? m : "__custom__";
    if (p.mode === "deepseek") this.deepseek = m;
    else this.ollama = m;
  }

  selectModel(v: string): void {
    this.modelSel = v;
    if (v === "__custom__") return;
    if (this.mode === "deepseek") this.deepseek = v;
    else this.ollama = v;
  }

  editCustomModel(v: string): void {
    if (this.mode === "deepseek") this.deepseek = v;
    else this.ollama = v;
  }

  toSaveRequest() {
    return {
      providerMode: this.mode,
      deepseekModel: this.deepseek,
      ollamaModel: this.ollama,
    };
  }
}
