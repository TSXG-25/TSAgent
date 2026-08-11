import type {
  ArtifactKind,
  ConversationRole,
  EventTone,
  RunStatus,
  StageStatus,
} from "../types";

export type { ArtifactKind, ConversationRole, EventTone, RunStatus, StageStatus };

/**
 * Public DTOs for the desktop boundary described by ADR-0021.
 *
 * These types intentionally contain no Runtime, Orchestrator, SQLite, or
 * workspace-path concepts. A future LocalAgentServiceClient can implement
 * the same interface without changing the UI.
 */

export interface StartRunRequest {
  tenantId: string;
  sessionId: string;
  requestId: string;
  requestText: string;
  requestDigest?: string;
}

export interface GetRunRequest {
  tenantId: string;
  sessionId: string;
  runId: string;
}

export interface CancelRunRequest {
  tenantId: string;
  sessionId: string;
  runId: string;
  requestId: string;
  requestedBy: string;
}

export type ResumeAction = "RESUME_EXACT" | "REPLAY_FROM_STAGE";

export interface ResumeRunRequest {
  tenantId: string;
  sessionId: string;
  runId: string;
  resumeRequestId: string;
  checkpointId: string;
  action: ResumeAction;
}

export interface ListArtifactsRequest {
  tenantId: string;
  sessionId: string;
  runId: string;
}

export interface EventStreamRequest {
  tenantId: string;
  sessionId: string;
  runId: string;
  /** Exclusive cursor: only sequence_number > afterSequence is returned. */
  afterSequence: number;
  limit?: number;
}

export interface RunHandle {
  tenantId: string;
  sessionId: string;
  runId: string;
  requestId: string;
  status: RunStatus;
  revision: number;
  createdAt: string;
  updatedAt: string;
}

export interface TaskSnapshot {
  taskId: string;
  title: string;
  status: StageStatus;
}

export interface StageSnapshot {
  stageId: string;
  name: string;
  eyebrow: string;
  description: string;
  status: StageStatus;
  duration: string;
  tasks: TaskSnapshot[];
}

export interface WorkflowSnapshot {
  workflowId: string;
  name: string;
  version: string;
  status: RunStatus;
  progress: number;
  stages: StageSnapshot[];
}

export interface ConversationMessageSnapshot {
  messageId: string;
  role: ConversationRole;
  content: string;
  at: string;
}

export interface VerifierSummary {
  status: "waiting" | "verified" | "failed";
  checks: string;
  stdout: string;
  detail: string;
}

export interface ResumeSummary {
  checkpointId: string;
  action: ResumeAction;
  reason: string;
  sourceStage: string;
  resumedAt?: string;
  outcome: "ready" | "completed";
}

export interface FailureSummary {
  code: string;
  message: string;
  retryable: boolean;
}

export interface ArtifactProducer {
  workflowId?: string;
  stageId?: string;
}

export interface ArtifactSummary {
  artifactId: string;
  runId: string;
  type: ArtifactKind;
  displayName: string;
  /** Opaque service reference. It is not a client-supplied filesystem path. */
  reference: string;
  digest: string;
  size: number;
  exists: boolean;
  verified: boolean;
  producer?: ArtifactProducer;
  createdRevision: number;
  createdAt: string;
  /** Mock-only preview; content reads are intentionally not part of this MVP. */
  preview?: string;
}

export interface RunEvent {
  eventId: string;
  sequenceNumber: number;
  eventType: string;
  tenantId: string;
  sessionId: string;
  runId: string;
  workflowId?: string;
  stageId?: string;
  taskId?: string;
  runRevision: number;
  timestamp: string;
  payload: Record<string, unknown>;
}

export interface RunSnapshot {
  tenantId: string;
  sessionId: string;
  runId: string;
  requestId: string;
  requestText: string;
  status: RunStatus;
  revision: number;
  createdAt: string;
  updatedAt: string;
  duration?: string;
  activeWorkflowId?: string;
  completedWorkflows: string[];
  pendingWorkflows: string[];
  workflows: WorkflowSnapshot[];
  conversation: ConversationMessageSnapshot[];
  verifierSummary?: VerifierSummary;
  resumeSummary?: ResumeSummary;
  failureSummary?: FailureSummary;
}

export type ServiceErrorCode =
  | "INVALID_REQUEST"
  | "IDENTITY_MISMATCH"
  | "RUN_NOT_FOUND"
  | "RUN_ALREADY_ACTIVE"
  | "RUN_ALREADY_CANCELLING"
  | "ALREADY_CANCELLED"
  | "RUN_NOT_CANCELLABLE"
  | "ALREADY_COMPLETED"
  | "RESUME_NOT_ALLOWED"
  | "IDEMPOTENCY_CONFLICT"
  | "EVENT_CURSOR_EXPIRED"
  | "CURSOR_INVALID"
  | "STORE_BUSY"
  | "PROVIDER_UNAVAILABLE"
  | "INTERNAL_ERROR";

export interface ServiceErrorDTO {
  code: ServiceErrorCode;
  message: string;
  retryable: boolean;
  runId?: string;
  requestId?: string;
  details?: Record<string, string>;
}

export class AgentServiceClientError extends Error {
  readonly code: ServiceErrorCode;
  readonly retryable: boolean;
  readonly runId?: string;
  readonly requestId?: string;
  readonly details?: Record<string, string>;

  constructor(error: ServiceErrorDTO) {
    super(error.message);
    this.name = "AgentServiceClientError";
    this.code = error.code;
    this.retryable = error.retryable;
    this.runId = error.runId;
    this.requestId = error.requestId;
    this.details = error.details;
  }

  toDTO(): ServiceErrorDTO {
    return {
      code: this.code,
      message: this.message,
      retryable: this.retryable,
      ...(this.runId ? { runId: this.runId } : {}),
      ...(this.requestId ? { requestId: this.requestId } : {}),
      ...(this.details ? { details: this.details } : {}),
    };
  }
}

/** The five methods map directly to the frozen AgentService contract. */
export interface AgentServiceClient {
  startRun(request: StartRunRequest): Promise<RunHandle>;
  getRun(request: GetRunRequest): Promise<RunSnapshot>;
  cancelRun(request: CancelRunRequest): Promise<RunSnapshot>;
  resumeRun(request: ResumeRunRequest): Promise<RunHandle>;
  listArtifacts(request: ListArtifactsRequest): Promise<ArtifactSummary[]>;
  readEvents(request: EventStreamRequest): Promise<RunEvent[]>;
}

/** Run List is a desktop catalog capability; the core contract remains above. */
export interface RunCatalogClient {
  listRuns(): Promise<RunSnapshot[]>;
}

export type DesktopAgentServiceClient = AgentServiceClient & RunCatalogClient;
