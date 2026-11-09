import type {
  ArtifactView,
  EventTone,
  RunEventView,
  RunView,
  StageStatus,
  WorkflowView,
} from "../types";
import type {
  ArtifactSummary,
  ConversationMessageSnapshot,
  RunEvent,
  RunSnapshot,
  StageSnapshot,
  WorkflowSnapshot,
} from "../types/service";

function textValue(value: unknown, fallback: string): string {
  return typeof value === "string" && value.length > 0 ? value : fallback;
}

function toneValue(value: unknown): EventTone {
  if (value === "success" || value === "warning" || value === "error" || value === "neutral") return value;
  return "info";
}

function parseSize(size: string): number {
  const match = size.match(/([\d.]+)\s*(KB|MB|B)?/i);
  if (!match) return 0;
  const value = Number(match[1]);
  const unit = match[2]?.toUpperCase();
  if (unit === "MB") return Math.round(value * 1024 * 1024);
  if (unit === "KB") return Math.round(value * 1024);
  return Math.round(value);
}

function formatSize(size: number): string {
  if (size >= 1024 * 1024) return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  if (size >= 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${size} B`;
}

function digestText(value: string): string {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return `fnv1a-${(hash >>> 0).toString(16).padStart(8, "0")}`;
}

function mapStage(stage: StageSnapshot): StageSnapshot {
  return {
    ...stage,
    tasks: stage.tasks.map((task) => ({ ...task })),
  };
}

function mapWorkflow(workflow: WorkflowSnapshot): WorkflowSnapshot {
  return {
    ...workflow,
    stages: workflow.stages.map(mapStage),
  };
}

export function toServiceEvent(
  event: RunEventView,
  identity: Pick<RunSnapshot, "tenantId" | "sessionId" | "runId">,
  runRevision: number,
  sequenceNumber: number,
): RunEvent {
  return {
    eventId: event.eventId,
    sequenceNumber: event.sequenceNumber ?? sequenceNumber,
    eventType: event.eventType ?? event.type,
    tenantId: identity.tenantId,
    sessionId: identity.sessionId,
    runId: identity.runId,
    runRevision: event.runRevision ?? runRevision,
    timestamp: event.timestamp ?? event.at,
    payload: {
      ...(event.payload ?? {}),
      title: event.title,
      description: event.description,
      tone: event.tone,
    },
  };
}

export function toRunEventView(event: RunEvent): RunEventView {
  return {
    eventId: event.eventId,
    type: event.eventType,
    title: textValue(event.payload.title, event.eventType),
    description: textValue(event.payload.description, "Durable event received.") ,
    at: event.timestamp,
    tone: toneValue(event.payload.tone),
    sequenceNumber: event.sequenceNumber,
    eventType: event.eventType,
    runRevision: event.runRevision,
    timestamp: event.timestamp,
    payload: event.payload,
  };
}

export function toServiceArtifact(
  artifact: ArtifactView,
  run: Pick<RunSnapshot, "runId" | "activeWorkflowId" | "revision" | "updatedAt">,
): ArtifactSummary {
  return {
    artifactId: artifact.artifactId,
    runId: run.runId,
    type: artifact.kind,
    displayName: artifact.path,
    reference: artifact.reference ?? `artifact://${run.runId}/${artifact.artifactId}`,
    digest: artifact.digest ?? digestText(artifact.artifactId),
    size: parseSize(artifact.size),
    exists: artifact.status !== "pending",
    verified: artifact.status === "verified",
    producer: artifact.producer
      ? { stageId: artifact.producer }
      : { workflowId: run.activeWorkflowId, stageId: "implementation" },
    createdRevision: artifact.createdRevision ?? run.revision,
    createdAt: artifact.updatedAt || run.updatedAt,
  };
}

export function toRunView(
  snapshot: RunSnapshot,
  artifacts: ArtifactSummary[],
  events: RunEvent[],
): RunView {
  const verifier = snapshot.verifierSummary ?? {
    status: "waiting" as const,
    checks: "—",
    stdout: "—",
    detail: "No verifier summary is available yet.",
  };

  return {
    runId: snapshot.runId,
    tenantId: snapshot.tenantId,
    requestId: snapshot.requestId,
    revision: snapshot.revision,
    status: snapshot.status,
    request: snapshot.requestText,
    activeWorkflowId: snapshot.activeWorkflowId ?? snapshot.workflows[0]?.workflowId ?? "",
    createdAt: snapshot.createdAt,
    updatedAt: snapshot.updatedAt,
    duration: snapshot.duration ?? "—",
    sessionId: snapshot.sessionId,
    workflows: snapshot.workflows.map(mapWorkflow),
    artifacts: artifacts.map((artifact) => ({
      artifactId: artifact.artifactId,
      path: artifact.displayName,
      kind: artifact.type,
      size: formatSize(artifact.size),
      updatedAt: artifact.createdAt,
      status: artifact.verified ? "verified" : artifact.exists ? "generated" : "pending",
      reference: artifact.reference,
      digest: artifact.digest,
      producer: artifact.producer
        ? [artifact.producer.workflowId, artifact.producer.stageId].filter(Boolean).join(" / ")
        : undefined,
      createdRevision: artifact.createdRevision,
    })),
    events: events.map(toRunEventView),
    conversation: snapshot.conversation.map((message: ConversationMessageSnapshot) => ({ ...message })),
    output: snapshot.output
      ? {
          runId: snapshot.output.runId,
          revision: snapshot.output.revision,
          text: snapshot.output.text,
          evidenceIds: [...snapshot.output.evidenceIds],
          artifactIds: [...snapshot.output.artifactIds],
          createdAt: snapshot.output.createdAt,
        }
      : undefined,
    failure: snapshot.failureSummary ? { ...snapshot.failureSummary } : undefined,
    resume: snapshot.resumeSummary ? { ...snapshot.resumeSummary } : undefined,
    verifier,
  };
}

export function toServiceSnapshot(
  run: RunView,
  identity: { tenantId: string; requestId: string; revision: number },
): RunSnapshot {
  const workflows = run.workflows.map((workflow) => ({
    ...workflow,
    stages: workflow.stages.map((stage) => ({
      ...stage,
      tasks: stage.tasks.map((task) => ({ ...task })),
    })),
  }));

  return {
    tenantId: identity.tenantId,
    sessionId: run.sessionId,
    runId: run.runId,
    requestId: identity.requestId,
    requestText: run.request,
    status: run.status,
    revision: identity.revision,
    createdAt: run.createdAt,
    updatedAt: run.updatedAt,
    duration: run.duration,
    activeWorkflowId: run.activeWorkflowId,
    completedWorkflows: workflows.filter((workflow) => workflow.status === "completed").map((workflow) => workflow.workflowId),
    pendingWorkflows: workflows.filter((workflow) => workflow.status !== "completed").map((workflow) => workflow.workflowId),
    workflows,
    conversation: run.conversation.map((message) => ({ ...message })),
    verifierSummary: { ...run.verifier },
    resumeSummary: run.resume ? { ...run.resume } : undefined,
    failureSummary:
      run.status === "failed"
        ? {
            code: "INTERNAL_ERROR",
            message: run.verifier.detail,
            retryable: false,
          }
        : undefined,
  };
}

/** Fold assistant_note events into the conversation — intermediate-round
 *  narration between tool calls. Each note becomes a durable assistant
 *  message so the thinking chain's Thoughts blocks carry real content. */
export function foldAssistantNotes(
  conversation: RunView["conversation"],
  events: RunEventView[],
): RunView["conversation"] {
  const base = conversation.filter((m) => !m.messageId.startsWith("note:"));
  const notes = events
    .filter((e) => e.eventType === "assistant_note")
    .map((e) => ({
      messageId: `note:${e.eventId}`,
      role: "assistant" as const,
      content: String(
        (e.payload as Record<string, unknown> | undefined)?.text ?? "",
      ),
      at: e.timestamp ?? "",
    }))
    .filter((m) => m.content);
  return [...base, ...notes];
}

/** Fold streamed assistant_delta events into the conversation — the live
 *  reply-in-progress renders as a single assistant message that updates as
 *  deltas arrive.  Finalized messages come from the snapshot; this only adds
 *  the still-streaming turn. */
export function foldLiveDelta(
  conversation: RunView["conversation"],
  events: RunEventView[],
): RunView["conversation"] {
  const text = events
    .filter((e) => e.eventType === "assistant_delta")
    .map((e) => String((e.payload as Record<string, unknown> | undefined)?.delta ?? ""))
    .join("");
  if (!text) return conversation;
  const live = conversation[conversation.length - 1];
  if (live && (live as { streaming?: boolean }).streaming) {
    return [...conversation.slice(0, -1), { ...live, content: text }];
  }
  return [
    ...conversation,
    {
      messageId: "live-delta",
      role: "assistant" as const,
      content: text,
      at: "",
      streaming: true,
    } as RunView["conversation"][number] & { streaming?: boolean },
  ];
}

function stageDuration(start: string | undefined, end: string | undefined): string {
  if (!start || !end) return "";
  const ms = Date.parse(end) - Date.parse(start);
  if (!Number.isFinite(ms) || ms < 0) return "";
  const total = Math.round(ms / 1000);
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

/** Local runs persist runtime `stage_started`/`stage_completed` events but no
 *  workflow snapshots — project one synthetic "runtime" workflow so the
 *  execution surfaces (Mission Control, Sun wfSteps) render real stages. */
export function projectRuntimeWorkflow(events: RunEventView[]): WorkflowView[] {
  const order: string[] = [];
  // Latest start/end per stage by event position — the runtime can re-enter
  // a stage on later goal rounds, so status follows the last boundary.
  const started = new Map<string, number>();
  const completed = new Map<string, number>();
  events.forEach((event, index) => {
    const stageId = typeof event.payload?.stage_id === "string" ? event.payload.stage_id : "";
    if (!stageId) return;
    const isStart = event.eventType === "stage_started";
    const isEnd = event.eventType === "stage_completed";
    if (!isStart && !isEnd) return;
    if (!started.has(stageId) && !completed.has(stageId)) order.push(stageId);
    if (isStart) started.set(stageId, index);
    if (isEnd) completed.set(stageId, index);
  });
  if (order.length === 0) return [];
  const stages = order.map((stageId) => {
    const startIndex = started.get(stageId) ?? -1;
    const endIndex = completed.get(stageId);
    const done = endIndex !== undefined && endIndex > startIndex;
    const status: StageStatus = done ? "completed" : "running";
    const startEvent = startIndex >= 0 ? events[startIndex] : undefined;
    return {
      stageId,
      name: stageId,
      eyebrow: typeof startEvent?.payload?.phase === "string" ? startEvent.payload.phase : "",
      description: "",
      status,
      duration: done && startEvent ? stageDuration(startEvent.timestamp, events[endIndex!].timestamp) : "",
      tasks: [],
    };
  });
  const done = stages.filter((stage) => stage.status === "completed").length;
  return [{
    workflowId: "runtime",
    name: "runtime",
    version: "",
    status: done === stages.length ? "completed" : "active",
    progress: Math.round((done / stages.length) * 100),
    stages,
  }];
}

export function mergeRunEvents(current: RunEventView[], incoming: RunEvent[]): RunEventView[] {
  const byId = new Map(current.map((event) => [event.eventId, event]));
  for (const event of incoming) byId.set(event.eventId, toRunEventView(event));
  return [...byId.values()].sort((left, right) => (left.sequenceNumber ?? 0) - (right.sequenceNumber ?? 0));
}
