import { useCallback, useEffect, useReducer, useState } from "react";
import type { CapabilitySummary, DesktopAgentServiceClient, ProfileSummary, SettingsSummary, SkillSummary, WorkspaceSummary } from "../types/service";
import type { RunView } from "../types";
import { Icon, requestIdForRun } from "./ui";
import { PROVIDERS, SettingsForm } from "./settingsForm";
import { t, useLang, setLang, type Lang } from "./i18n";

/** Global Settings drawer — §4.2. Reachable from every workspace via the
 *  topbar gear; provider/model fields are backed by getSettings/updateSettings,
 *  other sections show their partition contract with a not-installed state.
 *  Provider/model state lives in SettingsForm so select/edit events always
 *  write the field the backend saves. */
export default function GlobalSettings({ service, sessionId, onClose, runs = [] }: { service: DesktopAgentServiceClient; sessionId: string; onClose: () => void; runs?: RunView[] }) {
  const lang = useLang();
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
      sessionId,
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
      sessionId,
      requestId: requestIdForRun(0),
    }).then((p) => { if (!stale) setProfiles(p); }).catch(() => {});
    service.listCapabilities({
      tenantId: service.identity.tenantId,
      sessionId,
      requestId: requestIdForRun(0),
    }).then((c) => { if (!stale) setCapabilities(c); }).catch(() => {});
    service.listSkills({
      tenantId: service.identity.tenantId,
      sessionId,
      requestId: requestIdForRun(0),
    }).then((s) => { if (!stale) setSkills(s); }).catch(() => {});
    service.listWorkspaces({
      tenantId: service.identity.tenantId,
      sessionId,
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
        sessionId,
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
          <div><span className="panel-eyebrow">GLOBAL</span><h2>{t("settings.title")}</h2></div>
          <button aria-label={t("settings.close")} className="modal-close" onClick={onClose} type="button"><Icon name="x" size={16} /></button>
        </header>

        <section className="set-section">
          <h3>{t("settings.interface")}</h3>
          <div className="field">
            <label>{t("settings.language")}</label>
            <select value={lang} onChange={(e) => setLang(e.target.value as Lang)}>
              <option value="zh">中文</option>
              <option value="en">English</option>
            </select>
            <span className="omni-meta">{t("settings.lang-follow")}</span>
          </div>
        </section>

        <section className="set-section">
          <h3>{t("settings.models-providers")}</h3>
          {!settings && !error && <p className="omni-empty">{t("settings.loading")}</p>}
          {settings && (
            <>
              <div className="set-active">
                <span className="st-dot live" />
                <strong>{settings.activeProvider} · {settings.activeModel}</strong>
                <span className="omni-meta">{t("settings.active")}</span>
              </div>
              <div className="field">
                <label>{t("settings.provider")}</label>
                <select value={form.providerId} onChange={(e) => { form.selectProvider(e.target.value); bump(); }}>
                  {PROVIDERS.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
                </select>
              </div>
              <div className="field">
                <label>{t("settings.model")}</label>
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
                <span className="omni-meta"><code>{form.apiBase}</code> — {t("settings.apibase-note")}</span>
              </div>
            </>
          )}
          {error && <p className="omni-empty" style={{ color: "var(--st-danger,#ff5a4f)" }}>{error}</p>}
          {saved && <p className="omni-empty" style={{ color: "var(--st-ok,#4cc38a)" }}>{t("settings.saved")}</p>}
          <button className="btn primary" disabled={saving || !settings} onClick={() => void save()} type="button">
            {saving ? t("settings.saving") : t("settings.save")}
          </button>
          <p className="demo-note">{t("settings.keys-note")}</p>
        </section>

        <section className="set-section">
          <h3>{t("settings.usage")}</h3>
          {usage.length === 0 && <p className="omni-empty">{t("settings.usage-empty")}</p>}
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
              <div className="metric-row"><span>{t("settings.usage-total")}</span><strong>
                {usage.reduce((a, u) => a + (u.tokens.total_tokens ?? Object.values(u.tokens).reduce((x, b) => x + b, 0)), 0).toLocaleString()} tokens
              </strong></div>
            </>
          )}
        </section>

        <section className="set-section">
          <h3>{t("settings.creds")}</h3>
          <div className="field"><label>Bilibili SESSDATA</label>
            <input defaultValue={localStorage.getItem("tsa.env.BILI_SESSDATA") ?? ""}
              onBlur={(e) => localStorage.setItem("tsa.env.BILI_SESSDATA", e.target.value)}
              placeholder="SESSDATA cookie — 供 UI 本地持有；后端 sidecar 读 BILI_SESSDATA env，真正生效需写入 .env" type="password" /></div>
          <p className="omni-empty">{t("settings.creds-note")}</p>
        </section>

        <section className="set-section">
          <h3>{t("settings.more")}</h3>
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
          <div className="cap-empty"><span className="cap-dot live" /><div><strong>Runtime & Diagnostics</strong><small>mode {service.mode} · session {sessionId} · {capabilities.length} capabilities · {skills.length} skills · {profiles.length} profiles</small></div></div>
          {["General", "Appearance", "Memory Policy", "Notifications"].map((s) => (
            <div className="cap-empty" key={s}><span className="cap-dot" /><div><strong>{s}</strong><small>Section registered — surface not installed yet.</small></div></div>
          ))}
        </section>
      </aside>
    </div>
  );
}
