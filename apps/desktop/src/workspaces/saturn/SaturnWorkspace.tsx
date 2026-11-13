import { useCallback, useEffect, useState, type FormEvent } from "react";
import { SCENES } from "../../app/scenes";
import { WorkspaceFrame } from "../../design-system/WorkspaceFrame";
import AgentComposer from "../../app/AgentComposer";
import { requestIdForRun } from "../../app/ui";
import { t } from "../../app/i18n";
import type { RunCtx } from "../../app/runCtx";
import type { ScheduleSummary } from "../../types/service";

/** Chronos per hand-drawn spec §8 — task kanban (click card → config) |
 *  today's report | AgentComposer. */
export default function SaturnWorkspace({ ctx }: { ctx: RunCtx }) {
  const { service } = ctx;
    const scene = SCENES[6];
  const [schedules, setSchedules] = useState<ScheduleSummary[]>([]);
  const [sel, setSel] = useState<ScheduleSummary | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [mins, setMins] = useState("60");
  const [planet, setPlanet] = useState("");
  const [creating, setCreating] = useState(false);

  const req = useCallback(() => ({ tenantId: service.identity.tenantId, sessionId: service.identity.sessionId, requestId: requestIdForRun(0) }), [service]);
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
    if (!text.trim() || !(seconds > 0)) { setErr("request text + 正数分钟数"); return; }
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
          <input className="chron-in" onChange={(e) => setText(e.target.value)} placeholder="定时任务内容…" value={text} />
          <input className="chron-in chron-min" onChange={(e) => setMins(e.target.value)} placeholder="分钟" title="间隔（分钟）" type="number" value={mins} />
          <select className="chron-in chron-pl" onChange={(e) => setPlanet(e.target.value)} value={planet}>
            <option value="">planet…</option>
            {["sun","mercury","venus","earth","mars","jupiter","saturn","uranus","neptune","pluto"].map((w) => <option key={w} value={w}>{w}</option>)}
          </select>
          <button className="lab-run" disabled={creating} onClick={() => void create()} type="button">+ 创建</button>
        </div>
        {err && <p className="chron-err">{err}</p>}
        <div className="chron-board">
          {schedules.map((s) => (
            <button className={`chron-card ${s.status.toLowerCase()} ${sel?.scheduleId === s.scheduleId ? "on" : ""}`} key={s.scheduleId}
              onClick={() => setSel(s)} type="button">
              <span className={`chron-dot ${s.status.toLowerCase()}`} />
              <strong>{s.requestText.slice(0, 30)}{s.requestText.length > 30 ? "…" : ""}</strong>
              <small>{s.intervalSeconds >= 86400 ? `每 ${Math.round(s.intervalSeconds / 86400)} 天` : `每 ${Math.round(s.intervalSeconds / 3600)} 小时`} · {s.targetPlanet}</small>
              <em>{s.status}</em>
            </button>
          ))}
          {schedules.length === 0 && <div className="empty-state"><p>No scheduled tasks</p></div>}
        </div>
        <aside className="chron-side">
          {ctx.activeRun && (
            <div className="ws-hud"><span className="hud-mark">◈</span>
              <div className="ws-hud-info"><strong>Chronos</strong>
                <small><code>{ctx.activeRun.runId}</code> · {ctx.activeRun.status}</small></div>
            </div>
          )}
          <h4 className="ws-mod">当日汇报</h4>
          {sel ? (
            <div className="chron-detail">
              <div className="ven-meta"><span>task</span><code>{sel.requestText}</code></div>
              <div className="ven-meta"><span>周期</span><code>{sel.intervalSeconds}s</code></div>
              <div className="ven-meta"><span>目标</span><code>@{sel.targetPlanet}</code></div>
              <div className="ven-meta"><span>{t("saturn.next-fire")}</span><code>{sel.nextFireAt ? sel.nextFireAt.slice(0, 19).replace("T", " ") : "—"}</code></div>
              {sel.lastRunId && (
                <div className="ven-meta"><span>{t("saturn.last-run")}</span>
                  <button className="ven-link" onClick={() => void ctx.controller.refreshRun(sel.lastRunId, ctx.sessionId).then(() => ctx.openRun(sel.lastRunId)).catch((e) => setErr(String(e)))} type="button">{sel.lastRunId}</button>
                </div>
              )}
              <button className="btn secondary" onClick={() => void toggle(sel)} type="button">{sel.status === "ACTIVE" ? "暂停" : "恢复"}</button>
            </div>
          ) : <p className="omni-empty">点卡片设置任务（周期/目标）</p>}
          {err && <p className="omni-empty" style={{ color: "var(--st-danger,#ff5a4f)" }}>{err}</p>}
          <AgentComposer ctx={ctx} planet="saturn" placeholder="新计划 — 描述周期任务…" />
          <div className="ws-statusbar"><span>{schedules.filter((x) => x.status === "ACTIVE").length} ACTIVE</span><span>{schedules.length} SCHEDULES</span><span className="right">saturn</span></div>
        </aside>
      </div>
    </WorkspaceFrame>
  );
}
