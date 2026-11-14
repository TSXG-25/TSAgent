import { t } from "./i18n";
import { StatusBadge } from "./ui";
import type { RunCtx } from "./runCtx";
import { selectWorkspaceRun, useWorkspaceRunId, workspaceRuns } from "./workspaceSelection";

/** Task rail for one workstation — list, progress, failures and result
 *  entries for this scope, with per-run pause/cancel and an explicit
 *  "open in Omni" navigation. */
export function WorkspaceTasks({ ctx, planet }: { ctx: RunCtx; planet: string }) {
  const tenantId = ctx.service.identity.tenantId;
  const selId = useWorkspaceRunId(tenantId, ctx.sessionId, planet);
  const mine = workspaceRuns(ctx, planet);
  return (
    <div className="ws-tasks">
      <h4 className="ws-mod">{t("wstasks.title")}</h4>
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
    </div>
  );
}
