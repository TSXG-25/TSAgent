import { createRecordSession } from "./sessionWorkspace";
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
  options: { model?: string; sessionId?: string } = {},
): Promise<string> {
  // No explicit workspaceRoot — admission resolves the session's durable
  // binding itself, so a stale ctx path can never become the run root.
  // options.sessionId overrides the context session — the workstation
  // "new task" flow creates its session inside the send itself, after
  // this ctx snapshot was taken.
  const prevActiveId = ctx.activeRun?.runId ?? "";
  const view = await ctx.controller.startRun({
    sessionId: options.sessionId ?? ctx.sessionId,
    originPlanet: targetPlanet,
    contextRefs: attachments,
    metadata: metadataFor(options),
    requestText,
  });
  // startRun claims the session-global activeRun for whatever it just
  // created — that is Omni's view. A workstation submission owns only its
  // per-planet rail selection; restore the previous global selection so
  // sending on Mars never hijacks what Omni's chat shows. Omni-aimed
  // submissions keep the new run selected as before.
  if (targetPlanet !== "sun" && prevActiveId) ctx.controller.selectRun(prevActiveId);
  return view.runId;
}

/** Workstation "new task" send — files the submission on a freshly
 *  created planet-claimed session (inheriting the current workspace
 *  binding), then adopts it. Ordering is the contract: the record is
 *  created and bound first but never selected; only a successful
 *  admission selects it. A failed send leaves the current session, the
 *  home view and the session-keyed draft exactly as they were. */
export async function sendNewRecord(
  ctx: RunCtx,
  railPlanet: string,
  targetPlanet: string,
  requestText: string,
  attachments: string[] = [],
  options: { model?: string } = {},
): Promise<{ runId: string; sessionId: string }> {
  const sessionId = await createRecordSession(ctx.service, railPlanet, ctx.sessionId);
  const runId = await submitToPlanet(ctx, targetPlanet, requestText, attachments, {
    ...options,
    sessionId,
  });
  ctx.selectSession(sessionId);
  return { runId, sessionId };
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
    // the parent run owns the delegation scope — session switching must not
    // mis-anchor the child task/handoff onto a different session
    sessionId: parentRun.sessionId,
    parentRunId: parentRun.runId,
    contextRefs: attachments,
    requestText,
    targetPlanet,
  });
  // The Service's provisioning is authoritative for the child's start —
  // requestText carries verified refs/agent label, workspaceRoot and
  // agentProfile carry the same config spawn_subtask would pass.
  const view = await ctx.controller.startRun({
    sessionId: parentRun.sessionId,
    requestText: result?.requestText ?? requestText,
    taskId: result?.taskId ?? "",
    originPlanet: targetPlanet,
    workspaceRoot: result?.workspaceRoot,
    // artifact: refs validated against the parent are already embedded in
    // the enriched requestText/handoff — start_run rejects that scheme.
    contextRefs: attachments.filter((r) => !r.startsWith("artifact:")),
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

/** Omni → workstation delegation ("在 Omni 发任务，Mars 执行并回来汇报").
 *  Same durable chain as delegateToPlanet — child Task + Handoff + child Run
 *  in this session — anchored under the active run's task when one exists,
 *  else under a fresh Omni-side Task created on the spot. Returns the child
 *  run id so Omni can display progress/result inline. */
export async function delegateFromOmni(
  ctx: RunCtx,
  targetPlanet: string,
  requestText: string,
  attachments: string[] = [],
  options: { model?: string } = {},
): Promise<{ taskId: string; runId: string }> {
  const { service } = ctx;
  const metadata = metadataFor(options);
  // "继续" anchors only on a run owned by this composer's session — the
  // global activeRun is a cross-scope selection that survives session
  // switches, so it must never pull the delegation scope (or its project)
  // onto another session. Anything else files under a fresh Omni task in
  // the current session.
  const parentRun = ctx.activeRun?.sessionId === ctx.sessionId ? ctx.activeRun : null;
  let parentTaskId = "";
  if (!parentRun) {
    const parent = await service.createTask({
      sessionId: ctx.sessionId,
      originPlanet: "sun",
    });
    parentTaskId = parent.taskId;
  }
  const result = await service.delegateRun({
    sessionId: ctx.sessionId,
    parentRunId: parentRun?.runId ?? "",
    parentTaskId,
    contextRefs: attachments,
    requestText,
    targetPlanet,
  });
  const view = await ctx.controller.startRun({
    sessionId: ctx.sessionId,
    requestText: result?.requestText ?? requestText,
    taskId: result?.taskId ?? "",
    originPlanet: targetPlanet,
    workspaceRoot: result?.workspaceRoot,
    contextRefs: attachments.filter((r) => !r.startsWith("artifact:")),
    metadata: {
      ...metadata,
      delegation_depth: 1,
      agent_profile: {
        ...((metadata.agent_profile as Record<string, unknown>) ?? {}),
        ...(result?.agentProfile ?? {}),
      },
    },
  });
  return { taskId: result?.taskId ?? "", runId: view.runId };
}
