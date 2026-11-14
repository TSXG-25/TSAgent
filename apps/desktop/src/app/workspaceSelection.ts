import { useSyncExternalStore } from "react";
import type { RunCtx } from "./runCtx";
import type { RunView } from "../types";

/** Per-workstation selected Run — keyed by tenant/session/planet so each
 *  workspace keeps its own current task independently of the global
 *  Omni selection. */
const selections = new Map<string, string>();
const listeners = new Set<() => void>();

function key(tenantId: string, sessionId: string, planet: string): string {
  return `${tenantId}:${sessionId}:${planet}`;
}

export function selectWorkspaceRun(
  tenantId: string,
  sessionId: string,
  planet: string,
  runId: string,
): void {
  const k = key(tenantId, sessionId, planet);
  if (selections.get(k) === runId) return;
  selections.set(k, runId);
  for (const listener of listeners) listener();
}

export function getWorkspaceRunId(
  tenantId: string,
  sessionId: string,
  planet: string,
): string {
  return selections.get(key(tenantId, sessionId, planet)) ?? "";
}

export function useWorkspaceRunId(
  tenantId: string,
  sessionId: string,
  planet: string,
): string {
  return useSyncExternalStore(
    (listener) => {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    () => getWorkspaceRunId(tenantId, sessionId, planet),
  );
}

/** Runs owned by one workstation in the current session — partitioned by the
 *  durable origin_planet on the run's task, never by the current route. Runs
 *  without origin evidence are unclassified and surface only in Omni. */
export function workspaceRuns(ctx: RunCtx, planet: string): RunView[] {
  return ctx.runs.filter(
    (run) => run.sessionId === ctx.sessionId && run.originPlanet === planet,
  );
}

/** Current task of one workstation — its own selection, falling back to the
 *  scope's most recent run (ctx.runs is newest-first). */
export function useWorkspaceCurrentRun(ctx: RunCtx, planet: string): RunView | null {
  const selId = useWorkspaceRunId(
    ctx.service.identity.tenantId,
    ctx.sessionId,
    planet,
  );
  const mine = workspaceRuns(ctx, planet);
  return mine.find((run) => run.runId === selId) ?? mine[0] ?? null;
}
