import type {
  ApprovalSummary,
  DesktopAgentServiceClient,
  ParticipantSummary,
  SessionSummary,
} from "../../types/service";
import { requestIdForRun } from "../../app/ui";

export interface SessionSnapshot {
  /** The session these scoped results were fetched for — the caller drops
   *  the snapshot when the selected session has since changed. */
  sessionId: string;
  sessions: SessionSummary[];
  approvals: ApprovalSummary[];
  participants: ParticipantSummary[];
}

/** A11: one refresh reads ONE session.  Approvals and participants must be
 *  queried with the selected session — never the client's mutable identity
 *  session — or a single refresh mixes two sessions' data. */
export async function fetchSessionSnapshot(
  service: DesktopAgentServiceClient,
  sessionId: string,
): Promise<SessionSnapshot> {
  const [sessions, approvals, participants] = await Promise.all([
    service.listSessions({
      tenantId: service.identity.tenantId,
      userId: service.identity.userId,
      requestId: requestIdForRun(0),
    }),
    service.listApprovals({
      tenantId: service.identity.tenantId,
      sessionId,
      requestId: requestIdForRun(0),
      status: "PENDING",
    }),
    service.listParticipants({
      tenantId: service.identity.tenantId,
      sessionId,
      requestId: requestIdForRun(0),
    }).catch(() => [] as ParticipantSummary[]),
  ]);
  return { sessionId, sessions, approvals, participants };
}

/** Issues a sequence for each refresh — only the most recently issued
 *  sequence may commit, and close() invalidates everything outstanding.
 *  The session check alone cannot order A→B→A: the first A's late response
 *  would otherwise satisfy the same-session comparison. */
export class RefreshGate {
  private latest = 0;
  private closed = false;

  begin(): number {
    return this.closed ? -1 : ++this.latest;
  }

  isCurrent(seq: number): boolean {
    return !this.closed && seq === this.latest;
  }

  close(): void {
    this.closed = true;
  }
}
