import { useEffect, useState } from "react";
import type { ApprovalSummary, DesktopAgentServiceClient } from "../types/service";
import { requestIdForRun } from "./ui";

/** One session approval state shared by TaskTray and workspace approval
 *  cards — a single pending-approval poll per session and a single decide
 *  entrypoint; consumers do not keep their own copies. */

interface Entry {
  approvals: ApprovalSummary[];
  subs: Set<() => void>;
  timer: ReturnType<typeof setInterval> | null;
  service: DesktopAgentServiceClient;
  sessionId: string;
}

const entries = new Map<string, Entry>();

const key = (service: DesktopAgentServiceClient, sessionId: string) =>
  `${service.identity.tenantId}${sessionId}`;

async function poll(e: Entry) {
  try {
    e.approvals = await e.service.listApprovals({
      tenantId: e.service.identity.tenantId,
      sessionId: e.sessionId,
      requestId: requestIdForRun(0),
      status: "PENDING",
    });
    e.subs.forEach((fn) => fn());
  } catch { /* keep last snapshot */ }
}

function ensure(service: DesktopAgentServiceClient, sessionId: string): Entry {
  const k = key(service, sessionId);
  let e = entries.get(k);
  if (!e) {
    e = { approvals: [], subs: new Set(), timer: null, service, sessionId };
    entries.set(k, e);
    void poll(e);
    e.timer = setInterval(() => void poll(e!), 8_000);
  }
  return e;
}

/** Decide one approval through the service; the shared store refreshes on
 *  success and the error propagates so the originating card can show it. */
export async function decideSessionApproval(
  service: DesktopAgentServiceClient,
  sessionId: string,
  a: ApprovalSummary,
  decision: "approve" | "deny",
): Promise<void> {
  await service.decideApproval({
    tenantId: service.identity.tenantId,
    sessionId,
    requestId: requestIdForRun(0),
    runId: a.runId,
    approvalId: a.approvalId,
    decision,
    decidedBy: service.identity.userId,
  });
  const e = entries.get(key(service, sessionId));
  if (e) await poll(e);
}

/** Current cached snapshot for a session store (subscribing starts it). */
export function getSessionApprovals(service: DesktopAgentServiceClient, sessionId: string): ApprovalSummary[] {
  return entries.get(key(service, sessionId))?.approvals ?? [];
}

/** Subscribe outside React — returns an unsubscribe that also tears the
 *  shared poller down when the last consumer leaves. */
export function subscribeSessionApprovals(
  service: DesktopAgentServiceClient,
  sessionId: string,
  fn: () => void,
): () => void {
  const e = ensure(service, sessionId);
  e.subs.add(fn);
  fn();
  return () => {
    e.subs.delete(fn);
    if (e.subs.size === 0) {
      if (e.timer) clearInterval(e.timer);
      entries.delete(key(service, sessionId));
    }
  };
}

export function useSessionApprovals(service: DesktopAgentServiceClient, sessionId: string): ApprovalSummary[] {
  const [approvals, setApprovals] = useState<ApprovalSummary[]>([]);
  useEffect(() => {
    const e = ensure(service, sessionId);
    return subscribeSessionApprovals(service, sessionId, () => setApprovals([...e.approvals]));
  }, [service, sessionId]);
  return approvals;
}
