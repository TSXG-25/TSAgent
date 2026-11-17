import { useEffect, useState } from "react";
import { t } from "./i18n";
import { StatusBadge } from "./ui";
import type { RunCtx } from "./runCtx";
import type { SessionSummary } from "../types/service";
import { selectWorkspaceRun, useWorkspaceRunId, workspaceRuns } from "./workspaceSelection";

/** Task rail for one workstation — list, progress, failures and result
 *  entries for this scope, with per-run pause/cancel and an explicit
 *  "open in Omni" navigation. */
export function WorkspaceTasks({ ctx, planet }: { ctx: RunCtx; planet: string }) {
  const tenantId = ctx.service.identity.tenantId;
  const selId = useWorkspaceRunId(tenantId, ctx.sessionId, planet);
  const mine = workspaceRuns(ctx, planet);
  // Delegated runs land in derived child sessions — load this planet's
  // tenant-wide catalog so they stay discoverable here (never guessed:
  // durable originPlanet is the only membership fact).
  useEffect(() => {
    void ctx.controller.loadTenantCatalog(planet);
  }, [ctx.controller, planet]);
  const delegated = ctx.runs.filter(
    (run) => run.originPlanet === planet && run.sessionId !== ctx.sessionId,
  );
  return (
    <div className="ws-tasks">
      <div className="ws-tasks-head">
        <h4 className="ws-mod">{t("wstasks.title")}</h4>
        <PlanetSessionPicker ctx={ctx} planet={planet} />
      </div>
      {mine.length === 0 && <p className="omni-empty">{t("wstasks.empty")}</p>}
      {mine.map((run) => (
        <div className={`ws-task${run.runId === selId ? " sel" : ""}`} key={run.runId}>
          <button
            type="button"
            className="ws-task-title"
            onClick={() => selectWorkspaceRun(tenantId, ctx.sessionId, planet, run.runId)}
          >
            {run.request || run.runId}
          </button>
          <div className="ws-task-row">
            <StatusBadge status={run.status} />
            {run.status === "active" && (
              <>
                <button type="button" className="btn secondary ws-task-op"
                  onClick={() => void ctx.controller.pauseRun(run.runId)}>
                  {t("wstasks.pause")}
                </button>
                <button type="button" className="btn secondary ws-task-op"
                  onClick={() => void ctx.controller.cancelRun(run.runId)}>
                  {t("wstasks.cancel")}
                </button>
              </>
            )}
            <button type="button" className="ven-link"
              onClick={() => ctx.openRun(run.runId, run.sessionId)}>
              {t("wstasks.open-omni")}
            </button>
          </div>
          {run.failure && <p className="ws-task-fail">{run.failure.message}</p>}
        </div>
      ))}
      {delegated.length > 0 && (
        <>
          <h4 className="ws-mod">{t("wstasks.delegated")}</h4>
          {delegated.map((run) => (
            <div className="ws-task" key={run.runId}>
              <button
                type="button"
                className="ws-task-title"
                onClick={() => ctx.openRun(run.runId, run.sessionId)}
              >
                {run.request || run.runId}
              </button>
              <div className="ws-task-row">
                <StatusBadge status={run.status} />
                <span className="ws-task-sess">{run.sessionId}</span>
                <button type="button" className="ven-link"
                  onClick={() => ctx.openRun(run.runId, run.sessionId)}>
                  {t("wstasks.open-omni")}
                </button>
              </div>
              {run.failure && <p className="ws-task-fail">{run.failure.message}</p>}
            </div>
          ))}
        </>
      )}
    </div>
  );
}

/** Per-workstation session picker — lists sessions owned by this planet
 *  plus unclaimed ones (explicit adoption on select), and creates fresh
 *  planet-tagged sessions. */
function PlanetSessionPicker({ ctx, planet }: { ctx: RunCtx; planet: string }) {
  const service = ctx.service;
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [creating, setCreating] = useState(false);
  useEffect(() => {
    let alive = true;
    void service.listSessions({
      }).then((list) => { if (alive) setSessions(list); }).catch(ctx.reportError);
    return () => { alive = false; };
  }, [service, planet, ctx.sessionId]);
  const options = sessions.filter((s) => !s.planet || s.planet === planet);
  const known = options.some((s) => s.sessionId === ctx.sessionId);
  return (
    <span className="ws-sess-pick">
      <select
        className="ws-sess-sel"
        onChange={(e) => ctx.selectSession(e.target.value)}
        title={t("wstasks.session")}
        value={ctx.sessionId}
      >
        {!known && <option value={ctx.sessionId}>{ctx.sessionId}</option>}
        {options.map((s) => (
          <option key={s.sessionId} value={s.sessionId}>
            {s.sessionId}{!s.planet ? " ·" : ""}
          </option>
        ))}
      </select>
      <button
        className="btn secondary ws-sess-new"
        disabled={creating}
        onClick={() => {
          setCreating(true);
          void service.createSession({
            sessionId: `session-${planet}-${crypto.randomUUID().slice(0, 8)}`,
            planet,
          }).then((s) => ctx.selectSession(s.sessionId))
            .finally(() => setCreating(false));
        }}
        title={t("wstasks.new-session")}
        type="button"
      >＋</button>
    </span>
  );
}
