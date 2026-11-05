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

export type ResumeAction =
  | "RESUME_EXACT"
  | "REPLAY_FROM_STAGE"
  | "REPLAN_FROM_CHECKPOINT"
  | "ABANDON_AND_RESTART";

export interface PauseRunRequest {
  tenantId: string;
  sessionId: string;
  runId: string;
  requestId: string;
  requestedBy?: string;
}

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
  action: ResumeAction | null;
  reason: string;
  sourceStage: string;
  resumedAt?: string;
  outcome: "ready" | "completed";
}

export interface RunOutputSummary {
  runId: string;
  revision: number;
  text: string;
  evidenceIds: string[];
  artifactIds: string[];
  createdAt: string;
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
  /** Optional durable projection supplied by a real AgentService. */
  artifacts?: ArtifactSummary[];
  /** Optional durable terminal output supplied by a real AgentService. */
  output?: RunOutputSummary;
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
  | "ALREADY_TIMED_OUT"
  | "ALREADY_COMPLETED"
  | "RESUME_NOT_ALLOWED"
  | "IDEMPOTENCY_CONFLICT"
  | "EVENT_CURSOR_EXPIRED"
  | "EVENT_SEQUENCE_INVALID"
  | "CURSOR_INVALID"
  | "STORE_BUSY"
  | "PROVIDER_UNAVAILABLE"
  | "SERVICE_CLOSED"
  | "UNSUPPORTED_OPERATION"
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
  /** Long-poll variant — holds up to ~8s until new durable events arrive. */
  subscribeEvents(request: EventStreamRequest): Promise<RunEvent[]>;
}

export interface HealthSnapshot {
  status: "ok";
  protocolVersion: string;
  service: string;
}

export interface DesktopIdentity {
  tenantId: string;
  userId: string;
  sessionId: string;
}

export type DesktopAgentServiceMode = "mock" | "local";

export interface TaskHandle {
  tenantId: string;
  sessionId: string;
  taskId: string;
  parentTaskId: string;
  originPlanet: string;
  ownerProfileId: string;
  createdAt: string;
}

export interface TaskRecord extends TaskHandle {
  status: string;
  runIds: string[];
  childTaskIds: string[];
}

export interface CreateTaskRequest {
  tenantId: string;
  sessionId: string;
  requestId: string;
  taskId?: string;
  parentTaskId?: string;
  originPlanet?: string;
  ownerProfileId?: string;
}

export interface TaskLookupRequest {
  tenantId: string;
  sessionId: string;
  requestId: string;
  taskId: string;
}

/** Run List is a desktop catalog capability; the core contract remains above. */
export interface RunCatalogClient {
  listRuns(): Promise<RunSnapshot[]>;
}

export interface ApprovalSummary {
  approvalId: string;
  runId: string;
  action: string;
  scope: string;
  params: Record<string, unknown>;
  riskLevel: string;
  status: "PENDING" | "APPROVED" | "DENIED" | "EXPIRED";
  decidedBy: string;
  decidedAt: string;
  createdAt: string;
}

export interface ListApprovalsRequest {
  tenantId: string;
  sessionId: string;
  requestId: string;
  runId?: string;
  status?: string;
  limit?: number;
}

export interface DecideApprovalRequest {
  tenantId: string;
  sessionId: string;
  requestId: string;
  runId: string;
  approvalId: string;
  decision: "approve" | "deny";
  decidedBy?: string;
}

/** Approval cards — durable gates that park runs in WAITING_USER. */
export interface ApprovalClient {
  listApprovals(request: ListApprovalsRequest): Promise<ApprovalSummary[]>;
  decideApproval(request: DecideApprovalRequest): Promise<ApprovalSummary>;
}

export interface SessionSummary {
  tenantId: string;
  sessionId: string;
  userId: string;
  createdAt: string;
  runCount: number;
  latestRunId: string;
  latestStatus: string;
  latestUpdatedAt: string;
}

export interface CreateSessionRequest {
  tenantId: string;
  userId: string;
  sessionId: string;
  requestId: string;
}

export interface ListSessionsRequest {
  tenantId: string;
  userId: string;
  requestId: string;
  limit?: number;
}

export interface HandoffSummary {
  handoffId: string;
  sourceRunId: string;
  sourceTaskId: string;
  targetTaskId: string;
  targetRunId: string;
  targetPlanet: string;
  contextRefs: string[];
  approvalState: string;
  createdAt: string;
}

export interface ListHandoffsRequest {
  tenantId: string;
  sessionId: string;
  requestId: string;
  limit?: number;
}

export interface HistoryEntry {
  runId: string;
  taskId: string;
  requestText: string;
  answerText: string;
  status: string;
  updatedAt: string;
}

export interface ListHistoryRequest {
  tenantId: string;
  sessionId: string;
  requestId: string;
  limit?: number;
}

/** Jupiter history — cross-run request/answer provenance. */
export interface HistoryClient {
  listHistory(request: ListHistoryRequest): Promise<HistoryEntry[]>;
}

export interface ScheduleSummary {
  scheduleId: string;
  sessionId: string;
  requestText: string;
  userId: string;
  targetPlanet: string;
  intervalSeconds: number;
  status: string;
  nextFireAt: string;
  lastFiredAt: string;
  lastRunId: string;
  fireCount: number;
  createdAt: string;
}

export interface CreateScheduleRequest {
  tenantId: string;
  sessionId: string;
  requestId: string;
  requestText: string;
  intervalSeconds: number;
  targetPlanet?: string;
  scheduleId?: string;
}

export interface ListSchedulesRequest {
  tenantId: string;
  sessionId: string;
  requestId: string;
  limit?: number;
}

export interface UpdateScheduleRequest {
  tenantId: string;
  sessionId: string;
  requestId: string;
  scheduleId: string;
  status: "ACTIVE" | "PAUSED" | "DELETED";
}

/** Saturn — recurring tasks that fire Runs on a durable schedule. */
export interface ScheduleClient {
  createSchedule(request: CreateScheduleRequest): Promise<ScheduleSummary>;
  listSchedules(request: ListSchedulesRequest): Promise<ScheduleSummary[]>;
  updateSchedule(request: UpdateScheduleRequest): Promise<ScheduleSummary>;
}

export interface ProfileSummary {
  profileId: string;
  displayName: string;
  planetAffinity: string;
  modelRef: string;
  skillRefs: string[];
  permissionPolicy: Record<string, unknown>;
  updatedAt: string;
}

export interface UpsertProfileRequest {
  tenantId: string;
  sessionId: string;
  requestId: string;
  profileId: string;
  displayName?: string;
  planetAffinity?: string;
  modelRef?: string;
  skillRefs?: string[];
  permissionPolicy?: Record<string, unknown>;
}

export interface ListProfilesRequest {
  tenantId: string;
  sessionId: string;
  requestId: string;
  limit?: number;
}

/** Agent profiles — swappable planet bindings for delegated runs. */
export interface ProfileClient {
  upsertProfile(request: UpsertProfileRequest): Promise<ProfileSummary>;
  listProfiles(request: ListProfilesRequest): Promise<ProfileSummary[]>;
}

export interface SettingsSummary {
  providerMode: string;
  activeProvider: string;
  activeModel: string;
  deepseekModel: string;
  ollamaModel: string;
}

export interface GetSettingsRequest {
  tenantId: string;
  sessionId: string;
  requestId: string;
}

export interface UpdateSettingsRequest {
  tenantId: string;
  sessionId: string;
  requestId: string;
  providerMode?: string;
  deepseekModel?: string;
  ollamaModel?: string;
}

/** Runtime/provider settings surface. */
export interface SettingsClient {
  getSettings(request: GetSettingsRequest): Promise<SettingsSummary>;
  updateSettings(request: UpdateSettingsRequest): Promise<SettingsSummary>;
}

export interface WorkspaceSummary {
  workspaceId: string;
  displayName: string;
  capabilities: string[];
  defaultProfileId: string;
  description: string;
  updatedAt: string;
}

export interface UpsertWorkspaceRequest {
  tenantId: string;
  sessionId: string;
  requestId: string;
  workspaceId: string;
  displayName?: string;
  capabilities?: string[];
  defaultProfileId?: string;
  description?: string;
}

export interface ListWorkspacesRequest {
  tenantId: string;
  sessionId: string;
  requestId: string;
  limit?: number;
}

/** Planet/scene capability registry. */
export interface WorkspaceClient {
  upsertWorkspace(request: UpsertWorkspaceRequest): Promise<WorkspaceSummary>;
  listWorkspaces(request: ListWorkspacesRequest): Promise<WorkspaceSummary[]>;
}

export interface CapabilitySummary {
  capability: string;
  tools: string[];
}

export interface ListCapabilitiesRequest {
  tenantId: string;
  sessionId: string;
  requestId: string;
}

export interface WebSearchRequest {
  tenantId: string;
  sessionId: string;
  requestId: string;
  query: string;
  maxResults?: number;
}

export interface WebSearchResult {
  provider: string;
  content: string;
  sources: Array<Record<string, unknown>>;
}

export interface SkillSummary {
  skillId: string;
  name: string;
  description: string;
}

export interface ListSkillsRequest {
  tenantId: string;
  sessionId: string;
  requestId: string;
}

/** Real skill index — SKILL.md frontmatter registry. */
export interface SkillClient {
  listSkills(request: ListSkillsRequest): Promise<SkillSummary[]>;
}

/** Mercury — real web search via the backend provider chain. */
export interface WebSearchClient {
  webSearch(request: WebSearchRequest): Promise<WebSearchResult>;
}

/** Runtime capability registry — drives scene "not installed" states. */
export interface CapabilityClient {
  listCapabilities(request: ListCapabilitiesRequest): Promise<CapabilitySummary[]>;
}

export interface FactRecordSummary {
  category: string;
  key: string;
  value: string;
  scope: string;
  evidenceId: string;
  sourceKind: string;
  sourceRef: string;
  revision: number;
  createdAt: string;
}

export interface ListFactsRequest {
  tenantId: string;
  sessionId: string;
  requestId: string;
  scope?: string;
  limit?: number;
}

export interface ForgetFactRequest {
  tenantId: string;
  sessionId: string;
  requestId: string;
  category: string;
  key: string;
  scope?: string;
}

export interface FactForgetResult {
  category: string;
  key: string;
  removed: boolean;
}

export interface ParticipantSummary {
  participantId: string;
  role: string;
  joinedAt: string;
}

export interface ListParticipantsRequest {
  tenantId: string;
  sessionId: string;
  requestId: string;
}

export interface JupiterArtifactSummary {
  sessionId: string;
  runId: string;
  artifactId: string;
  artifactType: string;
  reference: string;
  digest: string;
  exists: boolean;
  verified: boolean;
  producerTaskId: string;
  updatedAt: string;
}

export interface SearchArtifactsRequest {
  tenantId: string;
  sessionId: string;
  requestId: string;
  query?: string;
  allSessions?: boolean;
  limit?: number;
}

/** Jupiter — cross-session artifact index. */
export interface JupiterClient {
  searchArtifacts(request: SearchArtifactsRequest): Promise<JupiterArtifactSummary[]>;
}

/** Session roster — owners and delegated agents. */
export interface ParticipantClient {
  listParticipants(request: ListParticipantsRequest): Promise<ParticipantSummary[]>;
}

/** Memory governance — see/forget what the agent remembers. */
export interface MemoryClient {
  listFacts(request: ListFactsRequest): Promise<FactRecordSummary[]>;
  forgetFact(request: ForgetFactRequest): Promise<FactForgetResult>;
}

/** Delegation records — Sun-side task tree edges. */
export interface HandoffClient {
  listHandoffs(request: ListHandoffsRequest): Promise<HandoffSummary[]>;
}

/** Session catalog — first-class identity scopes for runs/tasks. */
export interface SessionCatalogClient {
  createSession(request: CreateSessionRequest): Promise<SessionSummary>;
  listSessions(request: ListSessionsRequest): Promise<SessionSummary[]>;
}

/** Task tree catalog — delegation units that Runs attach to. */
export interface TaskCatalogClient {
  createTask(request: CreateTaskRequest): Promise<TaskHandle>;
  listTasks(): Promise<TaskRecord[]>;
  getTask(request: TaskLookupRequest): Promise<TaskRecord>;
}

export interface DesktopAgentServiceClient extends AgentServiceClient, RunCatalogClient, TaskCatalogClient, ApprovalClient, SessionCatalogClient, HandoffClient, HistoryClient, ScheduleClient, ProfileClient, SettingsClient, WorkspaceClient, CapabilityClient, WebSearchClient, SkillClient, MemoryClient, ParticipantClient, JupiterClient {
  readonly mode: DesktopAgentServiceMode;
  readonly identity: DesktopIdentity;
  /** Resolve the backend health before the UI begins using the client. */
  ready(): Promise<void>;
}
