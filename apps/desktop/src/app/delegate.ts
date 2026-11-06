import { requestIdForRun } from "./ui";
import type { RunCtx } from "./runCtx";

/** Sun Omni delegation — the UI-side path for spawn_subtask semantics:
 *  create_task (parent task = the delegating run's task) then start_run with
 *  the child task_id, so the Task tree + handoff record stay the durable
 *  source of truth. Returns the child task id. */
export async function delegateToPlanet(
  ctx: RunCtx,
  targetPlanet: string,
  requestText: string,
): Promise<string> {
  const { service, activeRun } = ctx;
  // One live run per session — sending while one is active would hit
  // RUN_ALREADY_ACTIVE; fail fast with the real reason.
  if (activeRun && (activeRun.status === "active" || activeRun.status === "blocked")) {
    throw new Error(`已有进行中的任务（${activeRun.runId}）——等待完成或先暂停再发送`);
  }
  // No active run means no delegation anchor — a standalone run serves the
  // request directly rather than sending an invalid empty parent_run_id.
  if (!activeRun) {
    await ctx.controller.startRun({
      tenantId: service.identity.tenantId,
      sessionId: service.identity.sessionId,
      requestId: requestIdForRun(0),
      requestText,
    } as never);
    return "";
  }
  const result = await service.delegateRun({
    tenantId: service.identity.tenantId,
    sessionId: service.identity.sessionId,
    requestId: requestIdForRun(0),
    parentRunId: activeRun.runId,
    requestText,
    targetPlanet,
  } as never);
  await ctx.controller.startRun({
    tenantId: service.identity.tenantId,
    sessionId: service.identity.sessionId,
    requestId: requestIdForRun(0),
    requestText,
    ...(result?.taskId ? { taskId: result.taskId } : {}),
  } as never);
  return result?.taskId ?? "";
}
