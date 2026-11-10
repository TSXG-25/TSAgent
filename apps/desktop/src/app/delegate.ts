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
  options: { model?: string } = {},
): Promise<string> {
  const { service, activeRun } = ctx;
  const metadata = options.model
    ? { agent_profile: { model_ref: options.model } }
    : {};
  // One live run *per session* — the guard is scoped to the session the
  // composer targets; a live run in another session must not block or
  // mis-anchor this send (service-wire.md §runs/§delegation).
  const sessionId = ctx.sessionId;
  const liveRun = activeRun && activeRun.sessionId === sessionId ? activeRun : null;
  if (liveRun && liveRun.status === "active") {
    throw new Error(`已有进行中的任务（${liveRun.runId}）——等待完成或先暂停再发送`);
  }
  // A blocked run is parked awaiting resolution and must not deadlock the
  // session — a new send starts a fresh run rather than anchoring on it.
  if (!liveRun || liveRun.status === "blocked") {
    await ctx.controller.startRun({
      tenantId: service.identity.tenantId,
      sessionId,
      workspaceRoot: ctx.workspacePath,
      contextRefs: attachments,
      metadata,
      requestId: requestIdForRun(0),
      requestText,
    });
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
  });
  // The Service's provisioning is authoritative for the child's start —
  // requestText carries verified refs/agent label, workspaceRoot and
  // agentProfile carry the same config spawn_subtask would pass.
  await ctx.controller.startRun({
    tenantId: service.identity.tenantId,
    sessionId: liveRun.sessionId,
    requestId: requestIdForRun(0),
    requestText: result?.requestText ?? requestText,
    taskId: result?.taskId ?? "",
    workspaceRoot: result?.workspaceRoot,
    contextRefs: attachments,
    metadata: {
      ...metadata,
      delegation_depth: 1,
      agent_profile: {
        ...((metadata.agent_profile as Record<string, unknown>) ?? {}),
        ...(result?.agentProfile ?? {}),
      },
    },
  });
  return result?.taskId ?? "";
}
