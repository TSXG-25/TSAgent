import { requestIdForRun } from "./ui";
import type { RunCtx } from "./runCtx";
import type { RunView } from "../types";

function metadataFor(options: { model?: string }): Record<string, unknown> {
  return options.model
    ? { agent_profile: { model_ref: options.model } }
    : {};
}

/** Normal submission — every send is independently admitted as a new task
 *  owned by the target workstation (origin_planet on the implicit task).
 *  No single-active-run limit and no implicit parent/child anchoring on the
 *  session's active Run. Returns the accepted Run's id. */
export async function submitToPlanet(
  ctx: RunCtx,
  targetPlanet: string,
  requestText: string,
  attachments: string[] = [],
  options: { model?: string } = {},
): Promise<string> {
  const view = await ctx.controller.startRun({
    tenantId: ctx.service.identity.tenantId,
    sessionId: ctx.sessionId,
    workspaceRoot: ctx.workspacePath,
    originPlanet: targetPlanet,
    contextRefs: attachments,
    metadata: metadataFor(options),
    requestId: requestIdForRun(0),
    requestText,
  });
  return view.runId;
}

/** Explicit delegation ("继续基于当前任务") — the parent run is a required
 *  parameter so parentage is never guessed from the session's active Run:
 *  create_task (parent task = the delegating run's task) then start_run with
 *  the child task_id, keeping the Task tree + handoff as the durable truth.
 *  Returns the child task id. */
export async function delegateToPlanet(
  ctx: RunCtx,
  parentRun: RunView,
  targetPlanet: string,
  requestText: string,
  attachments: string[] = [],
  options: { model?: string } = {},
): Promise<string> {
  const { service } = ctx;
  const metadata = metadataFor(options);
  const result = await service.delegateRun({
    tenantId: service.identity.tenantId,
    // the parent run owns the delegation scope — session switching must not
    // mis-anchor the child task/handoff onto a different session
    sessionId: parentRun.sessionId,
    requestId: requestIdForRun(0),
    parentRunId: parentRun.runId,
    contextRefs: attachments,
    requestText,
    targetPlanet,
  });
  // The Service's provisioning is authoritative for the child's start —
  // requestText carries verified refs/agent label, workspaceRoot and
  // agentProfile carry the same config spawn_subtask would pass.
  const view = await ctx.controller.startRun({
    tenantId: service.identity.tenantId,
    sessionId: parentRun.sessionId,
    requestId: requestIdForRun(0),
    requestText: result?.requestText ?? requestText,
    taskId: result?.taskId ?? "",
    originPlanet: targetPlanet,
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
  void view;
  return result?.taskId ?? "";
}
