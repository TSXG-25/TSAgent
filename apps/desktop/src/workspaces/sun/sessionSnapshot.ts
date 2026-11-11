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
