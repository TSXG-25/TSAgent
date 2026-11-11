import { useCallback, useEffect, useReducer, useState } from "react";
import type { CapabilitySummary, DesktopAgentServiceClient, ProfileSummary, SettingsSummary, SkillSummary, WorkspaceSummary } from "../types/service";
import type { RunView } from "../types";
import { Icon, requestIdForRun } from "./ui";
import { PROVIDERS, SettingsForm } from "./settingsForm";

/** Global Settings drawer — §4.2. Reachable from every workspace via the
 *  topbar gear; provider/model fields are backed by getSettings/updateSettings,
 *  other sections show their partition contract with a not-installed state.
 *  Provider/model state lives in SettingsForm so select/edit events always
 *  write the field the backend saves. */
export default function GlobalSettings({ service, onClose, runs = [] }: { service: DesktopAgentServiceClient; onClose: () => void; runs?: RunView[] }) {
  const [settings, setSettings] = useState<SettingsSummary | null>(null);
  const [form] = useState(() => new SettingsForm());
  const [, bump] = useReducer((x: number) => x + 1, 0);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
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
      setSettings(s);
      // 恢复已保存的模型而非首项 — 非首项模型重开不再跳回列表头。
      form.load(s);
      bump();
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
  }, [service, form]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    const rows = runs.map((r) => {
      const ev = [...(r.events ?? [])].reverse().find((e) => e.eventType === "token_usage");
      if (!ev?.payload) return null;
      const tokens = Object.fromEntries(
        Object.entries(ev.payload).filter(([, v]) => typeof v === "number"),
      ) as Record<string, number>;
      return { runId: r.runId, tokens };
    }).filter((u): u is { runId: string; tokens: Record<string, number> } => u !== null);
    setUsage(rows);
  }, [runs]);

  const save = useCallback(async () => {
    setSaving(true); setSaved(false); setError(null);
    try {
      const next = await service.updateSettings({
        tenantId: service.identity.tenantId,
        sessionId: service.identity.sessionId,
        requestId: requestIdForRun(0),
        ...form.toSaveRequest(),
      });
      setSettings(next); setSaved(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }, [service, form]);

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
                <select value={form.providerId} onChange={(e) => { form.selectProvider(e.target.value); bump(); }}>
                  {PROVIDERS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
                </select>
              </div>
              <div className="field">
                <label>Model</label>
                <select value={form.modelSel} onChange={(e) => { form.selectModel(e.target.value); bump(); }}>
                  {(PROVIDERS.find((p) => p.id === form.providerId)?.models ?? []).map((m) => <option key={m} value={m}>{m}</option>)}
                  <option value="__custom__">custom…</option>
                </select>
                {form.modelSel === "__custom__" && (
                  <input value={form.mode === "deepseek" ? form.deepseek : form.ollama} onChange={(e) => { form.editCustomModel(e.target.value); bump(); }} placeholder="model name…" />
                )}
              </div>
              <div className="field">
                <label>API Base</label>
                <span className="omni-meta"><code>{form.apiBase}</code> — 后端尚不支持保存自定义 endpoint。</span>
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
