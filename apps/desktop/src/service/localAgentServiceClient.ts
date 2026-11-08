import {
  AgentServiceClientError,
  type ApprovalSummary,
  type ArtifactSummary,
  type CancelRunRequest,
  type CreateScheduleRequest,
  type CreateSessionRequest,
  type GetSettingsRequest,
  type CreateTaskRequest,
  type DesktopAgentServiceClient,
  type DesktopIdentity,
  type EventStreamRequest,
  type FailureSummary,
  type GetRunRequest,
  type HealthSnapshot,
  type ListArtifactsRequest,
  type ResumeAction,
  type PauseRunRequest,
  type ResumeRunRequest,
  type ResumeSummary,
  type RunEvent,
  type RunHandle,
  type RunOutputSummary,
  type RunSnapshot,
  type RunStatus,
  type ServiceErrorCode,
  type SessionSummary,
  type HandoffSummary,
  type HistoryEntry,
  type ProfileSummary,
  type ScheduleSummary,
  type SettingsSummary,
  type UpdateScheduleRequest,
  type UpdateSettingsRequest,
  type UpsertWorkspaceRequest,
  type ListWorkspacesRequest,
  type AssignSessionWorkspaceRequest,
  type DeleteWorkspaceRequest,
  type DirectoryListing,

  type WorkspaceSummary,
  type UpsertProfileRequest,
  type DecideApprovalRequest,
  type ListApprovalsRequest,
  type ListHandoffsRequest,
  type ListHistoryRequest,
  type ListProfilesRequest,
  type ListCapabilitiesRequest,
  type CapabilitySummary,
  type WebSearchRequest,
  type WebSearchResult,
  type ListFactsRequest,
  type ListSkillsRequest,
  type FactRecordSummary,
  type FactForgetResult,
  type ForgetFactRequest,
  type ListParticipantsRequest,
  type ParticipantSummary,
  type SearchArtifactsRequest,
  type JupiterArtifactSummary,
  type ListWorkflowsRequest,
  type WorkflowDefinitionSummary,
  type WorkflowGraphSummary,
  type UpsertWorkflowGraphRequest,
  type ListWorkflowGraphsRequest,
  type GraphRunStepSummary,
  type RunWorkflowGraphRequest,
  type MotifSummary,
  type CreateMotifRequest,
  type AssignMotifRequest,
  type ResolveMotifRequest,
  type ListMotifsRequest,
  type EvaluationSummary,
  type RecordEvaluationRequest,
  type ListEvaluationsRequest,
  type ListWorkspaceFilesRequest,
  type WorkspaceFileSummary,
  type WorkspaceFileContent,
  type WorkspaceFileRequest,
  type SkillSummary,
  type DelegateRunRequest,
  type DelegateRunResult,
  type ListSchedulesRequest,
  type ListSessionsRequest,
  type StartRunRequest,
  type TaskHandle,
  type TaskLookupRequest,
  type TaskRecord,
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
  | "get_task"
  | "list_artifacts"
  | "read_events"
  | "cancel_run"
  | "pause_run";

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

function mapMotif(value: unknown): MotifSummary {
  const raw = isRecord(value) ? value : (() => { throw new Error("motif is not an object"); })();
  return {
    motifId: requiredString(raw.motif_id, "motif_id"),
    text: optionalString(raw.text) ?? "",
    source: optionalString(raw.source) ?? "",
    status: optionalString(raw.status) ?? "",
    assignedTaskId: optionalString(raw.assigned_task_id) ?? "",
    resolutionRunId: optionalString(raw.resolution_run_id) ?? "",
    resolution: optionalString(raw.resolution) ?? "",
    createdAt: optionalString(raw.created_at) ?? "",
    updatedAt: optionalString(raw.updated_at) ?? "",
  };
}

function mapWorkflowGraph(value: unknown): WorkflowGraphSummary {
  const raw = isRecord(value) ? value : (() => { throw new Error("workflow graph is not an object"); })();
  return {
    graphId: requiredString(raw.graph_id, "graph_id"),
    name: optionalString(raw.name) ?? "",
    definitionJson: optionalString(raw.definition_json) ?? "{}",
    owner: optionalString(raw.owner) ?? "",
    createdAt: optionalString(raw.created_at) ?? "",
    updatedAt: optionalString(raw.updated_at) ?? "",
  };
}

function mapEvaluation(value: unknown): EvaluationSummary {
  const raw = isRecord(value) ? value : (() => { throw new Error("evaluation is not an object"); })();
  return {
    evalId: requiredString(raw.eval_id, "eval_id"),
    name: optionalString(raw.name) ?? "",
    runId: optionalString(raw.run_id) ?? "",
    baselineRef: optionalString(raw.baseline_ref) ?? "",
    candidateRef: optionalString(raw.candidate_ref) ?? "",
    score: Number(raw.score ?? 0),
    verdict: optionalString(raw.verdict) ?? "neutral",
    notes: optionalString(raw.notes) ?? "",
    createdAt: optionalString(raw.created_at) ?? "",
  };
}

function mapWorkspace(value: unknown): WorkspaceSummary {
  const raw = isRecord(value) ? value : (() => { throw new Error("workspace is not an object"); })();
  return {
    workspaceId: requiredString(raw.workspace_id, "workspace_id"),
    displayName: optionalString(raw.display_name) ?? "",
    capabilities: Array.isArray(raw.capabilities) ? raw.capabilities.map(String) : [],
    defaultProfileId: optionalString(raw.default_profile_id) ?? "",
    description: optionalString(raw.description) ?? "",
    updatedAt: optionalString(raw.updated_at) ?? "",
    path: optionalString(raw.path) ?? "",
    memberCount: Number(raw.member_count ?? 0),
  };
}

function mapSettings(value: unknown): SettingsSummary {
  const raw = isRecord(value) ? value : (() => { throw new Error("settings is not an object"); })();
  return {
    providerMode: optionalString(raw.provider_mode) ?? "",
    activeProvider: optionalString(raw.active_provider) ?? "",
    activeModel: optionalString(raw.active_model) ?? "",
    deepseekModel: optionalString(raw.deepseek_model) ?? "",
    ollamaModel: optionalString(raw.ollama_model) ?? "",
  };
}

function mapProfile(value: unknown): ProfileSummary {
  const raw = isRecord(value) ? value : (() => { throw new Error("profile is not an object"); })();
  return {
    profileId: requiredString(raw.profile_id, "profile_id"),
    displayName: optionalString(raw.display_name) ?? "",
    planetAffinity: optionalString(raw.planet_affinity) ?? "",
    modelRef: optionalString(raw.model_ref) ?? "",
    skillRefs: Array.isArray(raw.skill_refs) ? raw.skill_refs.map(String) : [],
    permissionPolicy: isRecord(raw.permission_policy) ? raw.permission_policy : {},
    updatedAt: optionalString(raw.updated_at) ?? "",
  };
}

function mapSchedule(value: unknown): ScheduleSummary {
  const raw = isRecord(value) ? value : (() => { throw new Error("schedule is not an object"); })();
  return {
    scheduleId: requiredString(raw.schedule_id, "schedule_id"),
    sessionId: requiredString(raw.session_id, "session_id"),
    requestText: optionalString(raw.request_text) ?? "",
    userId: optionalString(raw.user_id) ?? "",
    autoEval: Boolean(raw.auto_eval),
    targetPlanet: optionalString(raw.target_planet) ?? "",
    intervalSeconds: Number(raw.interval_seconds ?? 0),
    status: optionalString(raw.status) ?? "ACTIVE",
    nextFireAt: optionalString(raw.next_fire_at) ?? "",
    lastFiredAt: optionalString(raw.last_fired_at) ?? "",
    lastRunId: optionalString(raw.last_run_id) ?? "",
    fireCount: Number(raw.fire_count ?? 0),
    createdAt: optionalString(raw.created_at) ?? "",
  };
}

function mapHistory(value: unknown): HistoryEntry {
  const raw = isRecord(value) ? value : (() => { throw new Error("history entry is not an object"); })();
  return {
    runId: requiredString(raw.run_id, "run_id"),
    taskId: optionalString(raw.task_id) ?? "",
    requestText: optionalString(raw.request_text) ?? "",
    answerText: optionalString(raw.answer_text) ?? "",
    status: optionalString(raw.status) ?? "",
    updatedAt: optionalString(raw.updated_at) ?? "",
  };
}

function mapHandoff(value: unknown): HandoffSummary {
  const raw = isRecord(value) ? value : (() => { throw new Error("handoff is not an object"); })();
  return {
    handoffId: requiredString(raw.handoff_id, "handoff_id"),
    sourceRunId: optionalString(raw.source_run_id) ?? "",
    sourceTaskId: optionalString(raw.source_task_id) ?? "",
    targetTaskId: optionalString(raw.target_task_id) ?? "",
    targetRunId: optionalString(raw.target_run_id) ?? "",
    targetPlanet: optionalString(raw.target_planet) ?? "",
    contextRefs: Array.isArray(raw.context_refs) ? raw.context_refs.map(String) : [],
    approvalState: optionalString(raw.approval_state) ?? "not_required",
    createdAt: requiredString(raw.created_at, "created_at"),
  };
}

function mapSession(value: unknown): SessionSummary {
  const raw = isRecord(value) ? value : (() => { throw new Error("session is not an object"); })();
  return {
    tenantId: requiredString(raw.tenant_id, "tenant_id"),
    sessionId: requiredString(raw.session_id, "session_id"),
    userId: optionalString(raw.user_id) ?? "",
    createdAt: requiredString(raw.created_at, "created_at"),
    runCount: Number(raw.run_count ?? 0),
    latestRunId: optionalString(raw.latest_run_id) ?? "",
    latestStatus: optionalString(raw.latest_status) ?? "",
    latestUpdatedAt: optionalString(raw.latest_updated_at) ?? "",
    workspaceId: optionalString(raw.workspace_id) ?? "",
  };
}

function mapApproval(value: unknown): ApprovalSummary {
  const raw = isRecord(value) ? value : (() => { throw new Error("approval is not an object"); })();
  return {
    approvalId: requiredString(raw.approval_id, "approval_id"),
    runId: requiredString(raw.run_id, "run_id"),
    action: requiredString(raw.action, "action"),
    scope: optionalString(raw.scope) ?? "",
    params: isRecord(raw.params) ? raw.params : {},
    riskLevel: optionalString(raw.risk_level) ?? "R1",
    status: (optionalString(raw.status) ?? "PENDING") as ApprovalSummary["status"],
    decidedBy: optionalString(raw.decided_by) ?? "",
    decidedAt: optionalString(raw.decided_at) ?? "",
    createdAt: requiredString(raw.created_at, "created_at"),
  };
}

function mapTaskHandle(value: unknown): TaskHandle {
  const raw = isRecord(value) ? value : (() => { throw new Error("task handle is not an object"); })();
  return {
    tenantId: requiredString(raw.tenant_id, "tenant_id"),
    sessionId: requiredString(raw.session_id, "session_id"),
    taskId: requiredString(raw.task_id, "task_id"),
    parentTaskId: optionalString(raw.parent_task_id) ?? "",
    originPlanet: optionalString(raw.origin_planet) ?? "",
    ownerProfileId: optionalString(raw.owner_profile_id) ?? "",
    createdAt: requiredString(raw.created_at, "created_at"),
  };
}

function mapTaskRecord(value: unknown): TaskRecord {
  const raw = isRecord(value) ? value : (() => { throw new Error("task snapshot is not an object"); })();
  return {
    ...mapTaskHandle(raw),
    status: optionalString(raw.status) ?? "CREATED",
    runIds: Array.isArray(raw.run_ids) ? raw.run_ids.map(String) : [],
    childTaskIds: Array.isArray(raw.child_task_ids) ? raw.child_task_ids.map(String) : [],
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
  identity: DesktopIdentity;

  setSession(sessionId: string): void {
    this.identity = { ...this.identity, sessionId };
  }
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

  async listRuns(): Promise<RunSnapshot[]> {
    const params: LocalRpcParams = {
      tenant_id: this.identity.tenantId,
      user_id: this.identity.userId,
      session_id: this.identity.sessionId,
      request_id: this.nextRequestId("get_run"),
      limit: 100,
    };
    return this.project(async () => {
      const result = (await this.call("list_runs", params)) as unknown;
      const items = Array.isArray(result) ? result : ((result as { runs?: unknown[] }).runs ?? []);
      const snapshots = items.map((value) => mapSnapshot(value));
      for (const snapshot of snapshots) {
        this.rememberSnapshot(snapshot);
      }
      return snapshots;
    });
  }

  async createTask(request: CreateTaskRequest): Promise<TaskHandle> {
    const params: LocalRpcParams = {
      tenant_id: request.tenantId,
      user_id: this.identity.userId,
      session_id: request.sessionId,
      request_id: request.requestId,
      task_id: request.taskId ?? "",
      parent_task_id: request.parentTaskId ?? "",
      origin_planet: request.originPlanet ?? "",
      owner_profile_id: request.ownerProfileId ?? "",
    };
    return mapTaskHandle(await this.call("create_task", params));
  }

  async listTasks(): Promise<TaskRecord[]> {
    const params: LocalRpcParams = {
      tenant_id: this.identity.tenantId,
      user_id: this.identity.userId,
      session_id: this.identity.sessionId,
      request_id: this.nextRequestId("get_task"),
    };
    const result = (await this.call("list_tasks", params)) as unknown[];
    return result.map((value) => mapTaskRecord(value));
  }

  async getTask(request: TaskLookupRequest): Promise<TaskRecord> {
    const params: LocalRpcParams = {
      tenant_id: request.tenantId,
      user_id: this.identity.userId,
      session_id: request.sessionId,
      request_id: request.requestId,
      task_id: request.taskId,
    };
    return mapTaskRecord(await this.call("get_task", params));
  }

  async listApprovals(request: ListApprovalsRequest): Promise<ApprovalSummary[]> {
    const params: LocalRpcParams = {
      tenant_id: request.tenantId,
      user_id: this.identity.userId,
      session_id: request.sessionId,
      request_id: request.requestId,
      run_id: request.runId ?? "",
      status: request.status ?? "",
      limit: request.limit ?? 200,
    };
    const result = (await this.call("list_approvals", params)) as unknown[];
    return result.map((value) => mapApproval(value));
  }

  async decideApproval(request: DecideApprovalRequest): Promise<ApprovalSummary> {
    const params: LocalRpcParams = {
      tenant_id: request.tenantId,
      user_id: this.identity.userId,
      session_id: request.sessionId,
      request_id: request.requestId,
      run_id: request.runId,
      approval_id: request.approvalId,
      decision: request.decision,
      decided_by: request.decidedBy ?? this.identity.userId,
    };
    return mapApproval(await this.call("decide_approval", params));
  }

  async upsertWorkspace(request: UpsertWorkspaceRequest): Promise<WorkspaceSummary> {
    return mapWorkspace(await this.call("upsert_workspace", {
      tenant_id: request.tenantId, user_id: this.identity.userId,
      session_id: request.sessionId, request_id: request.requestId,
      workspace_id: request.workspaceId,
      display_name: request.displayName ?? "",
      path: request.path ?? "",
      capabilities: request.capabilities ?? [],
      default_profile_id: request.defaultProfileId ?? "",
      description: request.description ?? "",
    }));
  }

  async listWorkspaces(request: ListWorkspacesRequest): Promise<WorkspaceSummary[]> {
    const result = (await this.call("list_workspaces", {
      tenant_id: request.tenantId, user_id: this.identity.userId,
      session_id: request.sessionId, request_id: request.requestId,
      limit: request.limit ?? 50,
    })) as unknown[];
    return result.map((value) => mapWorkspace(value));
  }

  async updateSettings(request: UpdateSettingsRequest): Promise<SettingsSummary> {
    return mapSettings(await this.call("update_settings", {
      tenant_id: request.tenantId, user_id: this.identity.userId,
      session_id: request.sessionId, request_id: request.requestId,
      provider_mode: request.providerMode ?? "",
      deepseek_model: request.deepseekModel ?? "",
      ollama_model: request.ollamaModel ?? "",
    }));
  }

  async getSettings(request: GetSettingsRequest): Promise<SettingsSummary> {
    return mapSettings(await this.call("get_settings", {
      tenant_id: request.tenantId, user_id: this.identity.userId,
      session_id: request.sessionId, request_id: request.requestId,
    }));
  }

  async upsertProfile(request: UpsertProfileRequest): Promise<ProfileSummary> {
    return mapProfile(await this.call("upsert_profile", {
      tenant_id: request.tenantId, user_id: this.identity.userId,
      session_id: request.sessionId, request_id: request.requestId,
      profile_id: request.profileId,
      display_name: request.displayName ?? "",
      planet_affinity: request.planetAffinity ?? "",
      model_ref: request.modelRef ?? "",
      skill_refs: request.skillRefs ?? [],
      permission_policy: request.permissionPolicy ?? {},
    }));
  }

  async listProfiles(request: ListProfilesRequest): Promise<ProfileSummary[]> {
    const result = (await this.call("list_profiles", {
      tenant_id: request.tenantId, user_id: this.identity.userId,
      session_id: request.sessionId, request_id: request.requestId,
      limit: request.limit ?? 200,
    })) as unknown[];
    return result.map((value) => mapProfile(value));
  }

  async createSchedule(request: CreateScheduleRequest): Promise<ScheduleSummary> {
    return mapSchedule(await this.call("create_schedule", {
      tenant_id: request.tenantId, user_id: this.identity.userId,
      session_id: request.sessionId, request_id: request.requestId,
      request_text: request.requestText,
      interval_seconds: request.intervalSeconds,
      target_planet: request.targetPlanet ?? "",
      auto_eval: request.autoEval ?? false,
      schedule_id: request.scheduleId ?? "",
    }));
  }

  async listSchedules(request: ListSchedulesRequest): Promise<ScheduleSummary[]> {
    const result = (await this.call("list_schedules", {
      tenant_id: request.tenantId, user_id: this.identity.userId,
      session_id: request.sessionId, request_id: request.requestId,
      limit: request.limit ?? 200,
    })) as unknown[];
    return result.map((value) => mapSchedule(value));
  }

  async updateSchedule(request: UpdateScheduleRequest): Promise<ScheduleSummary> {
    return mapSchedule(await this.call("update_schedule", {
      tenant_id: request.tenantId, user_id: this.identity.userId,
      session_id: request.sessionId, request_id: request.requestId,
      schedule_id: request.scheduleId, status: request.status,
    }));
  }

  async listCapabilities(request: ListCapabilitiesRequest): Promise<CapabilitySummary[]> {
    return ((await this.call("list_capabilities", {
      tenant_id: request.tenantId,
      session_id: request.sessionId,
      request_id: request.requestId,
    })) as Array<{ capability?: unknown; tools?: unknown }>).map((raw) => ({
      capability: requiredString(raw.capability, "capability"),
      tools: Array.isArray(raw.tools) ? raw.tools.map(String) : [],
    }));
  }

  async webSearch(request: WebSearchRequest): Promise<WebSearchResult> {
    const raw = await this.call("web_search", {
      tenant_id: request.tenantId,
      session_id: request.sessionId,
      request_id: request.requestId,
      query: request.query,
      max_results: request.maxResults ?? 8,
    }) as { provider?: unknown; content?: unknown; sources?: unknown };
    return {
      provider: optionalString(raw.provider) ?? "",
      content: optionalString(raw.content) ?? "",
      sources: Array.isArray(raw.sources) ? raw.sources as Array<Record<string, unknown>> : [],
    };
  }

  async recordEvaluation(request: RecordEvaluationRequest): Promise<EvaluationSummary> {
    const raw = (await this.call("record_evaluation", {
      tenant_id: request.tenantId, user_id: this.identity.userId,
      session_id: request.sessionId, request_id: request.requestId,
      name: request.name ?? "", run_id: request.runId ?? "",
      baseline_ref: request.baselineRef ?? "",
      candidate_ref: request.candidateRef ?? "",
      score: request.score ?? 0, verdict: request.verdict ?? "neutral",
      notes: request.notes ?? "", eval_id: request.evalId ?? "",
    })) as Record<string, unknown>;
    return mapEvaluation(raw);
  }

  async listEvaluations(request: ListEvaluationsRequest): Promise<EvaluationSummary[]> {
    const result = (await this.call("list_evaluations", {
      tenant_id: request.tenantId, user_id: this.identity.userId,
      session_id: request.sessionId, request_id: request.requestId,
      all_sessions: request.allSessions ?? false,
      limit: request.limit ?? 200,
    })) as Array<Record<string, unknown>>;
    return result.map((v) => mapEvaluation(v));
  }

  async listWorkspaceFiles(request: ListWorkspaceFilesRequest): Promise<WorkspaceFileSummary[]> {
    const result = (await this.call("list_workspace_files", {
      tenant_id: request.tenantId, user_id: this.identity.userId,
      session_id: request.sessionId, request_id: request.requestId,
      limit: request.limit ?? 500,
    })) as Array<Record<string, unknown>>;
    return result.map((raw) => ({
      path: requiredString(raw.path, "path"),
      size: Number(raw.size ?? 0),
      modifiedAt: optionalString(raw.modified_at) ?? "",
    }));
  }

  async readWorkspaceFile(request: WorkspaceFileRequest): Promise<WorkspaceFileContent> {
    const raw = (await this.call("read_workspace_file", {
      tenant_id: request.tenantId, user_id: this.identity.userId,
      session_id: request.sessionId, request_id: request.requestId,
      path: request.path,
    })) as Record<string, unknown>;
    return {
      path: requiredString(raw.path, "path"),
      text: optionalString(raw.text) ?? "",
    };
  }

  async writeWorkspaceFile(request: WorkspaceFileRequest): Promise<WorkspaceFileContent> {
    const raw = (await this.call("write_workspace_file", {
      tenant_id: request.tenantId, user_id: this.identity.userId,
      session_id: request.sessionId, request_id: request.requestId,
      path: request.path, content: request.content ?? "",
    })) as Record<string, unknown>;
    return {
      path: requiredString(raw.path, "path"),
      text: optionalString(raw.text) ?? "",
    };
  }

  async upsertWorkflowGraph(request: UpsertWorkflowGraphRequest): Promise<WorkflowGraphSummary> {
    const raw = (await this.call("upsert_workflow_graph", {
      tenant_id: request.tenantId, user_id: this.identity.userId,
      session_id: request.sessionId, request_id: request.requestId,
      graph_id: request.graphId, name: request.name ?? "",
      definition: request.definition ?? {},
      owner: request.owner ?? this.identity.userId,
    })) as Record<string, unknown>;
    return mapWorkflowGraph(raw);
  }

  async createMotif(request: CreateMotifRequest): Promise<MotifSummary> {
    const raw = (await this.call("create_motif", {
      tenant_id: request.tenantId, user_id: this.identity.userId,
      session_id: request.sessionId, request_id: request.requestId,
      text: request.text, motif_id: request.motifId ?? "",
      source: request.source ?? "user",
    })) as Record<string, unknown>;
    return mapMotif(raw);
  }

  async assignMotif(request: AssignMotifRequest): Promise<DelegateRunResult> {
    return this.call("assign_motif", {
      tenant_id: request.tenantId, user_id: this.identity.userId,
      session_id: request.sessionId, request_id: request.requestId,
      motif_id: request.motifId, parent_run_id: request.parentRunId,
    }) as Promise<DelegateRunResult>;
  }

  async resolveMotif(request: ResolveMotifRequest): Promise<MotifSummary[]> {
    const result = (await this.call("resolve_motif", {
      tenant_id: request.tenantId, user_id: this.identity.userId,
      session_id: request.sessionId, request_id: request.requestId,
      motif_id: request.motifId, resolution_run_id: request.resolutionRunId,
      resolution: request.resolution,
    })) as Array<Record<string, unknown>>;
    return result.map((v) => mapMotif(v));
  }

  async listMotifs(request: ListMotifsRequest): Promise<MotifSummary[]> {
    const result = (await this.call("list_motifs", {
      tenant_id: request.tenantId, user_id: this.identity.userId,
      session_id: request.sessionId, request_id: request.requestId,
      status: request.status ?? "", limit: request.limit ?? 100,
    })) as Array<Record<string, unknown>>;
    return result.map((v) => mapMotif(v));
  }

  async runGraph(request: RunWorkflowGraphRequest): Promise<GraphRunStepSummary[]> {
    const result = (await this.call("run_graph", {
      tenant_id: request.tenantId, user_id: this.identity.userId,
      session_id: request.sessionId, request_id: request.requestId,
      graph_id: request.graphId, parent_run_id: request.parentRunId ?? "",
    })) as Array<Record<string, unknown>>;
    return result.map((raw) => ({
      nodeId: requiredString(raw.node_id, "node_id"),
      taskId: requiredString(raw.task_id, "task_id"),
      runId: requiredString(raw.run_id, "run_id"),
      sessionId: requiredString(raw.session_id, "session_id"),
    }));
  }

  async listWorkflowGraphs(request: ListWorkflowGraphsRequest): Promise<WorkflowGraphSummary[]> {
    const result = (await this.call("list_workflow_graphs", {
      tenant_id: request.tenantId, user_id: this.identity.userId,
      session_id: request.sessionId, request_id: request.requestId,
      limit: request.limit ?? 200,
    })) as Array<Record<string, unknown>>;
    return result.map((v) => mapWorkflowGraph(v));
  }

  async listWorkflows(request: ListWorkflowsRequest): Promise<WorkflowDefinitionSummary[]> {
    const result = (await this.call("list_workflows", {
      tenant_id: request.tenantId, user_id: this.identity.userId,
      session_id: request.sessionId, request_id: request.requestId,
    })) as Array<Record<string, unknown>>;
    return result.map((raw) => ({
      workflowId: requiredString(raw.workflow_id, "workflow_id"),
      description: optionalString(raw.description) ?? "",
      version: optionalString(raw.version) ?? "",
    }));
  }

  async searchArtifacts(request: SearchArtifactsRequest): Promise<JupiterArtifactSummary[]> {
    const result = (await this.call("search_artifacts", {
      tenant_id: request.tenantId, user_id: this.identity.userId,
      session_id: request.sessionId, request_id: request.requestId,
      query: request.query ?? "",
      all_sessions: request.allSessions ?? false,
      limit: request.limit ?? 200,
    })) as Array<Record<string, unknown>>;
    return result.map((raw) => ({
      sessionId: optionalString(raw.session_id) ?? "",
      runId: optionalString(raw.run_id) ?? "",
      artifactId: optionalString(raw.artifact_id) ?? "",
      artifactType: optionalString(raw.artifact_type) ?? "",
      reference: optionalString(raw.reference) ?? "",
      digest: optionalString(raw.digest) ?? "",
      exists: Boolean(raw.exists),
      verified: Boolean(raw.verified),
      producerTaskId: optionalString(raw.producer_task_id) ?? "",
      updatedAt: optionalString(raw.updated_at) ?? "",
    }));
  }

  async listParticipants(request: ListParticipantsRequest): Promise<ParticipantSummary[]> {
    const result = (await this.call("list_participants", {
      tenant_id: request.tenantId, user_id: this.identity.userId,
      session_id: request.sessionId, request_id: request.requestId,
    })) as Array<Record<string, unknown>>;
    return result.map((raw) => ({
      participantId: optionalString(raw.participant_id) ?? "",
      role: optionalString(raw.role) ?? "",
      joinedAt: optionalString(raw.joined_at) ?? "",
    }));
  }

  async listFacts(request: ListFactsRequest): Promise<FactRecordSummary[]> {
    const result = (await this.call("list_facts", {
      tenant_id: request.tenantId, user_id: this.identity.userId,
      session_id: request.sessionId, request_id: request.requestId,
      scope: request.scope ?? "", limit: request.limit ?? 500,
    })) as Array<Record<string, unknown>>;
    return result.map((raw) => ({
      category: optionalString(raw.category) ?? "",
      key: optionalString(raw.key) ?? "",
      value: optionalString(raw.value) ?? "",
      scope: optionalString(raw.scope) ?? "",
      evidenceId: optionalString(raw.evidence_id) ?? "",
      sourceKind: optionalString(raw.source_kind) ?? "",
      sourceRef: optionalString(raw.source_ref) ?? "",
      revision: Number(raw.revision ?? 1),
      createdAt: optionalString(raw.created_at) ?? "",
    }));
  }

  async forgetFact(request: ForgetFactRequest): Promise<FactForgetResult> {
    const raw = (await this.call("forget_fact", {
      tenant_id: request.tenantId, user_id: this.identity.userId,
      session_id: request.sessionId, request_id: request.requestId,
      category: request.category, key: request.key,
      scope: request.scope ?? "",
    })) as Record<string, unknown>;
    return {
      category: optionalString(raw.category) ?? "",
      key: optionalString(raw.key) ?? "",
      removed: Boolean(raw.removed),
    };
  }

  async delegateRun(request: DelegateRunRequest): Promise<DelegateRunResult> {
    const raw = await this.call("delegate_run", {
      tenant_id: request.tenantId, user_id: this.identity.userId,
      session_id: request.sessionId, request_id: request.requestId,
      parent_run_id: request.parentRunId,
      request_text: request.requestText,
      target_planet: request.targetPlanet ?? "",
      profile_id: request.profileId ?? "",
      context_refs: request.contextRefs ?? [],
    }) as Record<string, unknown>;
    return {
      taskId: requiredString(raw.task_id, "task_id"),
      handoffId: requiredString(raw.handoff_id, "handoff_id"),
      sessionId: optionalString(raw.session_id) ?? "",
      targetPlanet: optionalString(raw.target_planet) ?? "",
    };
  }

  async listSkills(request: ListSkillsRequest): Promise<SkillSummary[]> {
    return ((await this.call("list_skills", {
      tenant_id: request.tenantId,
      session_id: request.sessionId,
      request_id: request.requestId,
    })) as Array<{ skill_id?: unknown; name?: unknown; description?: unknown }>).map((raw) => ({
      skillId: requiredString(raw.skill_id, "skill_id"),
      name: optionalString(raw.name) ?? "",
      description: optionalString(raw.description) ?? "",
    }));
  }

  async listHistory(request: ListHistoryRequest): Promise<HistoryEntry[]> {
    const params: LocalRpcParams = {
      tenant_id: request.tenantId,
      user_id: this.identity.userId,
      session_id: request.sessionId,
      request_id: request.requestId,
      limit: request.limit ?? 200,
    };
    const result = (await this.call("list_history", params)) as unknown[];
    return result.map((value) => mapHistory(value));
  }

  async listHandoffs(request: ListHandoffsRequest): Promise<HandoffSummary[]> {
    const params: LocalRpcParams = {
      tenant_id: request.tenantId,
      user_id: this.identity.userId,
      session_id: request.sessionId,
      request_id: request.requestId,
      limit: request.limit ?? 500,
    };
    const result = (await this.call("list_handoffs", params)) as unknown[];
    return result.map((value) => mapHandoff(value));
  }

  async createSession(request: CreateSessionRequest): Promise<SessionSummary> {
    const params: LocalRpcParams = {
      tenant_id: request.tenantId,
      user_id: request.userId,
      session_id: request.sessionId,
      request_id: request.requestId,
    };
    return mapSession(await this.call("create_session", params));
  }

  async deleteSession(request: CreateSessionRequest): Promise<{ removed: boolean }> {
    const params: LocalRpcParams = {
      tenant_id: request.tenantId,
      user_id: request.userId,
      session_id: request.sessionId,
      request_id: request.requestId,
    };
    return (await this.call("delete_session", params)) as { removed: boolean };
  }

  async assignSessionWorkspace(
    request: AssignSessionWorkspaceRequest,
  ): Promise<{ sessionId: string; workspaceId: string }> {
    this.assertIdentity(request);
    const result = (await this.call("assign_session_workspace", {
      tenant_id: request.tenantId, user_id: this.identity.userId,
      session_id: request.sessionId, request_id: request.requestId,
      workspace_id: request.workspaceId,
    })) as { session_id: string; workspace_id: string };
    return { sessionId: result.session_id, workspaceId: result.workspace_id };
  }

  async deleteWorkspace(request: DeleteWorkspaceRequest): Promise<{ removed: boolean }> {
    this.assertIdentity(request);
    return (await this.call("delete_workspace", {
      tenant_id: request.tenantId, user_id: this.identity.userId,
      session_id: request.sessionId, request_id: request.requestId,
      workspace_id: request.workspaceId,
    })) as { removed: boolean };
  }

  async pickFolder(): Promise<{ path: string | null }> {
    return (await this.call("pick_folder", {} as LocalRpcParams)) as { path: string | null };
  }

  async listDirectory(path: string): Promise<DirectoryListing> {
    return (await this.call("list_directory", { path } as LocalRpcParams)) as DirectoryListing;
  }

  async listSessions(request: ListSessionsRequest): Promise<SessionSummary[]> {
    const params: LocalRpcParams = {
      tenant_id: request.tenantId,
      user_id: request.userId,
      request_id: request.requestId,
      limit: request.limit ?? 500,
    };
    const result = (await this.call("list_sessions", params)) as unknown[];
    return result.map((value) => mapSession(value));
  }

  async startRun(request: StartRunRequest): Promise<RunHandle> {
    this.assertIdentity(request);
    const params: LocalRpcParams = {
      tenant_id: request.tenantId,
      user_id: this.identity.userId,
      session_id: request.sessionId,
      request_id: request.requestId,
      request_text: request.requestText,
      workspace_root: request.workspaceRoot ?? "",
      metadata: { ...(request.metadata ?? {}), ...(request.requestDigest ? { desktop_request_digest: request.requestDigest } : {}) },
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

  async pauseRun(request: PauseRunRequest): Promise<RunHandle> {
    const params: LocalRpcParams = {
      ...this.lookupParams(request, "pause_run", request.requestId),
      requested_by: request.requestedBy ?? "",
    };
    return this.project(async () => mapRunHandle(await this.call("pause_run", params)));
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

  async subscribeEvents(request: EventStreamRequest): Promise<RunEvent[]> {
    if (!Number.isInteger(request.afterSequence) || request.afterSequence < -1) {
      throw new AgentServiceClientError({
        code: "CURSOR_INVALID",
        message: "event cursor is invalid",
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
    return this.project(async () => mapEvents(await this.call("subscribe_events", params), afterSequence));
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
    // sessionId is a locator — sessions are first-class and the user can
    // switch between them; only the tenant scope is an identity invariant.
    if (request.tenantId !== this.identity.tenantId) {
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
      // Aborted fetches (navigation, component unmount, cleanup) are not
      // protocol failures — surface them as-is so callers can ignore or
      // log the cancellation instead of a misleading protocol error.
      if (error instanceof DOMException && error.name === "AbortError") throw error;
      if (error instanceof LocalTransportProtocolError) {
        throw new AgentServiceClientError({
          code: "INTERNAL_ERROR",
          message: `local transport protocol error on ${method}: ${error.message}`,
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
