import type {
  ArtifactView,
  EventTone,
  RunEventView,
  RunView,
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
    digest: artifact.digest ?? digestText(artifact.content),
    size: parseSize(artifact.size) || artifact.content.length,
    exists: artifact.status !== "pending",
    verified: artifact.status === "verified",
    producer: artifact.producer
      ? { stageId: artifact.producer }
      : { workflowId: run.activeWorkflowId, stageId: "implementation" },
    createdRevision: artifact.createdRevision ?? run.revision,
    createdAt: artifact.updatedAt || run.updatedAt,
    preview: artifact.content,
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
      content: artifact.preview ?? "Preview unavailable. Artifact content is not part of this MVP.",
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

export function mergeRunEvents(current: RunEventView[], incoming: RunEvent[]): RunEventView[] {
  const byId = new Map(current.map((event) => [event.eventId, event]));
  for (const event of incoming) byId.set(event.eventId, toRunEventView(event));
  return [...byId.values()].sort((left, right) => (left.sequenceNumber ?? 0) - (right.sequenceNumber ?? 0));
}
