import { useCallback, useEffect, useState } from "react";
import type { RunCtx } from "./runCtx";
import { Icon, requestIdForRun } from "./ui";
import type { ApprovalSummary, TaskRecord } from "../types/service";

/** Global task/approval tray — §4.3. Pending approvals reachable from every
 *  surface; Approve Once / Deny write through decideApproval. */
export default function TaskTray({ ctx }: { ctx: RunCtx }) {
  const { service } = ctx;
  const [open, setOpen] = useState(false);
  const [approvals, setApprovals] = useState<ApprovalSummary[]>([]);
  const [tasks, setTasks] = useState<TaskRecord[]>([]);
  const [busy, setBusy] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [a, t] = await Promise.all([
        service.listApprovals({
          tenantId: service.identity.tenantId,
          sessionId: service.identity.sessionId,
          requestId: requestIdForRun(0),
          status: "PENDING",
        }),
        service.listTasks(),
      ]);
      setApprovals(a);
      setTasks(t.filter((x) => ["queued", "planning", "active"].includes(x.status)));
    } catch { /* keep last snapshot */ }
  }, [service]);

  useEffect(() => {
    void refresh();
    const t = window.setInterval(() => void refresh(), 8_000);
    return () => window.clearInterval(t);
  }, [refresh]);

  async function decide(a: ApprovalSummary, decision: "approve" | "deny") {
    setBusy(a.approvalId);
    try {
      await service.decideApproval({
        tenantId: service.identity.tenantId,
        sessionId: service.identity.sessionId,
        requestId: requestIdForRun(0),
        runId: a.runId,
        approvalId: a.approvalId,
        decision,
        decidedBy: service.identity.userId,
      });
      await refresh();
    } finally { setBusy(null); }
  }

  return (
    <div className="task-tray">
      <button className={`topbar-button tray-btn ${approvals.length ? "hot" : ""}`} onClick={() => setOpen((v) => !v)}
        title="Pending approvals" type="button">
        <Icon name="shield" size={15} />
        {approvals.length > 0 && <span className="tray-badge">{approvals.length}</span>}
      </button>
      {open && (
        <div className="tray-pop">
          <header className="tray-head">PENDING APPROVALS <code>{approvals.length}</code></header>
          {tasks.length > 0 && (
            <>
              <header className="tray-head">LIVE TASKS <code>{tasks.length}</code></header>
              {tasks.slice(0, 4).map((t) => (
                <div className="tray-card" key={t.taskId}>
                  <div className="tray-card-head"><code>{t.originPlanet || "sun"}</code><strong>{t.status}</strong></div>
                  <p>{t.taskId} · {t.runIds.length} runs</p>
                </div>
              ))}
            </>
          )}
          {approvals.length === 0 && <p className="omni-empty">Nothing waiting.</p>}
          {approvals.map((a) => (
            <div className="tray-card" key={a.approvalId}>
              <div className="tray-card-head"><code>{a.riskLevel}</code><strong>{a.action}</strong></div>
              <p>{a.scope} · {a.runId}</p>
              <div className="tray-actions">
                <button disabled={busy === a.approvalId} onClick={() => void decide(a, "approve")} type="button">Approve once</button>
                <button className="danger" disabled={busy === a.approvalId} onClick={() => void decide(a, "deny")} type="button">Deny</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
