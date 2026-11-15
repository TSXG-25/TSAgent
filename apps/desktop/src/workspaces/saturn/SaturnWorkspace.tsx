import { useCallback, useEffect, useState, type FormEvent } from "react";
import { SCENES } from "../../app/scenes";
import { WorkspaceFrame } from "../../design-system/WorkspaceFrame";
import AgentComposer from "../../app/AgentComposer";
import { requestIdForRun } from "../../app/ui";
import { t } from "../../app/i18n";
import type { RunCtx } from "../../app/runCtx";
import { useWorkspaceCurrentRun } from "../../app/workspaceSelection";
import { WorkspaceTasks } from "../../app/WorkspaceTasks";
import type { ScheduleSummary } from "../../types/service";

/** Chronos per hand-drawn spec §8 — task kanban (click card → config) |
 *  today's report | AgentComposer. */
export default function SaturnWorkspace({ ctx }: { ctx: RunCtx }) {
  const { service } = ctx;
  const currentRun = useWorkspaceCurrentRun(ctx, "saturn");
    const scene = SCENES[6];
  const [schedules, setSchedules] = useState<ScheduleSummary[]>([]);
  // 选中只存 id —— 详情始终从最新列表派生，暂停/触发/刷新后不再持有旧对象。
  const [selId, setSelId] = useState("");
  const sel = schedules.find((s) => s.scheduleId === selId) ?? null;
  const [err, setErr] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [mins, setMins] = useState("60");
  const [planet, setPlanet] = useState("");
  const [creating, setCreating] = useState(false);

  const req = useCallback(() => ({ tenantId: service.identity.tenantId, sessionId: ctx.sessionId, requestId: requestIdForRun(0) }), [service, ctx.sessionId]);
  const refresh = useCallback(async () => {
    try { setSchedules(await service.listSchedules(req())); } catch (e) { setErr(String(e)); }
  }, [service, req]);
  useEffect(() => { void refresh(); const t = setInterval(() => void refresh(), 10_000); return () => clearInterval(t); }, [refresh]);

  async function toggle(s: ScheduleSummary) {
    try { await service.updateSchedule({ ...req(), scheduleId: s.scheduleId, status: s.status === "ACTIVE" ? "PAUSED" : "ACTIVE" }); await refresh(); }
    catch (e) { setErr(String(e)); }
  }

  async function create() {
    const seconds = Math.round(Number(mins) * 60);
    if (!text.trim() || !(seconds > 0)) { setErr(t("sat.err-input")); return; }
    setCreating(true); setErr(null);
    try {
      await service.createSchedule({ ...req(), sessionId: ctx.sessionId, requestText: text.trim(), intervalSeconds: seconds, ...(planet ? { targetPlanet: planet } : {}) });
      setText(""); setMins("60"); setPlanet(""); await refresh();
    } catch (e) { setErr(String(e)); } finally { setCreating(false); }
  }

  return (
    <WorkspaceFrame scene={scene} ctx={ctx}>
      <div className="chron">
        <div className="chron-create">
          <input className="chron-in" onChange={(e) => setText(e.target.value)} placeholder={t("sat.ph-task")} value={text} />
          <input className="chron-in chron-min" onChange={(e) => setMins(e.target.value)} placeholder={t("sat.ph-mins")} title={t("sat.mins-title")} type="number" value={mins} />
          <select className="chron-in chron-pl" onChange={(e) => setPlanet(e.target.value)} value={planet}>
            <option value="">{t("sat.ph-planet")}</option>
            {["sun","mercury","venus","earth","mars","jupiter","saturn","uranus","neptune","pluto"].map((w) => <option key={w} value={w}>{w}</option>)}
          </select>
          <button className="lab-run" disabled={creating} onClick={() => void create()} type="button">{t("sat.create")}</button>
        </div>
        {err && <p className="chron-err">{err}</p>}
        <div className="chron-board">
          {schedules.map((s) => (
            <button className={`chron-card ${s.status.toLowerCase()} ${sel?.scheduleId === s.scheduleId ? "on" : ""}`} key={s.scheduleId}
              onClick={() => setSelId(s.scheduleId)} type="button">
              <span className={`chron-dot ${s.status.toLowerCase()}`} />
              <strong>{s.requestText.slice(0, 30)}{s.requestText.length > 30 ? "…" : ""}</strong>
              <small>{s.intervalSeconds >= 86400 ? t("sat.every-days", { n: Math.round(s.intervalSeconds / 86400) }) : s.intervalSeconds >= 3600 ? t("sat.every-hours", { n: Math.round(s.intervalSeconds / 3600) }) : t("sat.every-minutes", { n: Math.max(1, Math.round(s.intervalSeconds / 60)) })} · {s.targetPlanet}</small>
              <em>{s.status}</em>
            </button>
          ))}
          {schedules.length === 0 && <div className="empty-state"><p>{t("sat.empty")}</p></div>}
        </div>
        <aside className="chron-side">
          {currentRun && (
            <div className="ws-hud"><span className="hud-mark">◈</span>
              <div className="ws-hud-info"><strong>Chronos</strong>
                <small><code>{currentRun.runId}</code> · {currentRun.status}</small></div>
            </div>
          )}
          <h4 className="ws-mod">{t("sat.report")}</h4>
          {sel ? (
            <div className="chron-detail">
              <div className="ven-meta"><span>{t("sat.label-task")}</span><code>{sel.requestText}</code></div>
              <div className="ven-meta"><span>{t("sat.label-interval")}</span><code>{sel.intervalSeconds}s</code></div>
              <div className="ven-meta"><span>{t("sat.label-target")}</span><code>@{sel.targetPlanet}</code></div>
              <div className="ven-meta"><span>{t("saturn.next-fire")}</span><code>{sel.nextFireAt ? new Date(sel.nextFireAt).toLocaleString() : "—"}</code></div>
              {sel.lastRunId && (
                <div className="ven-meta"><span>{t("saturn.last-run")}</span>
                  <button className="ven-link" onClick={() => ctx.openRun(sel.lastRunId, sel.sessionId)} type="button">{sel.lastRunId}</button>
                </div>
              )}
              <button className="btn secondary" onClick={() => void toggle(sel)} type="button">{sel.status === "ACTIVE" ? t("sat.pause") : t("sat.resume")}</button>
            </div>
          ) : <p className="omni-empty">{t("sat.pick")}</p>}
          {err && <p className="omni-empty" style={{ color: "var(--st-danger,#ff5a4f)" }}>{err}</p>}
          <AgentComposer ctx={ctx} planet="saturn" placeholder={t("sat.composer-ph")} />
                    <WorkspaceTasks ctx={ctx} planet="saturn" />
          <div className="ws-statusbar"><span>{schedules.filter((x) => x.status === "ACTIVE").length} ACTIVE</span><span>{schedules.length} SCHEDULES</span><span className="right">saturn</span></div>
        </aside>
      </div>
    </WorkspaceFrame>
  );
}
