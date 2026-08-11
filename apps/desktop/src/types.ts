export type RunStatus =
  | "pending"
  | "active"
  | "cancelling"
  | "cancelled"
  | "timed_out"
  | "completed"
  | "failed"
  | "blocked";

export type StageStatus =
  | "queued"
  | "running"
  | "completed"
  | "interrupted"
  | "verified"
  | "failed";

export type EventTone = "info" | "success" | "warning" | "error" | "neutral";

export type ConversationRole = "user" | "assistant" | "system";

export type ArtifactKind = "python" | "json" | "text";

export interface TaskView {
  taskId: string;
  title: string;
  status: StageStatus;
}

export interface StageView {
  stageId: string;
  name: string;
  eyebrow: string;
  description: string;
  status: StageStatus;
  duration: string;
  tasks: TaskView[];
}

export interface WorkflowView {
  workflowId: string;
  name: string;
  version: string;
  status: RunStatus;
  progress: number;
  stages: StageView[];
}

export interface ArtifactView {
  artifactId: string;
  path: string;
  kind: ArtifactKind;
  size: string;
  updatedAt: string;
  status: "generated" | "verified" | "pending";
  content: string;
  /** Opaque service reference; the UI never treats this as a local path. */
  reference?: string;
  digest?: string;
  producer?: string;
  createdRevision?: number;
}

export interface RunEventView {
  eventId: string;
  type: string;
  title: string;
  description: string;
  at: string;
  tone: EventTone;
  /** Contract metadata used for cursor-aware rendering and replay diagnostics. */
  sequenceNumber?: number;
  eventType?: string;
  runRevision?: number;
  timestamp?: string;
}

export interface ResumeView {
  checkpointId: string;
  action: "RESUME_EXACT" | "REPLAY_FROM_STAGE";
  reason: string;
  sourceStage: string;
  resumedAt?: string;
  outcome: "ready" | "completed";
}

export interface ConversationMessageView {
  messageId: string;
  role: ConversationRole;
  content: string;
  at: string;
}

export interface VerifierView {
  status: "waiting" | "verified" | "failed";
  checks: string;
  stdout: string;
  detail: string;
}

export interface RunView {
  runId: string;
  tenantId?: string;
  requestId?: string;
  revision?: number;
  status: RunStatus;
  request: string;
  activeWorkflowId: string;
  createdAt: string;
  updatedAt: string;
  duration: string;
  sessionId: string;
  workflows: WorkflowView[];
  artifacts: ArtifactView[];
  events: RunEventView[];
  conversation: ConversationMessageView[];
  resume?: ResumeView;
  verifier: VerifierView;
}
