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
