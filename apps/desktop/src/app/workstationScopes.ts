/**
 * Per-workstation scope store: each planet owns an independently persisted
 * current session (`tenant:user → planet → sessionId`). The mapping only
 * remembers *which* session each workstation is viewing — workstation
 * ownership of the session itself is the durable `planet` field on the
 * session head, claimed through `create_session`.
 */
const key = (tenantId: string, userId: string) => `tsagent:planet-sessions:${tenantId}:${userId}`;

export function loadPlanetSessions(tenantId: string, userId: string): Record<string, string> {
  try {
    const raw = localStorage.getItem(key(tenantId, userId));
    const value = raw ? JSON.parse(raw) : {};
    return value && typeof value === "object" ? value as Record<string, string> : {};
  } catch {
    return {};
  }
}

export function savePlanetSessions(tenantId: string, userId: string, sessions: Record<string, string>): void {
  try {
    localStorage.setItem(key(tenantId, userId), JSON.stringify(sessions));
  } catch { /* storage unavailable — scopes remain in memory */ }
}
