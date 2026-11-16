import type { DesktopAgentServiceClient } from "../types/service";

/** Resolve a session's durable workspace binding to an absolute path.
 *  "" means unbound — the backend then runs against its factory root and
 *  callers must not substitute cwd, last-used, or Omni's directory. */
export async function resolveBoundPath(
  service: DesktopAgentServiceClient,
  sessionId: string,
): Promise<string> {
  const tenantId = service.identity.tenantId;
  const userId = service.identity.userId;
  const [sessions, workspaces] = await Promise.all([
    service.listSessions({ tenantId, userId, requestId: `wsp-s-${Date.now()}` }),
    service.listWorkspaces({ tenantId, sessionId, requestId: `wsp-w-${Date.now()}` }),
  ]);
  const workspaceId = sessions.find((s) => s.sessionId === sessionId)?.workspaceId ?? "";
  return workspaces.find((w) => w.workspaceId === workspaceId)?.path ?? "";
}

/** Create a fresh planet-claimed session for a workstation "new task",
 *  carrying over the source session's user-bound workspace — a project
 *  the user deliberately bound stays the record's root; app-managed
 *  `ws-auto-*` roots are per-session and resolve fresh for the new
 *  session. Only files the facts; adopting the session (selectSession)
 *  is the caller's decision after the send is admitted. */
export async function createRecordSession(
  service: DesktopAgentServiceClient,
  planet: string,
  sourceSessionId: string,
): Promise<string> {
  const tenantId = service.identity.tenantId;
  const current = await service.resolveSessionWorkspace({
    tenantId,
    sessionId: sourceSessionId,
  });
  const session = await service.createSession({
    tenantId,
    userId: service.identity.userId,
    sessionId: `session-${planet}-${crypto.randomUUID().slice(0, 8)}`,
    requestId: `rail-new-${planet}-${Date.now()}`,
    planet,
  });
  if (current.bound && !current.workspaceId.startsWith("ws-auto-")) {
    await service.assignSessionWorkspace({
      tenantId,
      sessionId: session.sessionId,
      requestId: `rail-as-${Date.now()}`,
      workspaceId: current.workspaceId,
    });
  }
  return session.sessionId;
}

/** Omni visibility — records belong to the workstation that filed them;
 *  a session surfaces on Omni only while it is unclaimed or claimed by
 *  Omni itself. Cross-workstation linkage still happens through explicit
 *  @-delegation: its derived child session stays unclaimed and therefore
 *  remains Omni-visible. */
export const isOmniSession = (s: { planet?: string }): boolean => !s.planet || s.planet === "sun";
