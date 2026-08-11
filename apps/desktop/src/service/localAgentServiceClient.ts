import {
  AgentServiceClientError,
  type ArtifactSummary,
  type CancelRunRequest,
  type DesktopAgentServiceClient,
  type DesktopIdentity,
  type EventStreamRequest,
  type FailureSummary,
  type GetRunRequest,
  type HealthSnapshot,
  type ListArtifactsRequest,
  type ResumeAction,
  type ResumeRunRequest,
  type ResumeSummary,
  type RunEvent,
  type RunHandle,
  type RunOutputSummary,
  type RunSnapshot,
  type RunStatus,
  type ServiceErrorCode,
  type StartRunRequest,
  type VerifierSummary,
} from "../types/service";
import {
  LocalTransportProtocolError,
  type LocalRpcParams,
  type LocalTransport,
  type LocalTransportMethod,
} from "./localTransport";

export type LocalClientOperation =
  | "get_run"
  | "list_artifacts"
  | "read_events"
  | "cancel_run";

export interface LocalAgentServiceClientOptions {
  /** Identity is explicit configuration; there is no default user fallback. */
  identity: DesktopIdentity;
  requestIdFactory?: (operation: LocalClientOperation, sequence: number) => string;
}

export interface LocalHealthSnapshot {
  status: "ok";
  protocolVersion: string;
  service: string;
}

const RUN_STATUS_MAP: Record<string, RunStatus> = {
  CREATED: "pending",
  RUNNING: "active",
  CANCELLING: "cancelling",
  SUSPENDED: "blocked",
  WAITING_USER: "blocked",
  FAILED_RECOVERABLE: "blocked",
  FAILED_TERMINAL: "failed",
  BLOCKED: "blocked",
  COMPLETED: "completed",
  CANCELLED: "cancelled",
  TIMED_OUT: "timed_out",
};

const RESUME_ACTIONS = new Set<ResumeAction>([
  "RESUME_EXACT",
  "REPLAY_FROM_STAGE",
  "REPLAN_FROM_CHECKPOINT",
  "ABANDON_AND_RESTART",
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requiredString(value: unknown, label: string): string {
  if (typeof value !== "string" || value.trim().length === 0) {
    throw new Error(`${label} is missing or invalid`);
  }
  return value;
}

function optionalString(value: unknown): string | undefined {
  return typeof value === "string" && value.length > 0 ? value : undefined;
}

function numberValue(value: unknown, label: string, minimum = 0): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < minimum) {
    throw new Error(`${label} is missing or invalid`);
  }
  return value;
}

function stringArray(value: unknown, label: string): string[] {
  if (!Array.isArray(value) || value.some((item) => typeof item !== "string")) {
    throw new Error(`${label} is missing or invalid`);
  }
  return [...value];
}

function serviceProjectionError(message: string): AgentServiceClientError {
  void message;
  return new AgentServiceClientError({
    code: "INTERNAL_ERROR",
    message: "AgentService returned an invalid public DTO",
    retryable: false,
  });
}

function mapStatus(value: unknown): RunStatus {
  if (typeof value !== "string" || !RUN_STATUS_MAP[value]) {
    throw new Error("status is missing or unsupported");
  }
  return RUN_STATUS_MAP[value];
}

function mapResumeAction(value: unknown): ResumeAction | null {
  if (value === null || value === undefined) return null;
  if (typeof value !== "string" || !RESUME_ACTIONS.has(value as ResumeAction)) {
    throw new Error("resume action is unsupported");
  }
  return value as ResumeAction;
}

function mapRunHandle(value: unknown): RunHandle {
  const raw = isRecord(value) ? value : (() => { throw new Error("RunHandle is not an object"); })();
  const createdAt = requiredString(raw.created_at, "created_at");
  return {
    tenantId: requiredString(raw.tenant_id, "tenant_id"),
    sessionId: requiredString(raw.session_id, "session_id"),
    runId: requiredString(raw.run_id, "run_id"),
    requestId: requiredString(raw.request_id, "request_id"),
    status: mapStatus(raw.status),
    revision: numberValue(raw.revision, "revision"),
    createdAt,
    updatedAt: optionalString(raw.updated_at) ?? createdAt,
  };
}

function mapVerifierSummary(value: unknown): VerifierSummary | undefined {
  if (value === null || value === undefined) return undefined;
  const raw = isRecord(value) ? value : (() => { throw new Error("verifier_summary is not an object"); })();
  const status = raw.status === "verified" || raw.status === "failed" ? raw.status : "waiting";
  return {
    status,
    checks: typeof raw.checks === "string" ? raw.checks : "—",
    stdout: typeof raw.stdout === "string" ? raw.stdout : "—",
    detail: typeof raw.detail === "string" ? raw.detail : "No verifier summary is available yet.",
  };
}

function mapResumeSummary(value: unknown, status: RunStatus): ResumeSummary | undefined {
  if (value === null || value === undefined) return undefined;
  const raw = isRecord(value) ? value : (() => { throw new Error("resume_summary is not an object"); })();
  const action = mapResumeAction(raw.action);
  const reasonCode = typeof raw.reason_code === "string" ? raw.reason_code : "UNKNOWN";
  const summary = typeof raw.summary === "string" ? raw.summary : reasonCode;
  return {
    checkpointId: optionalString(raw.checkpoint_id) ?? "",
    action,
    reason: summary,
    sourceStage: optionalString(raw.source_stage) ?? "",
    ...(optionalString(raw.resumed_at) ? { resumedAt: raw.resumed_at as string } : {}),
    outcome: status === "completed" ? "completed" : "ready",
  };
}

function mapFailureSummary(value: unknown): FailureSummary | undefined {
  if (value === null || value === undefined) return undefined;
  const raw = isRecord(value) ? value : (() => { throw new Error("failure_summary is not an object"); })();
  return {
    code: requiredString(raw.code, "failure code"),
    message: typeof raw.message === "string" ? raw.message : "Run failed",
    retryable: raw.retryable === true,
  };
}

function mapOutput(value: unknown): RunOutputSummary | undefined {
  if (value === null || value === undefined) return undefined;
  const raw = isRecord(value) ? value : (() => { throw new Error("output is not an object"); })();
  return {
    runId: requiredString(raw.run_id, "output.run_id"),
    revision: numberValue(raw.revision, "output.revision"),
    text: requiredString(raw.text, "output.text"),
    evidenceIds: stringArray(raw.evidence_ids ?? [], "output.evidence_ids"),
    artifactIds: stringArray(raw.artifact_ids ?? [], "output.artifact_ids"),
    createdAt: requiredString(raw.created_at, "output.created_at"),
  };
}

function mapArtifact(value: unknown, fallbackRunId: string): ArtifactSummary {
  const raw = isRecord(value) ? value : (() => { throw new Error("artifact is not an object"); })();
  const artifactType = requiredString(raw.artifact_type, "artifact_type");
  const type = artifactType === "python" || artifactType === "json" ? artifactType : "text";
  const reference = requiredString(raw.reference, "artifact.reference");
  return {
    artifactId: requiredString(raw.artifact_id, "artifact_id"),
    runId: optionalString(raw.run_id) ?? fallbackRunId,
    type,
    displayName: optionalString(raw.display_name) ?? reference,
    reference,
    digest: typeof raw.digest === "string" ? raw.digest : "",
    size: raw.size === null || raw.size === undefined ? 0 : numberValue(raw.size, "artifact.size"),
    exists: raw.exists === true,
    verified: raw.verified === true,
    producer:
      optionalString(raw.producer_workflow_id) || optionalString(raw.producer_stage_id)
        ? {
            ...(optionalString(raw.producer_workflow_id)
              ? { workflowId: raw.producer_workflow_id as string }
              : {}),
            ...(optionalString(raw.producer_stage_id)
              ? { stageId: raw.producer_stage_id as string }
              : {}),
          }
        : undefined,
    createdRevision: numberValue(raw.created_revision ?? 0, "artifact.created_revision"),
    createdAt: typeof raw.created_at === "string" ? raw.created_at : "",
  };
}

function mapSnapshot(value: unknown): RunSnapshot {
  const raw = isRecord(value) ? value : (() => { throw new Error("RunSnapshot is not an object"); })();
  const status = mapStatus(raw.status);
  const runId = requiredString(raw.run_id, "run_id");
  const artifacts = Array.isArray(raw.artifacts)
    ? raw.artifacts.map((item) => mapArtifact(item, runId))
    : undefined;
  return {
    tenantId: requiredString(raw.tenant_id, "tenant_id"),
    sessionId: requiredString(raw.session_id, "session_id"),
    runId,
    requestId: requiredString(raw.request_id, "request_id"),
    requestText: typeof raw.request_text === "string" ? raw.request_text : "",
    status,
    revision: numberValue(raw.revision ?? 0, "revision"),
    createdAt: requiredString(raw.created_at, "created_at"),
    updatedAt: requiredString(raw.updated_at, "updated_at"),
    ...(optionalString(raw.active_workflow_id)
      ? { activeWorkflowId: raw.active_workflow_id as string }
      : {}),
    completedWorkflows: stringArray(raw.completed_workflow_ids ?? [], "completed_workflow_ids"),
    pendingWorkflows: stringArray(raw.pending_workflow_ids ?? [], "pending_workflow_ids"),
    workflows: [],
    conversation: [],
    ...(artifacts ? { artifacts } : {}),
    output: mapOutput(raw.output),
    verifierSummary: mapVerifierSummary(raw.verifier_summary),
    resumeSummary: mapResumeSummary(raw.resume_summary, status),
    failureSummary: mapFailureSummary(raw.failure_summary),
  };
}

function mapEvent(value: unknown): RunEvent {
  const raw = isRecord(value) ? value : (() => { throw new Error("RunEvent is not an object"); })();
  const payload = raw.payload === undefined ? {} : isRecord(raw.payload) ? raw.payload : (() => { throw new Error("event.payload is not an object"); })();
  return {
    eventId: requiredString(raw.event_id, "event_id"),
    sequenceNumber: numberValue(raw.sequence_number, "sequence_number", 1),
    eventType: requiredString(raw.event_type, "event_type"),
    tenantId: requiredString(raw.tenant_id, "tenant_id"),
    sessionId: requiredString(raw.session_id, "session_id"),
    runId: requiredString(raw.run_id, "run_id"),
    ...(optionalString(raw.workflow_id) ? { workflowId: raw.workflow_id as string } : {}),
    ...(optionalString(raw.stage_id) ? { stageId: raw.stage_id as string } : {}),
    ...(optionalString(raw.task_id) ? { taskId: raw.task_id as string } : {}),
    runRevision: numberValue(raw.run_revision ?? 0, "run_revision"),
    timestamp: requiredString(raw.timestamp, "timestamp"),
    payload: { ...payload },
  };
}

function mapEvents(value: unknown, afterSequence: number): RunEvent[] {
  if (!Array.isArray(value)) throw new Error("read_events result is not an array");
  const byId = new Map<string, RunEvent>();
  for (const item of value) {
    const event = mapEvent(item);
    if (event.sequenceNumber <= afterSequence) {
      throw new Error("event violates exclusive cursor");
    }
    const previous = byId.get(event.eventId);
    if (previous && previous.sequenceNumber !== event.sequenceNumber) {
      throw new Error("duplicate event ID has conflicting sequence number");
    }
    byId.set(event.eventId, event);
  }
  return [...byId.values()].sort((left, right) => left.sequenceNumber - right.sequenceNumber);
}

function mapHealth(value: unknown): HealthSnapshot {
  const raw = isRecord(value) ? value : (() => { throw new Error("health result is not an object"); })();
  if (raw.status !== "ok") throw new Error("health status is not ok");
  return {
    status: "ok",
    protocolVersion: requiredString(raw.protocol_version, "protocol_version"),
    service: requiredString(raw.service, "service"),
  };
}

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

export class LocalAgentServiceClient implements DesktopAgentServiceClient {
  readonly mode = "local" as const;
  readonly identity: DesktopIdentity;
  private requestSequence = 0;
  private readonly knownSnapshots = new Map<string, RunSnapshot>();

  constructor(
    private readonly transport: LocalTransport,
    private readonly options: LocalAgentServiceClientOptions,
  ) {
    if (!options.identity.tenantId.trim() || !options.identity.userId.trim() || !options.identity.sessionId.trim()) {
      throw new Error("LocalAgentServiceClient requires an explicit tenant, user, and session identity");
    }
    this.identity = { ...options.identity };
  }

  async health(): Promise<LocalHealthSnapshot> {
    return mapHealth(await this.call("health", {}));
  }

  async ready(): Promise<void> {
    await this.health();
  }

  /**
   * The local transport has no list_runs wire method yet. This catalog is
   * deliberately limited to snapshots observed by this client instance; it
   * never reads SQLite or reconstructs durable history in the UI layer.
   */
  async listRuns(): Promise<RunSnapshot[]> {
    return [...this.knownSnapshots.values()].map((snapshot) => clone(snapshot));
  }

  async startRun(request: StartRunRequest): Promise<RunHandle> {
    this.assertIdentity(request);
    const params: LocalRpcParams = {
      tenant_id: request.tenantId,
      user_id: this.identity.userId,
      session_id: request.sessionId,
      request_id: request.requestId,
      request_text: request.requestText,
      metadata: request.requestDigest ? { desktop_request_digest: request.requestDigest } : {},
    };
    return this.project(async () => mapRunHandle(await this.call("start_run", params)));
  }

  async getRun(request: GetRunRequest): Promise<RunSnapshot> {
    const params = this.lookupParams(request, "get_run");
    return this.project(async () => {
      const snapshot = mapSnapshot(await this.call("get_run", params));
      this.rememberSnapshot(snapshot);
      return snapshot;
    });
  }

  async cancelRun(request: CancelRunRequest): Promise<RunSnapshot> {
    const params: LocalRpcParams = {
      ...this.lookupParams(request, "cancel_run", request.requestId),
      requested_by: request.requestedBy,
    };
    return this.project(async () => {
      const snapshot = mapSnapshot(await this.call("cancel_run", params));
      this.rememberSnapshot(snapshot);
      return snapshot;
    });
  }

  async resumeRun(request: ResumeRunRequest): Promise<RunHandle> {
    this.assertIdentity(request);
    const params: LocalRpcParams = {
      tenant_id: request.tenantId,
      user_id: this.identity.userId,
      session_id: request.sessionId,
      run_id: request.runId,
      request_id: request.resumeRequestId,
      request_text: "",
      checkpoint_id: request.checkpointId,
      action: request.action,
    };
    return this.project(async () => mapRunHandle(await this.call("resume_run", params)));
  }

  async listArtifacts(request: ListArtifactsRequest): Promise<ArtifactSummary[]> {
    const params = this.lookupParams(request, "list_artifacts");
    return this.project(async () => {
      const value = await this.call("list_artifacts", params);
      if (!Array.isArray(value)) throw new Error("list_artifacts result is not an array");
      return value.map((item) => mapArtifact(item, request.runId));
    });
  }

  async readEvents(request: EventStreamRequest): Promise<RunEvent[]> {
    if (!Number.isInteger(request.afterSequence) || request.afterSequence < -1) {
      throw new AgentServiceClientError({
        code: "CURSOR_INVALID",
        message: "event cursor is invalid",
        retryable: false,
        runId: request.runId,
      });
    }
    if (request.limit !== undefined && (!Number.isInteger(request.limit) || request.limit < 1)) {
      throw new AgentServiceClientError({
        code: "INVALID_REQUEST",
        message: "event limit is invalid",
        retryable: false,
        runId: request.runId,
      });
    }
    const afterSequence = Math.max(0, request.afterSequence);
    const params = {
      ...this.lookupParams(request, "read_events"),
      after_sequence: afterSequence,
      ...(request.limit === undefined ? {} : { limit: request.limit }),
    };
    return this.project(async () => mapEvents(await this.call("read_events", params), afterSequence));
  }

  async shutdown(): Promise<void> {
    try {
      await this.call("shutdown", {});
    } finally {
      await this.transport.close();
    }
  }

  async close(): Promise<void> {
    await this.transport.close();
  }

  private lookupParams(
    request: { tenantId: string; sessionId: string; runId: string },
    operation: LocalClientOperation,
    requestId = this.nextRequestId(operation),
  ): LocalRpcParams {
    this.assertIdentity(request);
    return {
      tenant_id: request.tenantId,
      user_id: this.identity.userId,
      session_id: request.sessionId,
      run_id: request.runId,
      request_id: requestId,
    };
  }

  private nextRequestId(operation: LocalClientOperation): string {
    const sequence = ++this.requestSequence;
    const generated = this.options.requestIdFactory?.(operation, sequence);
    if (generated !== undefined) {
      if (!generated.trim()) throw new Error("requestIdFactory returned an invalid ID");
      return generated;
    }
    const uuid = globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${sequence}`;
    return `desktop-${operation}-${uuid}`;
  }

  private assertIdentity(request: { tenantId: string; sessionId: string }): void {
    if (request.tenantId !== this.identity.tenantId || request.sessionId !== this.identity.sessionId) {
      throw new AgentServiceClientError({
        code: "IDENTITY_MISMATCH",
        message: "request identity does not match the local desktop session",
        retryable: false,
      });
    }
  }

  private rememberSnapshot(snapshot: RunSnapshot): void {
    this.knownSnapshots.set(snapshot.runId, clone(snapshot));
  }

  private async call(method: LocalTransportMethod, params: LocalRpcParams): Promise<unknown> {
    try {
      return await this.transport.request<LocalRpcParams, unknown>(method, params);
    } catch (error) {
      if (error instanceof AgentServiceClientError) throw error;
      if (error instanceof LocalTransportProtocolError) {
        throw new AgentServiceClientError({
          code: "INTERNAL_ERROR",
          message: "local transport protocol error",
          retryable: false,
        });
      }
      throw new AgentServiceClientError({
        code: "PROVIDER_UNAVAILABLE",
        message: "local AgentService is unavailable",
        retryable: true,
      });
    }
  }

  private async project<T>(factory: () => Promise<T>): Promise<T> {
    try {
      return await factory();
    } catch (error) {
      if (error instanceof AgentServiceClientError) throw error;
      throw serviceProjectionError(error instanceof Error ? error.message : "unknown DTO error");
    }
  }
}

export default LocalAgentServiceClient;
