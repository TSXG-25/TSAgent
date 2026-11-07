import { useCallback, useEffect, useState } from "react";
import type { CapabilitySummary, DesktopAgentServiceClient, ProfileSummary, SettingsSummary, SkillSummary, WorkspaceSummary } from "../types/service";
import type { RunView } from "../types";
import { Icon, requestIdForRun } from "./ui";

/** Global Settings drawer — §4.2. Reachable from every workspace via the
 *  topbar gear; provider/model fields are backed by getSettings/updateSettings,
 *  other sections show their partition contract with a not-installed state. */
const PROVIDERS = [
  { id: "deepseek", label: "DeepSeek", mode: "api", models: ["deepseek-chat", "deepseek-reasoner"], base: "https://api.deepseek.com/v1" },
  { id: "openai", label: "OpenAI", mode: "api", models: ["gpt-4o", "gpt-4o-mini", "o3-mini"], base: "https://api.openai.com/v1" },
  { id: "anthropic", label: "Anthropic", mode: "api", models: ["claude-sonnet-4", "claude-haiku-3.5"], base: "https://api.anthropic.com" },
  { id: "qwen", label: "通义千问", mode: "api", models: ["qwen-max", "qwen-plus", "qwen-turbo"], base: "https://dashscope.aliyuncs.com/compatible-mode/v1" },
  { id: "custom", label: "其他 OpenAI 兼容", mode: "api", models: [], base: "" },
  { id: "ollama", label: "Ollama (本地)", mode: "local", models: ["llama3.1", "qwen2.5", "mistral", "phi4"], base: "http://localhost:11434" },
];

export default function GlobalSettings({ service, onClose, runs = [] }: { service: DesktopAgentServiceClient; onClose: () => void; runs?: RunView[] }) {
  const [settings, setSettings] = useState<SettingsSummary | null>(null);
  const [mode, setMode] = useState("api");
  const [deepseek, setDeepseek] = useState("");
  const [ollama, setOllama] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [providerId, setProviderId] = useState("deepseek");
  const [modelSel, setModelSel] = useState("deepseek-chat");
  const [apiBase, setApiBase] = useState("https://api.deepseek.com/v1");
  const [error, setError] = useState<string | null>(null);
  const [profiles, setProfiles] = useState<ProfileSummary[]>([]);
  const [workspaces, setWorkspaces] = useState<WorkspaceSummary[]>([]);
  const [capabilities, setCapabilities] = useState<CapabilitySummary[]>([]);
  const [skills, setSkills] = useState<SkillSummary[]>([]);
  const [usage, setUsage] = useState<{ runId: string; tokens: Record<string, number> }[]>([]);

  useEffect(() => {
    let stale = false;
    service.getSettings({
      tenantId: service.identity.tenantId,
      sessionId: service.identity.sessionId,
      requestId: requestIdForRun(0),
    }).then((s) => {
      if (stale) return;
      setSettings(s); setMode(s.providerMode); setDeepseek(s.deepseekModel); setOllama(s.ollamaModel);
      const p = PROVIDERS.find((x) => x.models.includes(s.providerMode === "api" ? s.deepseekModel : s.ollamaModel) && x.mode === s.providerMode) ?? PROVIDERS[0];
      setProviderId(p.id); setApiBase(p.base); setModelSel(p.models[0] ?? "__custom__");
    }).catch((e) => setError(String(e)));
    service.listProfiles({
      tenantId: service.identity.tenantId,
      sessionId: service.identity.sessionId,
      requestId: requestIdForRun(0),
    }).then((p) => { if (!stale) setProfiles(p); }).catch(() => {});
    service.listCapabilities({
      tenantId: service.identity.tenantId,
      sessionId: service.identity.sessionId,
      requestId: requestIdForRun(0),
    }).then((c) => { if (!stale) setCapabilities(c); }).catch(() => {});
    service.listSkills({
      tenantId: service.identity.tenantId,
      sessionId: service.identity.sessionId,
      requestId: requestIdForRun(0),
    }).then((s) => { if (!stale) setSkills(s); }).catch(() => {});
    service.listWorkspaces({
      tenantId: service.identity.tenantId,
      sessionId: service.identity.sessionId,
      requestId: requestIdForRun(0),
    }).then((w) => { if (!stale) setWorkspaces(w); }).catch(() => {});
    return () => { stale = true; };
  }, [service]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    const rows = runs.map((r) => {
      const ev = [...(r.events ?? [])].reverse().find((e) => e.type === "token_usage" || (e as { eventType?: string }).eventType === "token_usage");
      if (!ev) return null;
      const payload = (ev as { payload?: Record<string, unknown> }).payload ?? (ev as { data?: Record<string, unknown> }).data ?? {};
      return { runId: r.runId, tokens: payload as Record<string, number> };
    }).filter(Boolean) as { runId: string; tokens: Record<string, number> }[];
    setUsage(rows);
  }, [runs]);

  const save = useCallback(async () => {
    setSaving(true); setSaved(false); setError(null);
    try {
      const next = await service.updateSettings({
        tenantId: service.identity.tenantId,
        sessionId: service.identity.sessionId,
        requestId: requestIdForRun(0),
        providerMode: mode,
        deepseekModel: deepseek,
        ollamaModel: ollama,
      });
      setSettings(next); setSaved(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }, [service, mode, deepseek, ollama]);

  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <aside className="settings-drawer" onMouseDown={(e) => e.stopPropagation()}>
        <header className="modal-heading">
          <div><span className="panel-eyebrow">GLOBAL</span><h2>Settings</h2></div>
          <button className="modal-close" onClick={onClose} type="button"><Icon name="x" size={16} /></button>
        </header>

        <section className="set-section">
          <h3>Models & Providers</h3>
          {!settings && !error && <p className="omni-empty">Loading…</p>}
          {settings && (
            <>
              <div className="set-active">
                <span className="st-dot live" />
                <strong>{settings.activeProvider} · {settings.activeModel}</strong>
                <span className="omni-meta">当前生效</span>
              </div>
              <div className="field">
                <label>Provider</label>
                <select value={providerId} onChange={(e) => {
                  const p = PROVIDERS.find((x) => x.id === e.target.value)!;
                  setProviderId(p.id); setMode(p.mode); setApiBase(p.base);
                  const m = p.models[0] ?? "";
                  setModelSel(p.models.length ? m : "__custom__");
                  if (p.mode === "api") setDeepseek(m); else setOllama(m);
                }}>
                  {PROVIDERS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
                </select>
              </div>
              <div className="field">
                <label>Model</label>
                <select value={modelSel} onChange={(e) => {
                  const v = e.target.value;
                  setModelSel(v);
                  if (v !== "__custom__") { if (mode === "api") setDeepseek(v); else setOllama(v); }
                }}>
                  {(PROVIDERS.find((p) => p.id === providerId)?.models ?? []).map((m) => <option key={m} value={m}>{m}</option>)}
                  <option value="__custom__">custom…</option>
                </select>
                {modelSel === "__custom__" && (
                  <input value={mode === "api" ? deepseek : ollama} onChange={(e) => { if (mode === "api") setDeepseek(e.target.value); else setOllama(e.target.value); }} placeholder="model name…" />
                )}
              </div>
              <div className="field">
                <label>API Base</label>
                <input value={apiBase} onChange={(e) => setApiBase(e.target.value)} placeholder="https://api.deepseek.com/v1" />
                <span className="omni-meta">密钥走安全存储（OPENAI_API_KEY env）；endpoint 持久化需后端 api_base 字段。</span>
              </div>
            </>
          )}
          {error && <p className="omni-empty" style={{ color: "var(--st-danger,#ff5a4f)" }}>{error}</p>}
          {saved && <p className="omni-empty" style={{ color: "var(--st-ok,#4cc38a)" }}>Saved.</p>}
          <button className="btn primary" disabled={saving || !settings} onClick={() => void save()} type="button">
            {saving ? "Saving…" : "Save"}
          </button>
          <p className="demo-note">API keys live in secure storage — this surface shows masked status only, never secrets.</p>
        </section>

        <section className="set-section">
          <h3>Usage</h3>
          {usage.length === 0 && <p className="omni-empty">还没有 token 用量记录——run 结束时写入。</p>}
          {usage.length > 0 && (
            <>
              <table className="rec-table usage-table">
                <thead><tr><th>Run</th><th>Prompt</th><th>Completion</th><th>Total</th></tr></thead>
                <tbody>
                  {usage.map((u) => (
                    <tr key={u.runId}>
                      <td className="mono">{u.runId}</td>
                      <td>{u.tokens.prompt_tokens ?? u.tokens.input_tokens ?? "—"}</td>
                      <td>{u.tokens.completion_tokens ?? u.tokens.output_tokens ?? "—"}</td>
                      <td><strong>{u.tokens.total_tokens ?? Object.values(u.tokens).reduce((a, b) => a + b, 0)}</strong></td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="metric-row"><span>合计</span><strong>
                {usage.reduce((a, u) => a + (u.tokens.total_tokens ?? Object.values(u.tokens).reduce((x, b) => x + b, 0)), 0).toLocaleString()} tokens
              </strong></div>
            </>
          )}
        </section>

        <section className="set-section">
          <h3>平台凭据</h3>
          <div className="field"><label>Bilibili SESSDATA</label>
            <input defaultValue={localStorage.getItem("tsa.env.BILI_SESSDATA") ?? ""}
              onBlur={(e) => localStorage.setItem("tsa.env.BILI_SESSDATA", e.target.value)}
              placeholder="SESSDATA cookie — 供 UI 本地持有；后端 sidecar 读 BILI_SESSDATA env，真正生效需写入 .env" type="password" /></div>
          <p className="omni-empty">凭据保存在本机存储（不上传）。后端生效路径仍是 .env / sidecar env。</p>
        </section>

        <section className="set-section">
          <h3>More sections</h3>
          {profiles.map((p) => (
            <div className="cap-empty" key={p.profileId}><span className="cap-dot live" /><div><strong>{p.displayName || p.profileId}</strong><small>@{p.planetAffinity} · {p.modelRef} · {p.skillRefs.length} skills</small></div></div>
          ))}
          {workspaces.map((w) => (
            <div className="cap-empty" key={w.workspaceId}><span className="cap-dot live" /><div><strong>{w.displayName || w.workspaceId}</strong><small>{w.capabilities.join(", ") || "no caps"}</small></div></div>
          ))}
          {capabilities.map((c) => (
            <div className="cap-empty" key={c.capability}><span className="cap-dot live" /><div><strong>{c.capability}</strong><small>{c.tools.join(", ")}</small></div></div>
          ))}
          {skills.map((s) => (
            <div className="cap-empty" key={s.skillId}><span className="cap-dot live" /><div><strong>{s.name}</strong><small>{s.description}</small></div></div>
          ))}
          {profiles.filter((p) => Object.keys(p.permissionPolicy).length > 0).map((p) => (
            <div className="cap-empty" key={`perm-${p.profileId}`}><span className="cap-dot live" /><div><strong>{p.displayName || p.profileId} permissions</strong><small>{Object.entries(p.permissionPolicy).map(([k, v]) => `${k}=${String(v)}`).join(", ")}</small></div></div>
          ))}
          <div className="cap-empty"><span className="cap-dot live" /><div><strong>Runtime & Diagnostics</strong><small>mode {service.mode} · session {service.identity.sessionId} · {capabilities.length} capabilities · {skills.length} skills · {profiles.length} profiles</small></div></div>
          {["General", "Appearance", "Memory Policy", "Notifications"].map((s) => (
            <div className="cap-empty" key={s}><span className="cap-dot" /><div><strong>{s}</strong><small>Section registered — surface not installed yet.</small></div></div>
          ))}
        </section>
      </aside>
    </div>
  );
}
