import { useCallback, useEffect, useState } from "react";
import type { RunCtx } from "./runCtx";
import { Icon } from "./ui";
import { decideSessionApproval, useSessionApprovals } from "./approvals";
import { t } from "./i18n";
import type { ApprovalSummary, TaskRecord } from "../types/service";

/** Global task/approval tray — §4.3. Pending approvals share the single
 *  session approval store with workspace approval cards (same poll, same
 *  decide entrypoint); each card links to its owning run. */
export default function TaskTray({ ctx }: { ctx: RunCtx }) {
  const { service, sessionId, openRun } = ctx;
  const [open, setOpen] = useState(false);
  const approvals = useSessionApprovals(service, sessionId);
  const [tasks, setTasks] = useState<TaskRecord[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [cardError, setCardError] = useState<Record<string, string>>({});

  const refreshTasks = useCallback(async () => {
    try {
      const all = await service.listTasks({ tenantId: service.identity.tenantId, sessionId });
      setTasks(all.filter((x) => ["queued", "planning", "active"].includes(x.status)));
    } catch { /* keep last snapshot */ }
  }, [service]);

  useEffect(() => {
    void refreshTasks();
    const t = window.setInterval(() => void refreshTasks(), 8_000);
    return () => window.clearInterval(t);
  }, [refreshTasks]);

  async function decide(a: ApprovalSummary, decision: "approve" | "deny") {
    setBusy(a.approvalId);
    try {
      await decideSessionApproval(service, sessionId, a, decision);
      setCardError((m) => { const n = { ...m }; delete n[a.approvalId]; return n; });
    } catch (err) {
      // the failure stays on the originating card — result comes from service
      setCardError((m) => ({ ...m, [a.approvalId]: String(err instanceof Error ? err.message : err) }));
    } finally { setBusy(null); }
  }

  return (
    <div className="task-tray">
      <button className={`topbar-button tray-btn ${approvals.length ? "hot" : ""}`} onClick={() => setOpen((v) => !v)}
        title={t("tray.title")} type="button">
        <Icon name="shield" size={15} />
        {approvals.length > 0 && <span className="tray-badge">{approvals.length}</span>}
      </button>
      {open && (
        <div className="tray-pop">
          <header className="tray-head">{t("tray.pending")} <code>{approvals.length}</code></header>
          {tasks.length > 0 && (
            <>
              <header className="tray-head">{t("tray.live-tasks")} <code>{tasks.length}</code></header>
              {tasks.slice(0, 4).map((tk) => (
                <div className="tray-card" key={tk.taskId}>
                  <div className="tray-card-head"><code>{tk.originPlanet || "sun"}</code><strong>{tk.status}</strong></div>
                  <p>{tk.taskId} · {t("chat.runs", { n: tk.runIds.length })}</p>
                  {tk.runIds[0] && (
                    <div className="tray-actions">
                      <button onClick={() => { openRun(tk.runIds[0]); setOpen(false); }} type="button">→ {tk.runIds[0].replace("run-", "#")}</button>
                    </div>
                  )}
                </div>
              ))}
            </>
          )}
          {approvals.length === 0 && <p className="omni-empty">{t("tray.nothing")}</p>}
          {approvals.map((a) => (
            <div className="tray-card" key={a.approvalId}>
              <div className="tray-card-head"><code>{a.riskLevel}</code><strong>{a.action}</strong></div>
              <p>{a.scope} · {a.runId}</p>
              {cardError[a.approvalId] && <p className="tray-error">{cardError[a.approvalId]}</p>}
              <div className="tray-actions">
                <button disabled={busy === a.approvalId} onClick={() => void decide(a, "approve")} type="button">{t("tray.approve-once")}</button>
                <button className="danger" disabled={busy === a.approvalId} onClick={() => void decide(a, "deny")} type="button">{t("tray.deny")}</button>
                <button onClick={() => { openRun(a.runId); setOpen(false); }} title={t("tray.goto-run")} type="button">→ {t("tray.goto-run")}</button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
