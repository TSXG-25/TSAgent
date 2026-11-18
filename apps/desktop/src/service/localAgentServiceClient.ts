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
  type SessionScope,
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
  type GraphExecutionLookupRequest,
  type GraphExecutionRef,
  type GraphExecutionSummary,
  type GraphRunStepSummary,
  type RunWorkflowGraphRequest,
  type MotifSummary,
  type CreateMotifRequest,
  type AssignMotifRequest,
  type ResolveMotifRequest,
  type ReportMotifRequest,
  type RejectMotifRequest,
  type ListMotifsRequest,
  type EvaluationSummary,
  type RecordEvaluationRequest,
  type ListEvaluationsRequest,
  type AttachFilesRequest,
  type ListWorkspaceFilesRequest,
  type WorkspaceFileSummary,
  type WorkspaceFileContent,
  type WorkspaceFileRequest,
  type SkillSummary,
  type DelegateRunRequest,
  type DelegateRunResult,
  type ListSchedulesRequest,
  type ListSessionsRequest,
  type ReadArtifactRequest,
  type ArtifactContent,
  type ArtifactPreview,
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
import type {
  WireParams,
  WireResult,
  WireDelegationResult,
  WireGraphRunStepSummary,
  WireWorkspaceFileContent,
  WireApprovalSummary,
  WireArtifactSummary,
  WireEvaluationSummary,
  WireFailureSummary,
  WireHandoffSummary,
  WireHealthResult,
  WireHistoryEntry,
  WireMotifSummary,
  WireProfileSummary,
  WireResumeSummary,
  WireRunEvent,
  WireRunHandle,
  WireRunOutput,
  WireRunSnapshot,
  WireScheduleSummary,
  WireSessionSummary,
  WireSettingsSummary,
  WireTaskHandle,
  WireTaskSnapshot,
  WireWorkflowGraphSummary,
  WireWorkspaceSummary,
} from "./wire.generated";

export type LocalClientOperation = LocalTransportMethod;

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

function mapRunHandle(raw: WireRunHandle): RunHandle {
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

function mapResumeSummary(raw: WireResumeSummary | null | undefined, status: RunStatus): ResumeSummary | undefined {
  if (raw === null || raw === undefined) return undefined;
  // The wire summary carries the decision only — no checkpoint id or source
  // stage — so the resume request lets the service pick the checkpoint.
  return {
    checkpointId: "",
    action: mapResumeAction(raw.action),
    reason: raw.summary || raw.reason_code,
    sourceStage: "",
    outcome: status === "completed" ? "completed" : "ready",
  };
}

function mapFailureSummary(raw: WireFailureSummary | null | undefined): FailureSummary | undefined {
  if (raw === null || raw === undefined) return undefined;
  return {
    code: requiredString(raw.code, "failure code"),
    message: typeof raw.message === "string" ? raw.message : "Run failed",
    retryable: raw.retryable === true,
  };
}

function mapOutput(raw: WireRunOutput | null | undefined): RunOutputSummary | undefined {
  if (raw === null || raw === undefined) return undefined;
  return {
    runId: requiredString(raw.run_id, "output.run_id"),
    revision: numberValue(raw.revision, "output.revision"),
    text: requiredString(raw.text, "output.text"),
    evidenceIds: stringArray(raw.evidence_ids ?? [], "output.evidence_ids"),
    artifactIds: stringArray(raw.artifact_ids ?? [], "output.artifact_ids"),
    createdAt: requiredString(raw.created_at, "output.created_at"),
  };
}

function mapArtifact(raw: WireArtifactSummary, fallbackRunId: string): ArtifactSummary {
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

function mapMotif(raw: WireMotifSummary): MotifSummary {
  return {
    motifId: requiredString(raw.motif_id, "motif_id"),
    text: optionalString(raw.text) ?? "",
    source: optionalString(raw.source) ?? "",
    status: optionalString(raw.status) ?? "",
    assignedTaskId: optionalString(raw.assigned_task_id) ?? "",
    resolutionRunId: optionalString(raw.resolution_run_id) ?? "",
    resolutionSessionId: optionalString(raw.resolution_session_id) ?? "",
    resolution: optionalString(raw.resolution) ?? "",
    createdAt: optionalString(raw.created_at) ?? "",
    updatedAt: optionalString(raw.updated_at) ?? "",
  };
}

function mapWorkflowGraph(raw: WireWorkflowGraphSummary): WorkflowGraphSummary {
  return {
    graphId: requiredString(raw.graph_id, "graph_id"),
    name: optionalString(raw.name) ?? "",
    definitionJson: optionalString(raw.definition_json) ?? "{}",
    owner: optionalString(raw.owner) ?? "",
    createdAt: optionalString(raw.created_at) ?? "",
    updatedAt: optionalString(raw.updated_at) ?? "",
  };
}

function mapEvaluation(raw: WireEvaluationSummary): EvaluationSummary {
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

function mapWorkspace(raw: WireWorkspaceSummary): WorkspaceSummary {
  return {
    workspaceId: requiredString(raw.workspace_id, "workspace_id"),
    displayName: optionalString(raw.display_name) ?? "",
    capabilities: Array.isArray(raw.capabilities) ? raw.capabilities.map(String) : [],
    defaultProfileId: optionalString(raw.default_profile_id) ?? "",
    description: optionalString(raw.description) ?? "",
    updatedAt: optionalString(raw.updated_at) ?? "",
    path: optionalString(raw.path) ?? "",
    paths: Array.isArray(raw.paths) ? raw.paths.map(String) : [],
    memberCount: Number(raw.member_count ?? 0),
  };
}

function mapSettings(raw: WireSettingsSummary): SettingsSummary {
  return {
    providerMode: optionalString(raw.provider_mode) ?? "",
    activeProvider: optionalString(raw.active_provider) ?? "",
    activeModel: optionalString(raw.active_model) ?? "",
    deepseekModel: optionalString(raw.deepseek_model) ?? "",
    ollamaModel: optionalString(raw.ollama_model) ?? "",
  };
}

function mapProfile(raw: WireProfileSummary): ProfileSummary {
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

function mapSchedule(raw: WireScheduleSummary): ScheduleSummary {
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

function mapHistory(raw: WireHistoryEntry): HistoryEntry {
  return {
    runId: requiredString(raw.run_id, "run_id"),
    taskId: optionalString(raw.task_id) ?? "",
    requestText: optionalString(raw.request_text) ?? "",
    answerText: optionalString(raw.answer_text) ?? "",
    status: optionalString(raw.status) ?? "",
    updatedAt: optionalString(raw.updated_at) ?? "",
  };
}

function mapHandoff(raw: WireHandoffSummary): HandoffSummary {
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

function mapSession(raw: WireSessionSummary): SessionSummary {
  return {
    tenantId: requiredString(raw.tenant_id, "tenant_id"),
    sessionId: requiredString(raw.session_id, "session_id"),
    userId: optionalString(raw.user_id) ?? "",
    createdAt: requiredString(raw.created_at, "created_at"),
    runCount: Number(raw.run_count ?? 0),
    latestRunId: optionalString(raw.latest_run_id) ?? "",
    // latest_status rides the backend RUN_STATUS vocabulary — decode at
    // the boundary like every other status field, never pass through.
    latestStatus: raw.latest_status ? mapStatus(raw.latest_status) : "",
    latestUpdatedAt: optionalString(raw.latest_updated_at) ?? "",
    workspaceId: optionalString(raw.workspace_id) ?? "",
    planet: optionalString(raw.planet) ?? "",
  };
}

function mapApproval(raw: WireApprovalSummary): ApprovalSummary {
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

function mapTaskHandle(raw: WireTaskHandle): TaskHandle {
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

function mapTaskRecord(raw: WireTaskSnapshot): TaskRecord {
  return {
    ...mapTaskHandle(raw),
    status: optionalString(raw.status) ?? "CREATED",
    runIds: Array.isArray(raw.run_ids) ? raw.run_ids.map(String) : [],
    childTaskIds: Array.isArray(raw.child_task_ids) ? raw.child_task_ids.map(String) : [],
  };
}

function mapWorkspaceFile(raw: WireWorkspaceFileContent): WorkspaceFileContent {
  return {
    path: requiredString(raw.path, "path"),
    text: raw.text,
    digest: raw.digest,
    redacted: raw.redacted,
    workspaceRoot: raw.workspace_root,
  };
}

function mapDelegation(raw: WireDelegationResult): DelegateRunResult {
  return {
    taskId: requiredString(raw.task_id, "task_id"),
    handoffId: requiredString(raw.handoff_id, "handoff_id"),
    sessionId: raw.session_id,
    targetPlanet: raw.target_planet,
    requestText: raw.request_text,
    workspaceRoot: raw.workspace_root,
    agentProfile: { ...raw.agent_profile },
  };
}

function mapGraphStep(raw: WireGraphRunStepSummary): GraphRunStepSummary {
  // SKIPPED steps carry empty ids — only status reports the outcome.
  return {
    nodeId: requiredString(raw.node_id, "node_id"),
    taskId: raw.task_id,
    runId: raw.run_id,
    sessionId: raw.session_id,
    status: raw.status,
    executionId: raw.execution_id,
    detail: raw.detail,
  };
}

function mapSnapshot(raw: WireRunSnapshot): RunSnapshot {
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
    taskId: optionalString(raw.task_id) ?? "",
    originPlanet: optionalString(raw.origin_planet) ?? "",
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

function mapEvent(raw: WireRunEvent): RunEvent {
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

function mapEvents(value: WireRunEvent[], afterSequence: number): RunEvent[] {
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

function mapHealth(raw: WireHealthResult): HealthSnapshot {
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

  async listRuns(scope: SessionScope): Promise<RunSnapshot[]> {
    const params: WireParams<"list_runs"> = {
      ...this.ids(scope, "get_run"),
      limit: 100,
      ...(scope.originPlanet !== undefined ? { origin_planet: scope.originPlanet } : {}),
    };
    return this.project(async () => {
      const snapshots = (await this.call("list_runs", params)).map(mapSnapshot);
      for (const snapshot of snapshots) this.rememberSnapshot(snapshot);
      return snapshots;
    });
  }

  async createTask(request: CreateTaskRequest): Promise<TaskHandle> {
    return mapTaskHandle(await this.call("create_task", {
      ...this.ids(request, "create_task"),
      task_id: request.taskId ?? "",
      parent_task_id: request.parentTaskId ?? "",
      origin_planet: request.originPlanet ?? "",
      owner_profile_id: request.ownerProfileId ?? "",
    }));
  }

  async listTasks(scope: SessionScope): Promise<TaskRecord[]> {
    return (await this.call("list_tasks", this.ids(scope, "get_task"))).map(mapTaskRecord);
  }

  async getTask(request: TaskLookupRequest): Promise<TaskRecord> {
    return mapTaskRecord(await this.call("get_task", { ...this.ids(request, "get_task"), task_id: request.taskId }));
  }

  async listApprovals(request: ListApprovalsRequest): Promise<ApprovalSummary[]> {
    return (await this.call("list_approvals", {
      ...this.ids(request, "list_approvals"),
      run_id: request.runId ?? "",
      status: request.status ?? "",
      limit: request.limit ?? 200,
    })).map(mapApproval);
  }

  async decideApproval(request: DecideApprovalRequest): Promise<ApprovalSummary> {
    return mapApproval(await this.call("decide_approval", {
      ...this.ids(request, "decide_approval"),
      run_id: request.runId,
      approval_id: request.approvalId,
      decision: request.decision,
      decided_by: request.decidedBy ?? this.identity.userId,
    }));
  }

  async upsertWorkspace(request: UpsertWorkspaceRequest): Promise<WorkspaceSummary> {
    return mapWorkspace(await this.call("upsert_workspace", {
      ...this.ids(request, "upsert_workspace"),
      workspace_id: request.workspaceId,
      display_name: request.displayName ?? "",
      path: request.path ?? "",
      paths: request.paths ?? [],
      capabilities: request.capabilities ?? [],
      default_profile_id: request.defaultProfileId ?? "",
      description: request.description ?? "",
    }));
  }

  async listWorkspaces(request: ListWorkspacesRequest): Promise<WorkspaceSummary[]> {
    return (await this.call("list_workspaces", { ...this.ids(request, "list_workspaces"), limit: request.limit ?? 50 })).map(mapWorkspace);
  }

  async updateSettings(request: UpdateSettingsRequest): Promise<SettingsSummary> {
    return mapSettings(await this.call("update_settings", {
      ...this.ids(request, "update_settings"),
      provider_mode: request.providerMode ?? "",
      deepseek_model: request.deepseekModel ?? "",
      ollama_model: request.ollamaModel ?? "",
    }));
  }

  async getSettings(request: GetSettingsRequest): Promise<SettingsSummary> {
    return mapSettings(await this.call("get_settings", this.ids(request, "get_settings")));
  }

  async upsertProfile(request: UpsertProfileRequest): Promise<ProfileSummary> {
    return mapProfile(await this.call("upsert_profile", {
      ...this.ids(request, "upsert_profile"),
      profile_id: request.profileId,
      display_name: request.displayName ?? "",
      planet_affinity: request.planetAffinity ?? "",
      model_ref: request.modelRef ?? "",
      skill_refs: request.skillRefs ?? [],
      permission_policy: request.permissionPolicy ?? {},
    }));
  }

  async listProfiles(request: ListProfilesRequest): Promise<ProfileSummary[]> {
    return (await this.call("list_profiles", { ...this.ids(request, "list_profiles"), limit: request.limit ?? 200 })).map(mapProfile);
  }

  async createSchedule(request: CreateScheduleRequest): Promise<ScheduleSummary> {
    return mapSchedule(await this.call("create_schedule", {
      ...this.ids(request, "create_schedule"),
      request_text: request.requestText,
      interval_seconds: request.intervalSeconds,
      target_planet: request.targetPlanet ?? "",
      auto_eval: request.autoEval ?? false,
      schedule_id: request.scheduleId ?? "",
    }));
  }

  async listSchedules(request: ListSchedulesRequest): Promise<ScheduleSummary[]> {
    return (await this.call("list_schedules", { ...this.ids(request, "list_schedules"), limit: request.limit ?? 200 })).map(mapSchedule);
  }

  async updateSchedule(request: UpdateScheduleRequest): Promise<ScheduleSummary> {
    return mapSchedule(await this.call("update_schedule", {
      ...this.ids(request, "update_schedule"),
      schedule_id: request.scheduleId,
      status: request.status,
    }));
  }

  async listCapabilities(_request: ListCapabilitiesRequest): Promise<CapabilitySummary[]> {
    return (await this.call("list_capabilities", {})).map((raw) => ({
      capability: requiredString(raw.capability, "capability"),
      tools: raw.tools.map(String),
    }));
  }

  async webSearch(request: WebSearchRequest): Promise<WebSearchResult> {
    const raw = await this.call("web_search", { query: request.query, max_results: request.maxResults ?? 8 });
    return { provider: raw.provider, content: raw.content, sources: raw.sources };
  }

  async recordEvaluation(request: RecordEvaluationRequest): Promise<EvaluationSummary> {
    return mapEvaluation(await this.call("record_evaluation", {
      ...this.ids(request, "record_evaluation"),
      name: request.name ?? "",
      run_id: request.runId ?? "",
      baseline_ref: request.baselineRef ?? "",
      candidate_ref: request.candidateRef ?? "",
      score: request.score ?? 0,
      verdict: request.verdict ?? "neutral",
      notes: request.notes ?? "",
      eval_id: request.evalId ?? "",
    }));
  }

  async listEvaluations(request: ListEvaluationsRequest): Promise<EvaluationSummary[]> {
    return (await this.call("list_evaluations", {
      ...this.ids(request, "list_evaluations"),
      all_sessions: request.allSessions ?? false,
      limit: request.limit ?? 200,
    })).map(mapEvaluation);
  }

  async listWorkspaceFiles(request: ListWorkspaceFilesRequest): Promise<WorkspaceFileSummary[]> {
    return (await this.call("list_workspace_files", { ...this.ids(request, "list_workspace_files"), limit: request.limit ?? 500 }))
      .map((raw) => ({ path: requiredString(raw.path, "path"), size: raw.size, modifiedAt: raw.modified_at }));
  }

  async readWorkspaceFile(request: WorkspaceFileRequest): Promise<WorkspaceFileContent> {
    return mapWorkspaceFile(await this.call("read_workspace_file", { ...this.ids(request, "read_workspace_file"), path: request.path }));
  }

  async writeWorkspaceFile(request: WorkspaceFileRequest): Promise<WorkspaceFileContent> {
    return mapWorkspaceFile(await this.call("write_workspace_file", {
      ...this.ids(request, "write_workspace_file"),
      path: request.path,
      content: request.content ?? "",
      base_digest: request.baseDigest ?? "",
      workspace_root: request.workspaceRoot ?? "",
    }));
  }

  async upsertWorkflowGraph(request: UpsertWorkflowGraphRequest): Promise<WorkflowGraphSummary> {
    return mapWorkflowGraph(await this.call("upsert_workflow_graph", {
      ...this.ids(request, "upsert_workflow_graph"),
      graph_id: request.graphId,
      name: request.name ?? "",
      definition: request.definition ?? {},
      owner: request.owner ?? this.identity.userId,
    }));
  }

  async createMotif(request: CreateMotifRequest): Promise<MotifSummary> {
    return mapMotif(await this.call("create_motif", {
      ...this.ids(request, "create_motif"),
      text: request.text,
      motif_id: request.motifId ?? "",
      source: request.source ?? "user",
    }));
  }

  async assignMotif(request: AssignMotifRequest): Promise<DelegateRunResult> {
    return mapDelegation(await this.call("assign_motif", {
      ...this.ids(request, "assign_motif"),
      motif_id: request.motifId,
      parent_run_id: request.parentRunId,
    }));
  }

  async resolveMotif(request: ResolveMotifRequest): Promise<MotifSummary[]> {
    return (await this.call("resolve_motif", {
      ...this.ids(request, "resolve_motif"),
      motif_id: request.motifId,
      resolution_run_id: request.resolutionRunId,
      resolution: request.resolution,
    })).map(mapMotif);
  }

  async reportMotif(request: ReportMotifRequest): Promise<MotifSummary[]> {
    return (await this.call("report_motif", {
      ...this.ids(request, "report_motif"),
      motif_id: request.motifId,
      resolution_run_id: request.resolutionRunId,
      resolution: request.resolution,
    })).map(mapMotif);
  }

  async rejectMotif(request: RejectMotifRequest): Promise<MotifSummary[]> {
    return (await this.call("reject_motif", { ...this.ids(request, "reject_motif"), motif_id: request.motifId })).map(mapMotif);
  }

  async listMotifs(request: ListMotifsRequest): Promise<MotifSummary[]> {
    return (await this.call("list_motifs", {
      ...this.ids(request, "list_motifs"),
      status: request.status ?? "",
      limit: request.limit ?? 100,
    })).map(mapMotif);
  }

  async runGraph(request: RunWorkflowGraphRequest): Promise<GraphRunStepSummary[]> {
    return (await this.call("run_graph", {
      ...this.ids(request, "run_graph"),
      graph_id: request.graphId,
      parent_run_id: request.parentRunId ?? "",
      origin_planet: request.originPlanet ?? "",
    })).map(mapGraphStep);
  }

  async getGraphExecution(request: GraphExecutionLookupRequest): Promise<GraphExecutionSummary> {
    const raw = await this.call("get_graph_execution", { ...this.ids(request, "get_graph_execution"), execution_id: request.executionId });
    return {
      executionId: requiredString(raw.execution_id, "execution_id"),
      graphId: raw.graph_id,
      sessionId: raw.session_id,
      steps: raw.steps.map(mapGraphStep),
    };
  }

  async listGraphExecutions(request: { sessionId: string; requestId?: string }): Promise<GraphExecutionRef[]> {
    return (await this.call("list_graph_executions", this.ids(request, "list_graph_executions"))).map((raw) => ({
      executionId: requiredString(raw.execution_id, "execution_id"),
      graphId: raw.graph_id,
      updatedAt: raw.updated_at,
    }));
  }

  async listWorkflowGraphs(request: ListWorkflowGraphsRequest): Promise<WorkflowGraphSummary[]> {
    return (await this.call("list_workflow_graphs", { ...this.ids(request, "list_workflow_graphs"), limit: request.limit ?? 200 })).map(mapWorkflowGraph);
  }

  async listWorkflows(_request: ListWorkflowsRequest): Promise<WorkflowDefinitionSummary[]> {
    return (await this.call("list_workflows", {})).map((raw) => ({
      workflowId: requiredString(raw.workflow_id, "workflow_id"),
      description: raw.description,
      version: raw.version,
    }));
  }

  async searchArtifacts(request: SearchArtifactsRequest): Promise<JupiterArtifactSummary[]> {
    return (await this.call("search_artifacts", {
      ...this.ids(request, "search_artifacts"),
      query: request.query ?? "",
      all_sessions: request.allSessions ?? false,
      limit: request.limit ?? 200,
    })).map((raw) => ({
      sessionId: raw.session_id,
      runId: raw.run_id,
      artifactId: raw.artifact_id,
      artifactType: raw.artifact_type,
      reference: raw.reference,
      digest: raw.digest,
      exists: raw.exists,
      verified: raw.verified,
      producerTaskId: raw.producer_task_id,
      updatedAt: raw.updated_at,
    }));
  }

  async listParticipants(request: ListParticipantsRequest): Promise<ParticipantSummary[]> {
    return (await this.call("list_participants", this.ids(request, "list_participants"))).map((raw) => ({
      participantId: raw.participant_id,
      role: raw.role,
      joinedAt: raw.joined_at,
    }));
  }

  async listFacts(request: ListFactsRequest): Promise<FactRecordSummary[]> {
    return (await this.call("list_facts", {
      ...this.ids(request, "list_facts"),
      scope: request.scope ?? "",
      limit: request.limit ?? 500,
    })).map((raw) => ({
      category: raw.category,
      key: raw.key,
      value: raw.value,
      scope: raw.scope,
      evidenceId: raw.evidence_id,
      sourceKind: raw.source_kind,
      sourceRef: raw.source_ref,
      revision: raw.revision,
      createdAt: raw.created_at,
    }));
  }

  async forgetFact(request: ForgetFactRequest): Promise<FactForgetResult> {
    const raw = await this.call("forget_fact", {
      ...this.ids(request, "forget_fact"),
      category: request.category,
      key: request.key,
      scope: request.scope ?? "",
    });
    return { category: raw.category, key: raw.key, removed: raw.removed };
  }

  async delegateRun(request: DelegateRunRequest): Promise<DelegateRunResult> {
    return mapDelegation(await this.call("delegate_run", {
      ...this.ids(request, "delegate_run"),
      parent_run_id: request.parentRunId ?? "",
      parent_task_id: request.parentTaskId ?? "",
      request_text: request.requestText,
      target_planet: request.targetPlanet ?? "",
      profile_id: request.profileId ?? "",
      context_refs: request.contextRefs ?? [],
    }));
  }

  async listSkills(_request: ListSkillsRequest): Promise<SkillSummary[]> {
    return (await this.call("list_skills", {})).map((raw) => ({
      skillId: requiredString(raw.skill_id, "skill_id"),
      name: raw.name,
      description: raw.description,
    }));
  }

  async listHistory(request: ListHistoryRequest): Promise<HistoryEntry[]> {
    return (await this.call("list_history", { ...this.ids(request, "list_history"), limit: request.limit ?? 200 })).map(mapHistory);
  }

  async listHandoffs(request: ListHandoffsRequest): Promise<HandoffSummary[]> {
    return (await this.call("list_handoffs", { ...this.ids(request, "list_handoffs"), limit: request.limit ?? 500 })).map(mapHandoff);
  }

  async createSession(request: CreateSessionRequest): Promise<SessionSummary> {
    return mapSession(await this.call("create_session", { ...this.ids(request, "create_session"), planet: request.planet ?? "" }));
  }

  async deleteSession(request: CreateSessionRequest): Promise<{ removed: boolean }> {
    const raw = await this.call("delete_session", this.ids(request, "delete_session"));
    return { removed: raw.removed };
  }

  async assignSessionWorkspace(
    request: AssignSessionWorkspaceRequest,
  ): Promise<{ sessionId: string; workspaceId: string }> {
    const raw = await this.call("assign_session_workspace", { ...this.ids(request, "assign_session_workspace"), workspace_id: request.workspaceId });
    return { sessionId: raw.session_id, workspaceId: raw.workspace_id };
  }

  async resolveSessionWorkspace(
    request: SessionScope,
  ): Promise<{ sessionId: string; workspaceId: string; path: string; bound: boolean }> {
    const raw = await this.call("resolve_session_workspace", this.ids(request, "resolve_session_workspace"));
    return { sessionId: raw.session_id, workspaceId: raw.workspace_id, path: raw.path, bound: raw.bound };
  }

  async deleteWorkspace(request: DeleteWorkspaceRequest): Promise<{ removed: boolean }> {
    const raw = await this.call("delete_workspace", { ...this.ids(request, "delete_workspace"), workspace_id: request.workspaceId });
    return { removed: raw.removed };
  }

  async pickFolder(): Promise<{ path: string | null }> {
    return this.call("pick_folder", {});
  }

  async pickFiles(): Promise<{ paths: string[] }> {
    return this.call("pick_files", {});
  }

  async attachFiles(request: AttachFilesRequest): Promise<{ contextRefs: string[] }> {
    const raw = await this.call("attach_files", { ...this.ids(request, "attach_files"), paths: request.paths });
    return { contextRefs: raw.context_refs };
  }

  async listDirectory(path: string): Promise<DirectoryListing> {
    const raw = await this.call("list_directory", { path });
    return { path: raw.path, parent: raw.parent, dirs: raw.dirs, ...(raw.home !== undefined ? { home: raw.home } : {}) };
  }

  async revealPath(path: string): Promise<{ revealed: boolean }> {
    return this.call("reveal_path", { path });
  }

  async listSessions(request: ListSessionsRequest): Promise<SessionSummary[]> {
    return (await this.call("list_sessions", {
      tenant_id: this.identity.tenantId,
      user_id: this.identity.userId,
      request_id: request.requestId ?? this.nextRequestId("list_sessions"),
      limit: request.limit ?? 500,
    })).map(mapSession);
  }

  async startRun(request: StartRunRequest): Promise<RunHandle> {
    const params: WireParams<"start_run"> = {
      ...this.ids(request, "start_run"),
      request_text: request.requestText,
      task_id: request.taskId ?? "",
      workspace_root: request.workspaceRoot ?? "",
      origin_planet: request.originPlanet ?? "",
      context_refs: request.contextRefs ?? [],
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
    const params: WireParams<"cancel_run"> = {
      ...this.lookupParams(request, "cancel_run"),
      requested_by: request.requestedBy,
    };
    return this.project(async () => {
      const snapshot = mapSnapshot(await this.call("cancel_run", params));
      this.rememberSnapshot(snapshot);
      return snapshot;
    });
  }

  async resumeRun(request: ResumeRunRequest): Promise<RunHandle> {
    const params: WireParams<"resume_run"> = {
      ...this.ids({ sessionId: request.sessionId, requestId: request.resumeRequestId }, "resume_run"),
      run_id: request.runId,
      request_text: "",
      checkpoint_id: request.checkpointId,
      action: request.action,
    };
    return this.project(async () => mapRunHandle(await this.call("resume_run", params)));
  }

  async pauseRun(request: PauseRunRequest): Promise<RunHandle> {
    const params: WireParams<"pause_run"> = {
      ...this.lookupParams(request, "pause_run"),
      requested_by: request.requestedBy ?? "",
    };
    return this.project(async () => mapRunHandle(await this.call("pause_run", params)));
  }

  async listArtifacts(request: ListArtifactsRequest): Promise<ArtifactSummary[]> {
    const params = this.lookupParams(request, "list_artifacts");
    return this.project(async () => (await this.call("list_artifacts", params)).map((item) => mapArtifact(item, request.runId)));
  }

  async readArtifact(request: ReadArtifactRequest): Promise<ArtifactContent> {
    const params: WireParams<"read_artifact"> = { ...this.lookupParams(request, "read_artifact"), artifact_id: request.artifactId };
    return this.project(async () => {
      const raw = await this.call("read_artifact", params);
      return {
        artifactId: requiredString(raw.artifact_id, "artifact_id"),
        reference: requiredString(raw.reference, "artifact.reference"),
        text: raw.text,
        stale: raw.stale,
        result: raw.result,
      };
    });
  }

  async readArtifactPreview(request: ReadArtifactRequest): Promise<ArtifactPreview> {
    const params: WireParams<"read_artifact_preview"> = { ...this.lookupParams(request, "read_artifact_preview"), artifact_id: request.artifactId };
    return this.project(async () => {
      const raw = await this.call("read_artifact_preview", params);
      return {
        artifactId: requiredString(raw.artifact_id, "artifact_id"),
        mime: raw.mime,
        dataB64: raw.data_b64,
        size: raw.size,
        stale: raw.stale,
        result: raw.result,
      };
    });
  }

  async readEvents(request: EventStreamRequest): Promise<RunEvent[]> {
    const params = this.eventParams(request);
    if (request.limit !== undefined && (!Number.isInteger(request.limit) || request.limit < 1)) {
      throw new AgentServiceClientError({
        code: "INVALID_REQUEST",
        message: "event limit is invalid",
        retryable: false,
        runId: request.runId,
      });
    }
    return this.project(async () => mapEvents(await this.call("read_events", params), params.after_sequence ?? 0));
  }

  async subscribeEvents(request: EventStreamRequest): Promise<RunEvent[]> {
    const params = this.eventParams(request);
    return this.project(async () => mapEvents(await this.call("subscribe_events", params), params.after_sequence ?? 0));
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

  /** Identity-complete scope every session-bound request carries. */
  private ids(
    request: { sessionId: string; requestId?: string },
    operation: LocalClientOperation,
  ): { tenant_id: string; user_id: string; session_id: string; request_id: string } {
    return {
      tenant_id: this.identity.tenantId,
      user_id: this.identity.userId,
      session_id: request.sessionId,
      request_id: request.requestId ?? this.nextRequestId(operation),
    };
  }

  private eventParams(request: EventStreamRequest): WireParams<"read_events"> {
    if (!Number.isInteger(request.afterSequence) || request.afterSequence < -1) {
      throw new AgentServiceClientError({
        code: "CURSOR_INVALID",
        message: "event cursor is invalid",
        retryable: false,
        runId: request.runId,
      });
    }
    return {
      ...this.lookupParams(request, "read_events"),
      after_sequence: Math.max(0, request.afterSequence),
      ...(request.limit === undefined ? {} : { limit: request.limit }),
    };
  }

  private lookupParams(
    request: { sessionId: string; runId: string; requestId?: string },
    operation: LocalClientOperation,
  ): WireParams<"get_run"> {
    return { ...this.ids(request, operation), run_id: request.runId };
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

  private rememberSnapshot(snapshot: RunSnapshot): void {
    this.knownSnapshots.set(snapshot.runId, clone(snapshot));
  }

  private async call<M extends LocalTransportMethod>(method: M, params: WireParams<M>): Promise<WireResult<M>> {
    try {
      return await this.transport.request<LocalRpcParams, WireResult<M>>(method, params as LocalRpcParams);
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
