import { useCallback, useEffect, useState, type FormEvent } from "react";
import { SCENES } from "../../app/scenes";
import { WorkspaceFrame } from "../../design-system/WorkspaceFrame";
import AgentComposer from "../../app/AgentComposer";
import { requestIdForRun } from "../../app/ui";
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

  const req = useCallback(() => ({ tenantId: service.identity.tenantId, sessionId: service.identity.sessionId, requestId: requestIdForRun(0) }), [service]);
  const refresh = useCallback(async () => {
    try { setSchedules(await service.listSchedules(req() as never)); } catch (e) { setErr(String(e)); }
  }, [service, req]);
  useEffect(() => { void refresh(); const t = setInterval(() => void refresh(), 10_000); return () => clearInterval(t); }, [refresh]);

  async function toggle(s: ScheduleSummary) {
    try { await service.updateSchedule({ ...req(), scheduleId: s.scheduleId, status: s.status === "ACTIVE" ? "PAUSED" : "ACTIVE" } as never); await refresh(); }
    catch (e) { setErr(String(e)); }
  }

  return (
    <WorkspaceFrame scene={scene} ctx={ctx}>
      <div className="chron">
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
              <button className="btn secondary" onClick={() => void toggle(sel)} type="button">{sel.status === "ACTIVE" ? "暂停" : "恢复"}</button>
            </div>
          ) : <p className="omni-empty">点卡片设置任务（周期/目标）</p>}
          {err && <p className="omni-empty" style={{ color: "var(--st-danger,#ff5a4f)" }}>{err}</p>}
          <AgentComposer ctx={ctx} planet="saturn" placeholder="新计划 — 描述周期任务…" />
          <div className="ws-statusbar"><span className="sys-ok">● ONLINE</span><span>{schedules.filter((x) => x.status === "ACTIVE").length} ACTIVE</span><span>{schedules.length} SCHEDULES</span><span className="right">saturn</span></div>
        </aside>
      </div>
    </WorkspaceFrame>
  );
}
