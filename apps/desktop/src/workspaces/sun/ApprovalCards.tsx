import { useState } from "react";
import { decideSessionApproval } from "../../app/approvals";
import { t } from "../../app/i18n";
import type { RunCtx } from "../../app/runCtx";
import type { ApprovalSummary } from "../../types/service";

/** Pending approval cards for the current session — action, scope, risk and
 *  params shown; a failed decide stays on the card instead of vanishing. */
export default function ApprovalCards({ ctx, approvals }: { ctx: RunCtx; approvals: ApprovalSummary[] }) {
  const { service, sessionId } = ctx;
  const [apprErr, setApprErr] = useState<Record<string, string>>({});
  const [apprBusy, setApprBusy] = useState<string | null>(null);

  const decide = (a: ApprovalSummary, decision: "approve" | "deny") => async () => {
    setApprBusy(a.approvalId);
    try {
      await decideSessionApproval(service, sessionId, a, decision);
      setApprErr((m) => { const n = { ...m }; delete n[a.approvalId]; return n; });
    } catch (err) {
      setApprErr((m) => ({ ...m, [a.approvalId]: String(err instanceof Error ? err.message : err) }));
    } finally { setApprBusy(null); }
  };

  return (
    <>
      {approvals.map((a) => (
        <section className="omni-taskbar blocked" key={a.approvalId}>
          <div className="appr-detail">
            <span className="omni-warn">⚠ {a.action} — {String(a.params?.path ?? a.scope)}</span>
            <code className="appr-params">{JSON.stringify(a.params ?? {}, null, 1).slice(0, 400)}</code>
            {a.riskLevel && <span className="appr-risk">{t("sun.risk")} {String(a.riskLevel)}</span>}
            {apprErr[a.approvalId] && <p className="appr-error">{apprErr[a.approvalId]}</p>}
          </div>
          <button className="btn primary" disabled={apprBusy === a.approvalId} onClick={decide(a, "approve")} type="button">{t("sun.approve")}</button>
          <button className="btn danger" disabled={apprBusy === a.approvalId} onClick={decide(a, "deny")} type="button">{t("sun.deny")}</button>
        </section>
      ))}
    </>
  );
}
