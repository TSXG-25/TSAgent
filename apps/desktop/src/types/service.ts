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
  sessionId: string;
  requestId?: string;
  requestText: string;
  /** Session-bound workspace root — scopes the run's file ops to this dir. */
  workspaceRoot?: string;
  /** Model pick (agent_profile.model_ref) and other run metadata. */
  metadata?: Record<string, unknown>;
  /** Anchor the run under an existing task (delegated child runs). */
  taskId?: string;
  /** Route recorded on the run's implicit task (scheduler/delegation). */
  originPlanet?: string;
  /** Attachment refs (file:/task:/run:) — validated and recorded on start. */
  contextRefs?: string[];
  requestDigest?: string;
}

export interface GetRunRequest {
  sessionId: string;
  runId: string;
}

export interface CancelRunRequest {
  sessionId: string;
  runId: string;
  requestId?: string;
  requestedBy: string;
}

export type ResumeAction =
  | "RESUME_EXACT"
  | "REPLAY_FROM_STAGE"
  | "REPLAN_FROM_CHECKPOINT"
  | "ABANDON_AND_RESTART";

export interface PauseRunRequest {
  sessionId: string;
  runId: string;
  requestId?: string;
  requestedBy?: string;
}

export interface ResumeRunRequest {
  sessionId: string;
  runId: string;
  resumeRequestId: string;
  checkpointId: string;
  action: ResumeAction;
}

export interface ListArtifactsRequest {
  sessionId: string;
  runId: string;
}

export interface EventStreamRequest {
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
}

export interface ReadArtifactRequest {
  sessionId: string;
  runId: string;
  artifactId: string;
}

/** Artifact file content; `stale` means the workspace file changed since
 *  the artifact digest was recorded. */
export interface ArtifactContent {
  artifactId: string;
  reference: string;
  text: string;
  stale: boolean;
  /** "ok" | "missing" | "unsupported" — text+stale describe one byte read. */
  result: string;
}

/** Guarded binary preview — base64 payload capped server-side; `mime`
 *  comes from a fixed allowlist, never client sniffing. */
export interface ArtifactPreview {
  artifactId: string;
  mime: string;
  dataB64: string;
  size: number;
  stale: boolean;
  /** "ok" | "missing" | "unsupported" | "too_large" */
  result: string;
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
  /** Owning task + workstation route — durable facts, never route-derived. */
  taskId?: string;
  originPlanet?: string;
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
  | "STALE_DOCUMENT"
  | "READ_ONLY_DOCUMENT"
  | "WORKSPACE_CHANGED"
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
  pauseRun(request: PauseRunRequest): Promise<RunHandle>;
  listArtifacts(request: ListArtifactsRequest): Promise<ArtifactSummary[]>;
  readArtifact(request: ReadArtifactRequest): Promise<ArtifactContent>;
  readArtifactPreview(request: ReadArtifactRequest): Promise<ArtifactPreview>;
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
  sessionId: string;
  requestId?: string;
  taskId?: string;
  parentTaskId?: string;
  originPlanet?: string;
  ownerProfileId?: string;
}

export interface TaskLookupRequest {
  sessionId: string;
  requestId?: string;
  taskId: string;
}

/** Run List is a desktop catalog capability; the core contract remains above. */
export interface RunCatalogClient {
  listRuns(scope: SessionScope): Promise<RunSnapshot[]>;
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
  sessionId: string;
  requestId?: string;
  runId?: string;
  status?: string;
  limit?: number;
}

export interface DecideApprovalRequest {
  sessionId: string;
  requestId?: string;
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
  /** Latest run status decoded at the client boundary ("" = no runs). */
  latestStatus: RunStatus | "";
  latestUpdatedAt: string;
  /** Workspace this session is filed under ("" = recent/ungrouped). */
  workspaceId?: string;
  /** Workstation this session is owned by ("" = unclaimed). */
  planet?: string;
}

export interface CreateSessionRequest {
  sessionId: string;
  requestId?: string;
  /** Claim the session for a workstation — explicit adoption/creation. */
  planet?: string;
}

/** Explicit session scope required by per-session catalog reads — callers
 * pass the workstation's own session; nothing is read off mutable identity.
 * `sessionId: ""` = tenant-wide catalog (Omni aggregate); `originPlanet`
 * filters durable workstation ownership ("" = unclassified). */
export interface SessionScope {
  sessionId: string;
  originPlanet?: string;
}

export interface ListSessionsRequest {
  requestId?: string;
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
  sessionId: string;
  requestId?: string;
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
  sessionId: string;
  requestId?: string;
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
  autoEval: boolean;
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
  sessionId: string;
  requestId?: string;
  requestText: string;
  intervalSeconds: number;
  targetPlanet?: string;
  scheduleId?: string;
  autoEval?: boolean;
}

export interface ListSchedulesRequest {
  sessionId: string;
  requestId?: string;
  limit?: number;
}

export interface UpdateScheduleRequest {
  sessionId: string;
  requestId?: string;
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
  sessionId: string;
  requestId?: string;
  profileId: string;
  displayName?: string;
  planetAffinity?: string;
  modelRef?: string;
  skillRefs?: string[];
  permissionPolicy?: Record<string, unknown>;
}

export interface ListProfilesRequest {
  sessionId: string;
  requestId?: string;
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
  sessionId: string;
  requestId?: string;
}

export interface UpdateSettingsRequest {
  sessionId: string;
  requestId?: string;
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
  path?: string;
  /** All source folders; `path` is the primary one used for run scoping. */
  paths?: string[];
  memberCount?: number;
  capabilities: string[];
  defaultProfileId: string;
  description: string;
  updatedAt: string;
}

export interface UpsertWorkspaceRequest {
  /** Primary directory — scopes run file ops. Must be a member of `paths`. */
  path?: string;
  /** All source folders for this workspace. */
  paths?: string[];
  sessionId: string;
  requestId?: string;
  workspaceId: string;
  displayName?: string;
  capabilities?: string[];
  defaultProfileId?: string;
  description?: string;
}

export interface ListWorkspacesRequest {
  sessionId: string;
  requestId?: string;
  limit?: number;
}

export interface AssignSessionWorkspaceRequest {
  /** Session being filed — the target, which may differ from the caller's session. */
  sessionId: string;
  requestId?: string;
  /** "" moves the session back to the unfiled list. */
  workspaceId: string;
}

export interface DeleteWorkspaceRequest {
  /** Caller's current session — the wire DTO requires it as a locator. */
  sessionId: string;
  requestId?: string;
  workspaceId: string;
}

/** In-page folder-browser payload from the sidecar's `list_directory`. */
export interface DirectoryListing {
  path: string;
  parent: string;
  dirs: string[];
  home?: string;
}

/** Planet/scene capability registry. */
export interface WorkspaceClient {
  upsertWorkspace(request: UpsertWorkspaceRequest): Promise<WorkspaceSummary>;
  listWorkspaces(request: ListWorkspacesRequest): Promise<WorkspaceSummary[]>;
  assignSessionWorkspace(
    request: AssignSessionWorkspaceRequest,
  ): Promise<{ sessionId: string; workspaceId: string }>;
  /** Effective workspace root — the admission contract: durable binding,
   *  or the backend's resolver/app-managed default (registered + bound).
   *  Never Home or cwd. */
  resolveSessionWorkspace(
    request: SessionScope,
  ): Promise<{ sessionId: string; workspaceId: string; path: string; bound: boolean }>;
  deleteWorkspace(request: DeleteWorkspaceRequest): Promise<{ removed: boolean }>;
  /** Native folder picker — resolves to the picked path or null on cancel. */
  pickFolder(): Promise<{ path: string | null }>;
  /** Native multi-file picker — resolves to picked absolute paths (empty on cancel). */
  pickFiles(): Promise<{ paths: string[] }>;
  /** Copy external files into the session workspace; returns file: context refs. */
  attachFiles(request: AttachFilesRequest): Promise<{ contextRefs: string[] }>;
  listDirectory(path: string): Promise<DirectoryListing>;
  /** Open a path in the OS file manager (Finder on macOS). */
  revealPath(path: string): Promise<{ revealed: boolean }>;
}

export interface CapabilitySummary {
  capability: string;
  tools: string[];
}

export interface ListCapabilitiesRequest {
  sessionId: string;
  requestId?: string;
}

export interface WebSearchRequest {
  sessionId: string;
  requestId?: string;
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
  sessionId: string;
  requestId?: string;
}

export interface DelegateRunRequest {
  sessionId: string;
  requestId?: string;
  /** Anchor under an existing Run — continuation of its task tree. */
  parentRunId?: string;
  /** Anchor under a bare Task — a fresh send that owns no Run yet. */
  parentTaskId?: string;
  requestText: string;
  targetPlanet?: string;
  profileId?: string;
  contextRefs?: string[];
}

export interface DelegateRunResult {
  taskId: string;
  handoffId: string;
  sessionId: string;
  targetPlanet: string;
  /** Provisioned child start config — forward into the start_run call. */
  requestText?: string;
  workspaceRoot?: string;
  agentProfile?: Record<string, unknown>;
}

/** Durable child-task + handoff provisioning for UI delegation. */
export interface DelegationClient {
  delegateRun(request: DelegateRunRequest): Promise<DelegateRunResult>;
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
  sessionId: string;
  requestId?: string;
  scope?: string;
  limit?: number;
}

export interface ForgetFactRequest {
  sessionId: string;
  requestId?: string;
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
  sessionId: string;
  requestId?: string;
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
  sessionId: string;
  requestId?: string;
  query?: string;
  allSessions?: boolean;
  limit?: number;
}

export interface WorkspaceFileSummary {
  path: string;
  size: number;
  modifiedAt: string;
}

export interface WorkspaceFileContent {
  path: string;
  text: string;
  digest: string;
  redacted: boolean;
  workspaceRoot: string;
}

export interface ListWorkspaceFilesRequest {
  sessionId: string;
  requestId?: string;
  limit?: number;
}

export interface AttachFilesRequest {
  sessionId: string;
  requestId?: string;
  paths: string[];
}

export interface WorkspaceFileRequest {
  sessionId: string;
  requestId?: string;
  path: string;
  content?: string;
  baseDigest?: string;
  workspaceRoot?: string;
}

/** Forge workspace filesystem — guarded read/write surface. */
export interface WorkspaceFilesClient {
  listWorkspaceFiles(request: ListWorkspaceFilesRequest): Promise<WorkspaceFileSummary[]>;
  readWorkspaceFile(request: WorkspaceFileRequest): Promise<WorkspaceFileContent>;
  writeWorkspaceFile(request: WorkspaceFileRequest): Promise<WorkspaceFileContent>;
}

/** evaluations.verdict — two explicit categories share the column:
 *  scheduled execution outcomes (Saturn) and human A/B verdicts (Earth).
 *  Mirrors EVALUATION_VERDICTS in agent/runtime_store/sqlite.py. */
export const EVALUATION_VERDICTS = [
  "pass", "fail", "neutral",
  "keep_candidate", "keep_baseline", "inconclusive",
] as const;
export type EvaluationVerdict = typeof EVALUATION_VERDICTS[number];
/** The human-facing vocabulary — record_evaluation accepts only these. */
export const HUMAN_EVALUATION_VERDICTS = [
  "keep_candidate", "keep_baseline", "inconclusive",
] as const;

export interface EvaluationSummary {
  evalId: string;
  name: string;
  runId: string;
  baselineRef: string;
  candidateRef: string;
  score: number;
  verdict: string;
  notes: string;
  createdAt: string;
}

export interface RecordEvaluationRequest {
  sessionId: string;
  requestId?: string;
  name?: string;
  runId?: string;
  baselineRef?: string;
  candidateRef?: string;
  score?: number;
  verdict?: EvaluationVerdict;
  notes?: string;
  evalId?: string;
}

export interface ListEvaluationsRequest {
  sessionId: string;
  requestId?: string;
  allSessions?: boolean;
  limit?: number;
}

/** Earth — experiment verdicts with baseline/candidate provenance. */
export interface EvaluationClient {
  recordEvaluation(request: RecordEvaluationRequest): Promise<EvaluationSummary>;
  listEvaluations(request: ListEvaluationsRequest): Promise<EvaluationSummary[]>;
}

export interface WorkflowDefinitionSummary {
  workflowId: string;
  description: string;
  version: string;
}

export interface ListWorkflowsRequest {
  sessionId: string;
  requestId?: string;
}

export interface WorkflowGraphSummary {
  graphId: string;
  name: string;
  definitionJson: string;
  owner: string;
  createdAt: string;
  updatedAt: string;
}

export interface UpsertWorkflowGraphRequest {
  sessionId: string;
  requestId?: string;
  graphId: string;
  name?: string;
  definition?: Record<string, unknown>;
  owner?: string;
}

export interface ListWorkflowGraphsRequest {
  sessionId: string;
  requestId?: string;
  limit?: number;
}

export interface MotifSummary {
  motifId: string;
  text: string;
  source: string;
  status: string;
  assignedTaskId: string;
  resolutionRunId: string;
  /** Session owning resolutionRunId — motifs are tenant-scoped, so the
   *  service projects this from the run head for cross-session links. */
  resolutionSessionId: string;
  resolution: string;
  createdAt: string;
  updatedAt: string;
}

export interface CreateMotifRequest {
  sessionId: string;
  requestId?: string;
  text: string;
  motifId?: string;
  source?: string;
}

export interface AssignMotifRequest {
  sessionId: string;
  requestId?: string;
  motifId: string;
  parentRunId: string;
}

export interface ResolveMotifRequest {
  sessionId: string;
  requestId?: string;
  motifId: string;
  resolutionRunId: string;
  resolution: string;
}

/** A run's reported understanding — evidence awaiting verdict. */
export interface ReportMotifRequest {
  sessionId: string;
  requestId?: string;
  motifId: string;
  resolutionRunId: string;
  resolution: string;
}

/** Human verdict rejecting a report — sends the motif back to exploring. */
export interface RejectMotifRequest {
  sessionId: string;
  requestId?: string;
  motifId: string;
}

export interface ListMotifsRequest {
  sessionId: string;
  requestId?: string;
  status?: string;
  limit?: number;
}

/** Pluto motif domain — motif-driven exploration with durable records. */
export interface MotifClient {
  createMotif(request: CreateMotifRequest): Promise<MotifSummary>;
  assignMotif(request: AssignMotifRequest): Promise<DelegateRunResult>;
  resolveMotif(request: ResolveMotifRequest): Promise<MotifSummary[]>;
  reportMotif(request: ReportMotifRequest): Promise<MotifSummary[]>;
  rejectMotif(request: RejectMotifRequest): Promise<MotifSummary[]>;
  listMotifs(request: ListMotifsRequest): Promise<MotifSummary[]>;
}

export interface GraphRunStepSummary {
  nodeId: string;
  taskId: string;
  runId: string;
  sessionId: string;
  /** Terminal status (COMPLETED, FAILED_*), or SKIPPED for dependents of a failed node. */
  status?: string;
  /** Durable execution identity — lookup key for getGraphExecution. */
  executionId?: string;
  /** Why a node is in its state (e.g. "predecessor not COMPLETED"). */
  detail?: string;
}

/** One graph execution's identity + live node steps. */
export interface GraphExecutionSummary {
  executionId: string;
  graphId: string;
  sessionId: string;
  steps: GraphRunStepSummary[];
}

export interface GraphExecutionLookupRequest {
  sessionId: string;
  requestId?: string;
  executionId: string;
}

/** A session's past graph executions (id + owning graph). */
export interface GraphExecutionRef {
  executionId: string;
  graphId: string;
  updatedAt: string;
}

export interface RunWorkflowGraphRequest {
  sessionId: string;
  requestId?: string;
  graphId: string;
  /** Empty runs the graph under its own durable parent Task — no Omni
   *  task needs to exist first (WI-07). */
  parentRunId: string;
  originPlanet?: string;
}

/** Uranus canvas persistence — durable node-graph definitions. */
export interface WorkflowGraphClient {
  upsertWorkflowGraph(request: UpsertWorkflowGraphRequest): Promise<WorkflowGraphSummary>;
  runGraph(request: RunWorkflowGraphRequest): Promise<GraphRunStepSummary[]>;
  getGraphExecution(request: GraphExecutionLookupRequest): Promise<GraphExecutionSummary>;
  listGraphExecutions(request: { sessionId: string; requestId?: string }): Promise<GraphExecutionRef[]>;
  listWorkflowGraphs(request: ListWorkflowGraphsRequest): Promise<WorkflowGraphSummary[]>;
}

/** Real workflow definitions — Uranus canvas data face. */
export interface WorkflowCatalogClient {
  listWorkflows(request: ListWorkflowsRequest): Promise<WorkflowDefinitionSummary[]>;
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
  deleteSession(request: CreateSessionRequest): Promise<{ removed: boolean }>;
  listSessions(request: ListSessionsRequest): Promise<SessionSummary[]>;
}

/** Task tree catalog — delegation units that Runs attach to. */
export interface TaskCatalogClient {
  createTask(request: CreateTaskRequest): Promise<TaskHandle>;
  listTasks(scope: SessionScope): Promise<TaskRecord[]>;
  getTask(request: TaskLookupRequest): Promise<TaskRecord>;
}

export interface DesktopAgentServiceClient extends AgentServiceClient, RunCatalogClient, TaskCatalogClient, ApprovalClient, SessionCatalogClient, HandoffClient, HistoryClient, ScheduleClient, ProfileClient, SettingsClient, WorkspaceClient, CapabilityClient, WebSearchClient, SkillClient, DelegationClient, MemoryClient, ParticipantClient, JupiterClient, WorkspaceFilesClient, EvaluationClient, WorkflowCatalogClient, WorkflowGraphClient, MotifClient {
  readonly mode: DesktopAgentServiceMode;
  readonly identity: DesktopIdentity;
  /** Resolve the backend health before the UI begins using the client. */
  ready(): Promise<void>;
  /** Release transport/sidecar resources. */
  close(): Promise<void>;
}
