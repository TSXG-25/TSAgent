import { useCallback, useEffect, useState } from "react";
import type { RunCtx } from "../../app/runCtx";
import { requestIdForRun } from "../../app/ui";
import type { ApprovalSummary, HandoffSummary, ParticipantSummary, SessionSummary, TaskRecord } from "../../types/service";

/** §5.1 Sun orchestration data — sessions, task tree, handoffs, approvals,
 *  participants. Consumed by the right rail and the orbit strip. */
export function useOrchestration(ctx: RunCtx) {
  const { service } = ctx;
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [tasks, setTasks] = useState<TaskRecord[]>([]);
  const [handoffs, setHandoffs] = useState<HandoffSummary[]>([]);
  const [approvals, setApprovals] = useState<ApprovalSummary[]>([]);
  const [participants, setParticipants] = useState<ParticipantSummary[]>([]);
  const [busy, setBusy] = useState<string | null>(null);

  const req = useCallback(
    () => ({
      tenantId: service.identity.tenantId,
      sessionId: service.identity.sessionId,
      requestId: requestIdForRun(0),
    }),
    [service],
  );

  const refresh = useCallback(async () => {
    try {
      const [s, t, h, a, p] = await Promise.all([
        service.listSessions(req() as never),
        service.listTasks(),
        service.listHandoffs(req() as never),
        service.listApprovals({ ...req(), status: "PENDING" } as never),
        service.listParticipants(req() as never) as Promise<ParticipantSummary[]>,
      ]);
      setSessions(s); setTasks(t); setHandoffs(h); setApprovals(a); setParticipants(p);
    } catch { /* service unavailable — panels stay empty */ }
  }, [service, req]);

  useEffect(() => {
    void refresh();
    const t = window.setInterval(() => void refresh(), 10_000);
    return () => window.clearInterval(t);
  }, [refresh]);

  const decide = useCallback(async (approval: ApprovalSummary, decision: "approve" | "deny") => {
    setBusy(approval.approvalId);
    try {
      await service.decideApproval({
        ...req(), runId: approval.runId, approvalId: approval.approvalId,
        decision, decidedBy: service.identity.userId,
      } as never);
      await refresh();
    } finally {
      setBusy(null);
    }
  }, [service, req, refresh]);

  return { sessions, tasks, handoffs, approvals, participants, busy, decide, refresh };
}
