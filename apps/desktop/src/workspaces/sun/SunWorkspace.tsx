import { useEffect, useMemo, useState } from "react";
import { SCENES } from "../../app/scenes";
import { setSidecarPicker } from "../../service/tauriBridge";
import { WorkspaceFrame } from "../../design-system/WorkspaceFrame";
import AgentComposer from "../../app/AgentComposer";
import { t } from "../../app/i18n";
import { useSessionApprovals } from "../../app/approvals";
import type { RunCtx } from "../../app/runCtx";
import type { WorkspaceSummary } from "../../types/service";
import SessionRail from "./SessionRail";
import RunThread from "./RunThread";
import ApprovalCards from "./ApprovalCards";
import { useSunSessions } from "./useSunSessions";
import { useSunDialogs } from "./SunDialogs";

/** Omni v3 — task-workbench layout per review:
 *  SessionRail (task titles+status+search) | approval cards + RunThread +
 *  AgentComposer | status footer.
 *  All data real: sessions.latestStatus, run request/status/events/
 *  artifacts/approvals. Nothing fabricated. */

export default function SunWorkspace({ ctx }: { ctx: RunCtx }) {
  const { service, sessionId, runs } = ctx;
  const scene = SCENES[0];
  const { sessions, participants, refresh, newSession, titleOf, viewRun } = useSunSessions(ctx);
  // single shared approval store — same state + decide entrypoint as TaskTray
  const approvals = useSessionApprovals(service, sessionId);
  const [wsList, setWsList] = useState<WorkspaceSummary[]>([]);
  const refreshWs = () => {
    void service.listWorkspaces({ sessionId: ctx.sessionId }).then(setWsList).catch(ctx.reportError);
  };
  useEffect(refreshWs, [ctx.sessionId]);
  useEffect(() => {
    // Only the local client can drive a real folder picker via the sidecar;
    // mock mode falls back to tauriBridge's webkitdirectory input.
    if (service.mode !== "local") return;
    setSidecarPicker(() => service.pickFolder());
    return () => setSidecarPicker(null);
  }, [service]);
  // Omni aggregates every tenant session — hydrate the tenant-wide
  // catalog once per mount and let the rail filter by workstation.
  useEffect(() => { void ctx.controller.loadTenantCatalog(); }, [ctx.controller]);

  // session → workspace path (backend resolves run scope from session_heads)
  const workspaceOf = (sid: string) => {
    const sess = sessions.find((x) => x.sessionId === sid);
    const ws = wsList.find((w) => w.workspaceId === (sess?.workspaceId ?? ""));
    return ws?.path ?? "";
  };
  const setFolder = (sid: string, wsId: string) => {
    void service.assignSessionWorkspace({
      sessionId: sid,
      workspaceId: wsId,
    }).then(() => { refreshWs(); void refresh(); }).catch(ctx.reportError);
  };
  const { api: dialogs, node: dialogNodes } = useSunDialogs(ctx, wsList, {
    setFolder,
    onChanged: () => { refreshWs(); void refresh(); },
  });

  const wfSteps = useMemo(() => (viewRun?.workflows ?? []).flatMap((w) => w.stages.map((st) => ({ stage: st.name, wf: w.name, status: st.status, duration: st.duration }))), [viewRun]);
  const activeCount = runs.filter((r) => r.status === "active").length;
  const curPhase = wfSteps.find((s) => s.status === "running")?.stage ?? "";

  return (
    <WorkspaceFrame scene={scene} ctx={ctx}>
      <div className="omni3">
        <SessionRail
          ctx={ctx}
          sessions={sessions}
          titleOf={titleOf}
          wsList={wsList}
          onNewSession={newSession}
          onSetFolder={setFolder}
          onSessionsChanged={() => void refresh()}
          openPrompt={dialogs.openPrompt}
          openConfirm={dialogs.openConfirm}
          onEditWorkspace={dialogs.openEditWs}
          onRemoveWorkspace={dialogs.removeWorkspace}
        />
        {dialogNodes}

        {/* ── Main column ── */}
        <main className="omni-col">
          {approvals.length > 0 && <ApprovalCards approvals={approvals} ctx={ctx} />}
          <RunThread approvals={approvals} ctx={ctx} curPhase={curPhase} viewRun={viewRun} wfSteps={wfSteps} />

          {workspaceOf(sessionId) && <div className="ws-bound" title={workspaceOf(sessionId)}>📁 {t("sun.bound-to")}: {workspaceOf(sessionId).split("/").pop()}<i onClick={() => setFolder(sessionId, "")}>✕</i></div>}<AgentComposer ctx={{ ...ctx, workspacePath: workspaceOf(sessionId) }} planet="sun" placeholder={t("sun.composer-placeholder")} />

          {/* status footer — real counts */}
          <footer className="omni-statusbar">
            <span>{t("footer.running", { n: activeCount })} · {t("footer.waiting", { n: runs.filter((r) => r.status === "blocked").length })}</span>
            <span>{t("footer.pending-approval", { n: approvals.length })}</span>
            <span className="omni-status-right" title={participants.map((x) => `${x.role}:${x.participantId}`).join(" ") + (participants.length ? " · " : "") + sessionId}>{participants.length > 0 ? `👥 ${participants.map((x) => `${x.role}:${x.participantId.slice(0,12)}`).join(" ")} · ` : ""}{sessionId}</span>
          </footer>
        </main>
      </div>
    </WorkspaceFrame>
  );
}
