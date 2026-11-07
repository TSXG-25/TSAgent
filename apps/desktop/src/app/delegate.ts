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
  attachments: string[] = [],
): Promise<string> {
  const { service, activeRun } = ctx;
  // One live run *per session* — the guard is scoped to the session the
  // composer targets; a live run in another session must not block or
  // mis-anchor this send (service-wire.md §runs/§delegation).
  const sessionId = ctx.sessionId ?? service.identity.sessionId;
  const liveRun = activeRun && activeRun.sessionId === sessionId ? activeRun : null;
  if (liveRun && (liveRun.status === "active" || liveRun.status === "blocked")) {
    throw new Error(`已有进行中的任务（${liveRun.runId}）——等待完成或先暂停再发送`);
  }
  if (!liveRun) {
    await ctx.controller.startRun({
      tenantId: service.identity.tenantId,
      sessionId,
      workspaceRoot: ctx.workspacePath,
      requestId: requestIdForRun(0),
      requestText,
    } as never);
    return "";
  }
  const result = await service.delegateRun({
    tenantId: service.identity.tenantId,
    // the run owns the delegation scope — session switching must not
    // mis-anchor the child task/handoff onto a different session
    sessionId: liveRun.sessionId,
    requestId: requestIdForRun(0),
    parentRunId: liveRun.runId,
    contextRefs: attachments,
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
