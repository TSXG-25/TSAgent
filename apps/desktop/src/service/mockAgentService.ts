import { artifactTextFor, createMockRuns, createNewRun } from "../mocks/runData";
import type { RunView } from "../types";
import {
  AgentServiceClientError,
  HUMAN_EVALUATION_VERDICTS,
  type ArtifactSummary,
  type CancelRunRequest,
  type DesktopAgentServiceClient,
  type DesktopIdentity,
  type EventStreamRequest,
  type GetRunRequest,
  type ListArtifactsRequest,
  type ReadArtifactRequest,
  type ArtifactContent,
  type ArtifactPreview,
  type ApprovalSummary,
  type SessionScope,
  type SessionSummary,
  type HandoffSummary,
  type DelegateRunRequest,
  type CreateSessionRequest,
  type DelegateRunResult,
  type HistoryEntry,
  type ScheduleSummary,
  type CreateScheduleRequest,
  type UpdateScheduleRequest,
  type ListHistoryRequest,
  type ListApprovalsRequest,
  type UpdateSettingsRequest,
  type UpsertProfileRequest,
  type CreateTaskRequest,
  type CapabilitySummary,
  type WebSearchRequest,
  type WebSearchResult,
  type SkillSummary,
  type FactRecordSummary,
  type FactForgetResult,
  type ParticipantSummary,
  type JupiterArtifactSummary,
  type WorkflowDefinitionSummary,
  type WorkflowGraphSummary,
  type UpsertWorkflowGraphRequest,
  type RunWorkflowGraphRequest,
  type GraphExecutionRef,
  type GraphExecutionSummary,
  type GraphRunStepSummary,
  type MotifSummary,
  type WorkspaceFileSummary,
  type WorkspaceFileRequest,
  type WorkspaceFileContent,
  type EvaluationSummary,
  type ListEvaluationsRequest,
  type RecordEvaluationRequest,
  type UpsertWorkspaceRequest,
  type AssignSessionWorkspaceRequest,
  type DeleteWorkspaceRequest,
  type DirectoryListing,
  type DecideApprovalRequest,
  type ProfileSummary,
  type SettingsSummary,
  type WorkspaceSummary,
  type TaskHandle,
  type TaskRecord,
  type ResumeRunRequest,
  type RunEvent,
  type RunHandle,
  type RunSnapshot,
  type StartRunRequest,
  type ServiceErrorCode,
} from "../types/service";
import {
  mergeRunEvents,
  toRunView,
  toServiceArtifact,
  toServiceEvent,
  toServiceSnapshot,
} from "./viewMapper";

const LOCAL_TENANT_ID = "tenant-local";
const DEFAULT_IDENTITY: DesktopIdentity = {
  tenantId: LOCAL_TENANT_ID,
  userId: "user-local",
  sessionId: "session-desktop-mock",
};

function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function digestText(value: string): string {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return `fnv1a-${(hash >>> 0).toString(16).padStart(8, "0")}`;
}

function keyForRequest(tenantId: string, requestId: string): string {
  return `${tenantId}\u0000${requestId}`;
}

function keyForResume(tenantId: string, runId: string, resumeRequestId: string): string {
  return `${tenantId}\u0000${runId}\u0000${resumeRequestId}`;
}

function keyForCancel(tenantId: string, runId: string, requestId: string): string {
  return `${tenantId}\u0000${runId}\u0000${requestId}`;
}

function cancelDigest(request: CancelRunRequest): string {
  return digestText([request.sessionId, request.runId, request.requestedBy].join("\u0000"));
}

function clientError(
  code: ServiceErrorCode,
  message: string,
  options: Omit<ConstructorParameters<typeof AgentServiceClientError>[0], "code" | "message" | "retryable"> & {
    retryable?: boolean;
  } = {},
): AgentServiceClientError {
  return new AgentServiceClientError({ ...options, code, message, retryable: options.retryable ?? false });
}

function toHandle(snapshot: RunSnapshot): RunHandle {
  return {
    tenantId: snapshot.tenantId,
    sessionId: snapshot.sessionId,
    runId: snapshot.runId,
    requestId: snapshot.requestId,
    status: snapshot.status,
    revision: snapshot.revision,
    createdAt: snapshot.createdAt,
    updatedAt: snapshot.updatedAt,
  };
}

function event(
  snapshot: RunSnapshot,
  sequenceNumber: number,
  eventId: string,
  eventType: string,
  title: string,
  description: string,
  tone: "info" | "success" | "warning" | "error" | "neutral",
): RunEvent {
  return {
    eventId,
    sequenceNumber,
    eventType,
    tenantId: snapshot.tenantId,
    sessionId: snapshot.sessionId,
    runId: snapshot.runId,
    workflowId: snapshot.activeWorkflowId,
    runRevision: snapshot.revision,
    timestamp: "刚刚",
    payload: { title, description, tone },
  };
}

export class MockAgentServiceClient implements DesktopAgentServiceClient {
  readonly mode = "mock" as const;
  identity: DesktopIdentity;

  private readonly snapshots = new Map<string, RunSnapshot>();
  private readonly artifacts = new Map<string, ArtifactSummary[]>();
  private readonly artifactText = new Map<string, string>();
  private readonly events = new Map<string, RunEvent[]>();
  private readonly requestIndex = new Map<string, { digest: string; runId: string }>();
  private readonly resumeIndex = new Map<string, { checkpointId: string; action: ResumeRunRequest["action"]; runId: string }>();
  private readonly cancelIndex = new Map<string, { digest: string; runId: string }>();
  private readonly oldestRetainedSequence = new Map<string, number>();
  private nextRunSequence = 2050;

  constructor(seed: RunView[] = createMockRuns(), identity: DesktopIdentity = DEFAULT_IDENTITY) {
    this.identity = { ...identity };
    this.seedSchedules();
    seed.forEach((run, index) => this.addSeedRun(run, index));
  }

  async ready(): Promise<void> {
    // The mock has no external process to probe, but it still participates in
    // the same composition-root readiness contract as the local client.
  }

  async close(): Promise<void> {}

  /** Composition-root helper for the first render; the UI still gets data from this adapter. */
  listRunViewsSync(): RunView[] {
    return [...this.snapshots.values()].map((snapshot) =>
      toRunView(snapshot, this.artifacts.get(snapshot.runId) ?? [], this.events.get(snapshot.runId) ?? []),
    );
  }

  listRunsSync(scope?: SessionScope): RunSnapshot[] {
    return [...this.snapshots.values()]
      .filter((snapshot) => !scope?.sessionId || snapshot.sessionId === scope.sessionId)
      .filter((snapshot) => scope?.originPlanet === undefined || (snapshot.originPlanet ?? "") === scope.originPlanet)
      .map((snapshot) => clone(snapshot));
  }

  async listRuns(scope: SessionScope): Promise<RunSnapshot[]> {
    return this.listRunsSync(scope);
  }

  private tasks: TaskRecord[] = [];

  async createTask(request: CreateTaskRequest): Promise<TaskHandle> {
    const task: TaskRecord = {
      tenantId: this.identity.tenantId,
      sessionId: request.sessionId,
      taskId: request.taskId ?? `task-${Math.random().toString(16).slice(2, 10)}`,
      parentTaskId: request.parentTaskId ?? "",
      originPlanet: request.originPlanet ?? "",
      ownerProfileId: request.ownerProfileId ?? "",
      createdAt: new Date().toISOString(),
      status: "queued",
      runIds: [],
      childTaskIds: [],
    };
    this.tasks = [task, ...this.tasks];
    return task;
  }

  async listTasks(scope: SessionScope): Promise<TaskRecord[]> {
    return [...this.tasks.filter((t) => t.sessionId === scope.sessionId),
      ...[...this.snapshots.values()].filter((run) => run.sessionId === scope.sessionId).map((run) => ({
      tenantId: run.tenantId,
      sessionId: run.sessionId,
      taskId: run.taskId ?? `task-${run.runId}`,
      parentTaskId: "",
      originPlanet: run.originPlanet ?? "",
      ownerProfileId: "",
      createdAt: run.createdAt,
      status: run.status,
      runIds: [run.runId],
      childTaskIds: [],
    }))];
  }

  async getTask(): Promise<TaskRecord> {
    throw clientError("UNSUPPORTED_OPERATION", "mock client does not manage tasks");
  }

  private approvals: ApprovalSummary[] = [
    {
      approvalId: "appr-demo-01",
      runId: "run-2048",
      action: "file.write",
      scope: "workspace:/output",
      params: { path: "output/summary.md" },
      riskLevel: "R1",
      status: "PENDING",
      decidedBy: "",
      decidedAt: "",
      createdAt: new Date().toISOString(),
    },
  ];

  async listApprovals(request: ListApprovalsRequest): Promise<ApprovalSummary[]> {
    const status = request.status;
    return this.approvals.filter((a) => !status || a.status === status);
  }

  async decideApproval(request: DecideApprovalRequest): Promise<ApprovalSummary> {
    this.approvals = this.approvals.map((a) =>
      a.approvalId === request.approvalId
        ? { ...a, status: request.decision === "approve" ? "APPROVED" : "DENIED", decidedBy: request.decidedBy ?? "user", decidedAt: new Date().toISOString() }
        : a,
    );
    return this.approvals.find((a) => a.approvalId === request.approvalId)!;
  }

  async createSession(request: CreateSessionRequest): Promise<SessionSummary> {
    const session: SessionSummary = {
      tenantId: this.identity.tenantId,
      sessionId: request.sessionId || `session-${Math.random().toString(16).slice(2, 10)}`,
      userId: this.identity.userId,
      createdAt: new Date().toISOString(),
      runCount: 0,
      latestRunId: "",
      latestStatus: "",
      latestUpdatedAt: new Date().toISOString(),
      // first claim wins — same ownership contract as the sidecar store
      planet: this.sessionPlanets.get(request.sessionId) || request.planet || "",
    };
    if (session.planet) this.sessionPlanets.set(session.sessionId, session.planet);
    const existing = this.extraSessions.findIndex((s) => s.sessionId === session.sessionId);
    if (existing >= 0) this.extraSessions[existing] = { ...this.extraSessions[existing], planet: session.planet };
    else this.extraSessions = [session, ...this.extraSessions];
    return session;
  }

  private sessionPlanets = new Map<string, string>();

  async deleteSession(request: CreateSessionRequest): Promise<{ removed: boolean }> {
    const before = this.extraSessions.length;
    this.extraSessions = this.extraSessions.filter((s) => s.sessionId !== request.sessionId);
    this.removedSessions.add(request.sessionId);
    return { removed: this.extraSessions.length !== before || true };
  }

  private extraSessions: SessionSummary[] = [];
  private removedSessions = new Set<string>();

  async listSessions(): Promise<SessionSummary[]> {
    const bySession = new Map<string, SessionSummary>();
    for (const run of this.snapshots.values()) {
      if (!this.extraSessions.some((s) => s.sessionId === run.sessionId) && this.removedSessions.has(run.sessionId)) continue;
      const existing = bySession.get(run.sessionId);
      bySession.set(run.sessionId, {
        tenantId: run.tenantId,
        sessionId: run.sessionId,
        userId: this.identity.userId,
        createdAt: existing?.createdAt ?? run.createdAt,
        runCount: (existing?.runCount ?? 0) + 1,
        latestRunId: run.runId,
        latestStatus: run.status,
        latestUpdatedAt: run.updatedAt,
        workspaceId: this.sessionWorkspaces.get(run.sessionId),
        planet: this.sessionPlanets.get(run.sessionId),
      });
    }
    return [
      ...this.extraSessions.map((s) => ({ ...s, workspaceId: this.sessionWorkspaces.get(s.sessionId), planet: this.sessionPlanets.get(s.sessionId) ?? s.planet })),
      ...bySession.values(),
    ];
  }

  private handoffs: HandoffSummary[] = [
    {
      handoffId: "handoff-demo-01",
      sourceRunId: "run-2048",
      sourceTaskId: "task-run-2048",
      targetTaskId: "task-sub-research",
      targetRunId: "",
      targetPlanet: "mercury",
      contextRefs: ["artifact:research_notes"],
      approvalState: "not_required",
      createdAt: new Date().toISOString(),
    },
  ];

  async listHandoffs(): Promise<HandoffSummary[]> {
    return [...this.handoffs];
  }

  async upsertWorkspace(request: UpsertWorkspaceRequest): Promise<WorkspaceSummary> {
    const next: WorkspaceSummary = {
      workspaceId: request.workspaceId,
      displayName: request.displayName ?? "",
      path: request.path ?? "",
      paths: request.paths ?? (request.path ? [request.path] : []),
      capabilities: request.capabilities ?? [],
      defaultProfileId: request.defaultProfileId ?? "",
      description: request.description ?? "",
      updatedAt: new Date().toISOString(),
    };
    this.workspaces = [next, ...this.workspaces.filter((w) => w.workspaceId !== next.workspaceId)];
    return next;
  }

  private sessionWorkspaces = new Map<string, string>();

  async assignSessionWorkspace(
    request: AssignSessionWorkspaceRequest,
  ): Promise<{ sessionId: string; workspaceId: string }> {
    if (request.workspaceId) this.sessionWorkspaces.set(request.sessionId, request.workspaceId);
    else this.sessionWorkspaces.delete(request.sessionId);
    return { sessionId: request.sessionId, workspaceId: request.workspaceId };
  }

  async resolveSessionWorkspace(
    request: SessionScope,
  ): Promise<{ sessionId: string; workspaceId: string; path: string; bound: boolean }> {
    const boundId = this.sessionWorkspaces.get(request.sessionId) ?? "";
    if (boundId) {
      const path = this.workspaces.find((w) => w.workspaceId === boundId)?.path ?? "";
      return { sessionId: request.sessionId, workspaceId: boundId, path, bound: true };
    }
    // Mirror the backend admission contract: unbound sessions get an
    // app-managed root, registered and bound so it is a visible fact.
    const workspaceId = `ws-auto-${request.sessionId}`;
    const path = `/mock/workspaces/${request.sessionId}`;
    const auto = {
      workspaceId, displayName: `session ${request.sessionId} workspace`,
      path, paths: [path], capabilities: [] as string[],
      defaultProfileId: "", description: "", updatedAt: new Date().toISOString(),
    };
    this.workspaces = [auto, ...this.workspaces.filter((w) => w.workspaceId !== workspaceId)];
    this.sessionWorkspaces.set(request.sessionId, workspaceId);
    return { sessionId: request.sessionId, workspaceId, path, bound: true };
  }

  async deleteWorkspace(request: DeleteWorkspaceRequest): Promise<{ removed: boolean }> {
    const before = this.workspaces.length;
    this.workspaces = this.workspaces.filter((w) => w.workspaceId !== request.workspaceId);
    for (const [sid, wsId] of this.sessionWorkspaces) {
      if (wsId === request.workspaceId) this.sessionWorkspaces.delete(sid);
    }
    return { removed: this.workspaces.length !== before };
  }

  async pickFolder(): Promise<{ path: string | null }> {
    // Deterministic fake so the workspace-file flow is exercisable in mock mode.
    return { path: "/mock/picked-workspace" };
  }

  async pickFiles(): Promise<{ paths: string[] }> {
    return { paths: ["/mock/outside/report.txt"] };
  }

  async attachFiles(request: { paths: string[] }): Promise<{ contextRefs: string[] }> {
    return {
      contextRefs: request.paths.map(
        (p) => `file:.attachments/${p.split("/").filter(Boolean).pop()}`,
      ),
    };
  }

  async listDirectory(path: string): Promise<DirectoryListing> {
    const current = path || "/mock";
    return { path: current, parent: "/", dirs: ["docs/", "src/", "output/"], home: "/mock" };
  }

  async revealPath(): Promise<{ revealed: boolean }> {
    return { revealed: false };
  }

  private workspaces: WorkspaceSummary[] = [
    {
      workspaceId: "ws-forge-default",
      displayName: "Forge Default",
      capabilities: ["file_write", "code_execution"],
      defaultProfileId: "profile-forge",
      description: "Engineering workspace for Mars/Forge runs",
      updatedAt: new Date().toISOString(),
    },
  ];

  async listWorkspaces(): Promise<WorkspaceSummary[]> {
    return this.workspaces.map((w) => ({
      ...w,
      memberCount: [...this.sessionWorkspaces.values()].filter((id) => id === w.workspaceId).length,
    }));
  }

  private settings: SettingsSummary = {
    providerMode: "api",
    activeProvider: "deepseek",
    activeModel: "deepseek-chat",
    deepseekModel: "deepseek-chat",
    ollamaModel: "llama3.1",
  };

  async updateSettings(request: UpdateSettingsRequest): Promise<SettingsSummary> {
    this.settings = {
      ...this.settings,
      ...(request.providerMode !== undefined ? { providerMode: request.providerMode } : {}),
      ...(request.deepseekModel !== undefined ? { deepseekModel: request.deepseekModel } : {}),
      ...(request.ollamaModel !== undefined ? { ollamaModel: request.ollamaModel } : {}),
      ...(request.providerMode === "api" ? { activeProvider: "deepseek", activeModel: this.settings.deepseekModel } : {}),
      ...(request.providerMode === "local" ? { activeProvider: "ollama", activeModel: this.settings.ollamaModel } : {}),
    };
    return { ...this.settings };
  }

  async getSettings(): Promise<SettingsSummary> {
    return { ...this.settings };
  }

  private profiles: ProfileSummary[] = [
    {
      profileId: "profile-navigator",
      displayName: "Navigator",
      planetAffinity: "mercury",
      modelRef: "deepseek-chat",
      skillRefs: ["browser", "web_search"],
      permissionPolicy: { risk: "R1" },
      updatedAt: new Date().toISOString(),
    },
    {
      profileId: "profile-forge",
      displayName: "Forge",
      planetAffinity: "mars",
      modelRef: "deepseek-chat",
      skillRefs: ["filesystem", "terminal"],
      permissionPolicy: { risk: "R2" },
      updatedAt: new Date().toISOString(),
    },
  ];

  async upsertProfile(request: UpsertProfileRequest): Promise<ProfileSummary> {
    const next: ProfileSummary = {
      profileId: request.profileId,
      displayName: request.displayName ?? "",
      planetAffinity: request.planetAffinity ?? "",
      modelRef: request.modelRef ?? "",
      skillRefs: request.skillRefs ?? [],
      permissionPolicy: request.permissionPolicy ?? {},
      updatedAt: new Date().toISOString(),
    };
    this.profiles = [next, ...this.profiles.filter((p) => p.profileId !== next.profileId)];
    return next;
  }

  async listProfiles(): Promise<ProfileSummary[]> {
    return [...this.profiles];
  }

  async webSearch(request: WebSearchRequest): Promise<WebSearchResult> {
    return {
      provider: "duckduckgo",
      content: `Results for "${request.query}" — mock search returns canned content; the local client calls the real provider chain.`,
      sources: [],
    };
  }

  private wsFiles = new Map<string, string>([
    ["main.py", 'def main():\n    print("forge")\n'],
    ["spec.md", "# Spec\n\nDraft spec for the forge workspace.\n"],
    ["lib/util.py", "def clamp(v, lo, hi):\n    return max(lo, min(hi, v))\n"],
  ]);

  private evals: Array<{ summary: EvaluationSummary; tenantId: string; sessionId: string }> = [{
    summary: {
      evalId: "eval-mock-1", name: "baseline A vs candidate B", runId: "run-2048",
      baselineRef: "artifact-a", candidateRef: "artifact-b",
      score: 0.62, verdict: "pass", notes: "mock seed", createdAt: new Date().toISOString(),
    },
    tenantId: LOCAL_TENANT_ID, sessionId: DEFAULT_IDENTITY.sessionId,
  }];

  async recordEvaluation(request: RecordEvaluationRequest): Promise<EvaluationSummary> {
    const verdict = request.verdict ?? "neutral";
    if (!(HUMAN_EVALUATION_VERDICTS as readonly string[]).includes(verdict)) {
      throw clientError(
        "INVALID_REQUEST",
        `verdict must be one of ${HUMAN_EVALUATION_VERDICTS.join(", ")}`,
      );
    }
    const ev: EvaluationSummary = {
      evalId: request.evalId || `eval-${Math.random().toString(16).slice(2, 10)}`,
      name: request.name ?? "", runId: request.runId ?? "",
      baselineRef: request.baselineRef ?? "", candidateRef: request.candidateRef ?? "",
      score: request.score ?? 0, verdict,
      notes: request.notes ?? "", createdAt: new Date().toISOString(),
    };
    this.evals = [{ summary: ev, tenantId: this.identity.tenantId, sessionId: request.sessionId }, ...this.evals];
    return ev;
  }

  async listEvaluations(request: ListEvaluationsRequest): Promise<EvaluationSummary[]> {
    return this.evals
      .filter((e) => e.tenantId === this.identity.tenantId && (request.allSessions || e.sessionId === request.sessionId))
      .map((e) => e.summary);
  }

  async listWorkspaceFiles(): Promise<WorkspaceFileSummary[]> {
    return [...this.wsFiles.entries()].map(([path, text]) => ({
      path,
      size: text.length,
      modifiedAt: new Date().toISOString(),
    }));
  }

  async readWorkspaceFile(request: WorkspaceFileRequest): Promise<WorkspaceFileContent> {
    const text = this.wsFiles.get(request.path);
    if (text === undefined) throw clientError("RUN_NOT_FOUND", `workspace file not found: ${request.path}`);
    return { path: request.path, text, digest: `mock-${request.path}-${text.length}`, redacted: false, workspaceRoot: "mock-ws" };
  }

  async writeWorkspaceFile(request: WorkspaceFileRequest): Promise<WorkspaceFileContent> {
    if (request.workspaceRoot && request.workspaceRoot !== "mock-ws") {
      throw clientError("WORKSPACE_CHANGED", `document not bound to the active workspace: ${request.path}`);
    }
    const text = this.wsFiles.get(request.path) ?? "";
    if (request.baseDigest && request.baseDigest !== `mock-${request.path}-${text.length}`) {
      throw clientError("STALE_DOCUMENT", `file changed since it was read: ${request.path}`);
    }
    this.wsFiles.set(request.path, request.content ?? "");
    const next = this.wsFiles.get(request.path) ?? "";
    return { path: request.path, text: "saved", digest: `mock-${request.path}-${next.length}`, redacted: false, workspaceRoot: "mock-ws" };
  }

  private workflowGraphs: WorkflowGraphSummary[] = [];
  private graphExecutions = new Map<string, GraphExecutionSummary>();

  async upsertWorkflowGraph(request: UpsertWorkflowGraphRequest): Promise<WorkflowGraphSummary> {
    const existing = this.workflowGraphs.find((g) => g.graphId === request.graphId);
    const summary: WorkflowGraphSummary = {
      graphId: request.graphId,
      name: request.name ?? existing?.name ?? "",
      definitionJson: JSON.stringify(request.definition ?? {}),
      owner: request.owner ?? existing?.owner ?? "",
      createdAt: existing?.createdAt ?? new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };
    this.workflowGraphs = [summary, ...this.workflowGraphs.filter((g) => g.graphId !== summary.graphId)];
    return summary;
  }

  async createMotif(): Promise<MotifSummary> {
    return {} as MotifSummary;
  }

  async assignMotif(): Promise<DelegateRunResult> {
    return {} as DelegateRunResult;
  }

  async resolveMotif(): Promise<MotifSummary[]> {
    return [];
  }

  async reportMotif(): Promise<MotifSummary[]> {
    return [];
  }

  async rejectMotif(): Promise<MotifSummary[]> {
    return [];
  }

  async listMotifs(): Promise<MotifSummary[]> {
    return [];
  }

  async runGraph(request: RunWorkflowGraphRequest): Promise<GraphRunStepSummary[]> {
    const graph = this.workflowGraphs.find((g) => g.graphId === request.graphId);
    if (!graph) throw clientError("RUN_NOT_FOUND", `workflow graph not found: ${request.graphId}`);
    const definition = JSON.parse(graph.definitionJson) as { nodes?: Array<{ id?: string }> };
    const executionId = `gexec:${request.graphId}:${request.requestId}`;
    const steps: GraphRunStepSummary[] = (definition.nodes ?? [])
      .filter((n) => n.id)
      .map((n) => ({
        nodeId: String(n.id), taskId: "", runId: "", sessionId: "",
        status: "PENDING", executionId,
      }));
    this.graphExecutions.set(executionId, {
      executionId, graphId: request.graphId, sessionId: request.sessionId, steps,
    });
    return steps;
  }

  async getGraphExecution(request: { executionId: string }): Promise<GraphExecutionSummary> {
    return this.graphExecutions.get(request.executionId)
      ?? { executionId: request.executionId, graphId: "", sessionId: "", steps: [] };
  }

  async listGraphExecutions(): Promise<GraphExecutionRef[]> {
    return [...this.graphExecutions.values()].map((e) => ({
      executionId: e.executionId,
      graphId: e.graphId,
      updatedAt: new Date().toISOString(),
    }));
  }

  async listWorkflowGraphs(): Promise<WorkflowGraphSummary[]> {
    return [...this.workflowGraphs];
  }

  async listWorkflows(): Promise<WorkflowDefinitionSummary[]> {
    return [];
  }

  async searchArtifacts(): Promise<JupiterArtifactSummary[]> {
    return [];
  }

  async listParticipants(): Promise<ParticipantSummary[]> {
    return [];
  }

  async listFacts(): Promise<FactRecordSummary[]> {
    const f = (category: string, key: string, value: string, rev = 1): FactRecordSummary => ({
      category, key, value, scope: "user", evidenceId: "ev-mock", sourceKind: "manual",
      sourceRef: "demo", revision: rev, createdAt: "demo",
    });
    return [
      f("preference", "code_style", "简洁直接，少注释", 4),
      f("preference", "ui_density", "紧凑高密度"),
      f("preference", "review_tone", "客观直白，不要讨好"),
      f("project", "tsagent_stack", "Tauri + React + Python sidecar", 6),
      f("project", "theme", "黑蓝机甲 + 行星壁纸"),
      f("project", "layout", "Omni v3 四段结构", 3),
      f("motif", "motif-01", "桌面端任务工作台形态", 2),
      f("user", "lang", "中文交流"),
      f("user", "role", "产品/设计评审"),
      f("sys", "sidecar_port", "4173 dev · 60192 preview"),
      f("memory", "scope", "user 级持久记忆", 2),
      f("workflow", "graph_demo", "model→profile→skills→@planet"),
    ];
  }

  async forgetFact(): Promise<FactForgetResult> {
    return {} as FactForgetResult;
  }

  async delegateRun(request: DelegateRunRequest): Promise<DelegateRunResult> {
    const sourceTaskId = request.parentRunId
      ? `task-${request.parentRunId}`
      : request.parentTaskId ?? "";
    const task = await this.createTask({
      sessionId: request.sessionId,
      requestId: request.requestId,
      parentTaskId: sourceTaskId,
      originPlanet: request.targetPlanet ?? "",
    });
    const handoff: HandoffSummary = {
      handoffId: `handoff-${Math.random().toString(16).slice(2, 10)}`,
      sourceRunId: request.parentRunId ?? "",
      sourceTaskId,
      targetTaskId: task.taskId,
      targetRunId: "",
      targetPlanet: request.targetPlanet ?? "",
      contextRefs: request.contextRefs ?? [],
      approvalState: "not_required",
      createdAt: new Date().toISOString(),
    };
    this.handoffs = [handoff, ...this.handoffs];
    return { taskId: task.taskId, handoffId: handoff.handoffId, sessionId: request.sessionId, targetPlanet: request.targetPlanet ?? "" };
  }

  async listSkills(): Promise<SkillSummary[]> {
    return [
      { skillId: "web-research", name: "Web Research", description: "Search and cite sources for a topic" },
      { skillId: "code-refactor", name: "Code Refactor", description: "Refactor a module keeping tests green" },
    ];
  }

  async listCapabilities(): Promise<CapabilitySummary[]> {
    return [
      { capability: "file_read", tools: ["read_file"] },
      { capability: "file_write", tools: ["write_file"] },
      { capability: "web_search", tools: ["duckduckgo_search"] },
      { capability: "code_execution", tools: ["run_python"] },
    ];
  }

  private schedules: ScheduleSummary[] = [];

  private seedSchedules(): void {
    this.schedules = [
    {
      scheduleId: "sched-nightly-index",
      sessionId: this.identity.sessionId,
      userId: this.identity.userId,
      requestText: "同步知识索引并产出摘要 artifact",
      autoEval: false,
      targetPlanet: "jupiter",
      intervalSeconds: 86400,
      status: "ACTIVE",
      nextFireAt: new Date(Date.now() + 86400e3).toISOString(),
      lastFiredAt: "",
      lastRunId: "",
      fireCount: 0,
      createdAt: new Date().toISOString(),
    },
    {
      scheduleId: "sched-weekly-report",
      sessionId: this.identity.sessionId,
      userId: this.identity.userId,
      requestText: "起草本周进展汇报（Venus 草稿）",
      autoEval: false,
      targetPlanet: "venus",
      intervalSeconds: 604800,
      status: "PAUSED",
      nextFireAt: new Date(Date.now() + 604800e3).toISOString(),
      lastFiredAt: "",
      lastRunId: "",
      fireCount: 0,
      createdAt: new Date().toISOString(),
    },
    ];
  }

  async createSchedule(request: CreateScheduleRequest): Promise<ScheduleSummary> {
    const schedule: ScheduleSummary = {
      scheduleId: request.scheduleId ?? `sched-${Math.random().toString(16).slice(2, 10)}`,
      sessionId: request.sessionId,
      userId: this.identity.userId,
      requestText: request.requestText,
      autoEval: request.autoEval ?? false,
      targetPlanet: request.targetPlanet ?? "",
      intervalSeconds: request.intervalSeconds,
      status: "ACTIVE",
      nextFireAt: new Date(Date.now() + request.intervalSeconds * 1000).toISOString(),
      lastFiredAt: "",
      lastRunId: "",
      fireCount: 0,
      createdAt: new Date().toISOString(),
    };
    this.schedules = [schedule, ...this.schedules];
    return schedule;
  }

  async listSchedules(): Promise<ScheduleSummary[]> {
    return [...this.schedules];
  }

  async updateSchedule(request: UpdateScheduleRequest): Promise<ScheduleSummary> {
    this.schedules = this.schedules.map((s) =>
      s.scheduleId === request.scheduleId ? { ...s, status: request.status } : s,
    );
    return this.schedules.find((s) => s.scheduleId === request.scheduleId)!;
  }

  async listHistory(request: ListHistoryRequest): Promise<HistoryEntry[]> {
    return this.listRuns({ sessionId: request.sessionId })
      .then((runs) => runs.slice(0, request.limit ?? 200).map((run) => ({
        runId: run.runId,
        taskId: "",
        requestText: run.requestText,
        answerText: "",
        status: run.status,
        updatedAt: run.updatedAt,
      })));
  }

  async startRun(request: StartRunRequest): Promise<RunHandle> {
    const requestId = request.requestId ?? `mock-start-${crypto.randomUUID()}`;
    if (!request.sessionId || !requestId.trim() || !request.requestText.trim()) {
      throw clientError("INVALID_REQUEST", "A session, request ID, and request text are required.", { requestId });
    }

    const digest = request.requestDigest ?? digestText(request.requestText);
    const requestKey = keyForRequest(this.identity.tenantId, requestId);
    const existing = this.requestIndex.get(requestKey);
    if (existing) {
      if (existing.digest !== digest) {
        throw clientError("IDEMPOTENCY_CONFLICT", "The request ID is already used for different request content.", { requestId });
      }
      const existingRun = this.snapshots.get(existing.runId);
      if (existingRun) return toHandle(clone(existingRun));
    }

    const run = createNewRun(request.requestText, this.nextRunSequence);
    this.nextRunSequence += 1;
    const snapshot = toServiceSnapshot(run, {
      tenantId: this.identity.tenantId,
      requestId,
      revision: 1,
    });
    snapshot.sessionId = request.sessionId;
    snapshot.taskId = request.taskId || `task-${snapshot.runId}`;
    snapshot.originPlanet = request.originPlanet ?? "";
    snapshot.updatedAt = "刚刚";
    snapshot.createdAt = "刚刚";
    const serviceEvents = run.events.map((item, index) => toServiceEvent(item, snapshot, snapshot.revision, index + 1));

    this.snapshots.set(snapshot.runId, snapshot);
    this.artifacts.set(snapshot.runId, run.artifacts.map((item) => toServiceArtifact(item, snapshot)));
    for (const item of run.artifacts) {
      this.artifactText.set(item.artifactId, artifactTextFor(item.path, item.status === "verified"));
    }
    this.events.set(snapshot.runId, serviceEvents);
    this.oldestRetainedSequence.set(snapshot.runId, 1);
    this.requestIndex.set(requestKey, { digest, runId: snapshot.runId });
    return toHandle(clone(snapshot));
  }

  async getRun(request: GetRunRequest): Promise<RunSnapshot> {
    return clone(this.getScopedRun(request));
  }

  async cancelRun(request: CancelRunRequest): Promise<RunSnapshot> {
    const requestId = request.requestId ?? `mock-cancel-${crypto.randomUUID()}`;
    if (!requestId.trim() || !request.requestedBy.trim()) {
      throw clientError("INVALID_REQUEST", "A cancellation request ID and requester are required.", {
        requestId,
        runId: request.runId,
      });
    }

    const snapshot = this.getScopedRun(request);
    const cancelKey = keyForCancel(this.identity.tenantId, request.runId, requestId);
    const digest = cancelDigest(request);
    const previous = this.cancelIndex.get(cancelKey);
    if (previous) {
      if (previous.digest !== digest) {
        throw clientError("IDEMPOTENCY_CONFLICT", "The cancellation request ID is already used for different cancellation content.", {
          requestId: request.requestId,
          runId: request.runId,
        });
      }
      return clone(this.snapshots.get(previous.runId) ?? snapshot);
    }

    if (snapshot.status === "cancelling") {
      throw clientError("RUN_ALREADY_CANCELLING", "Cancellation is already converging for this Run.", {
        requestId: request.requestId,
        runId: request.runId,
      });
    }
    if (snapshot.status === "cancelled") {
      throw clientError("ALREADY_CANCELLED", "This Run is already cancelled.", { runId: request.runId });
    }
    if (snapshot.status === "completed") {
      throw clientError("ALREADY_COMPLETED", "This Run is already completed.", { runId: request.runId });
    }
    if (snapshot.status !== "active") {
      throw clientError("RUN_NOT_CANCELLABLE", "This Run is not currently cancellable.", { runId: request.runId });
    }

    const cancelling = withCancellationStatus(snapshot, "cancelling");
    this.snapshots.set(snapshot.runId, cancelling);
    this.cancelIndex.set(cancelKey, { digest, runId: snapshot.runId });
    this.appendEvent(
      cancelling,
      "run_cancelling",
      "Cancellation requested",
      "Durable cancellation accepted; waiting for a safe boundary.",
      "warning",
    );

    globalThis.setTimeout(() => {
      const current = this.snapshots.get(snapshot.runId);
      if (!current || current.status !== "cancelling") return;
      const cancelled = withCancellationStatus(current, "cancelled");
      this.snapshots.set(snapshot.runId, cancelled);
      this.appendEvent(
        cancelled,
        "run_cancelled",
        "Run cancelled",
        "Cancellation converged at a safe boundary. Verified artifacts are preserved.",
        "warning",
      );
    }, 120);

    return clone(cancelling);
  }

  async pauseRun(): Promise<RunSnapshot> {
    return {} as RunSnapshot;
  }

  async resumeRun(request: ResumeRunRequest): Promise<RunHandle> {
    const snapshot = this.getScopedRun(request);
    const resumeKey = keyForResume(this.identity.tenantId, request.runId, request.resumeRequestId);
    const previous = this.resumeIndex.get(resumeKey);
    if (previous) {
      if (previous.checkpointId !== request.checkpointId || previous.action !== request.action) {
        throw clientError("IDEMPOTENCY_CONFLICT", "The resume request ID is already used for a different resume action.", {
          requestId: request.resumeRequestId,
          runId: request.runId,
        });
      }
      return toHandle(clone(this.snapshots.get(previous.runId) ?? snapshot));
    }

    if (snapshot.status === "completed") {
      throw clientError("ALREADY_COMPLETED", "This Run is already completed.", { runId: request.runId });
    }
    if (snapshot.status === "active") {
      throw clientError("RUN_ALREADY_ACTIVE", "This Run is already active.", { runId: request.runId });
    }
    if (snapshot.status !== "blocked" || snapshot.resumeSummary?.outcome !== "ready") {
      throw clientError("RESUME_NOT_ALLOWED", "This Run has no recoverable checkpoint.", { runId: request.runId });
    }

    const nextRevision = snapshot.revision + 1;
    const resumedSnapshot: RunSnapshot = {
      ...snapshot,
      status: "completed",
      revision: nextRevision,
      updatedAt: "刚刚",
      completedWorkflows: snapshot.workflows.map((workflow) => workflow.workflowId),
      pendingWorkflows: [],
      workflows: snapshot.workflows.map((workflow) => ({
        ...workflow,
        status: "completed",
        progress: 100,
        stages: workflow.stages.map((stage) => ({
          ...stage,
          status: stage.stageId === "verification" ? "verified" : "completed",
          duration: stage.stageId === "verification" ? "00:35" : stage.duration,
          tasks: stage.tasks.map((task) => ({ ...task, status: "completed" })),
        })),
      })),
      resumeSummary: {
        ...snapshot.resumeSummary,
        action: request.action,
        outcome: "completed",
        resumedAt: "刚刚",
      },
      verifierSummary: {
        status: "verified",
        checks: "3 / 3 checks",
        stdout: "49",
        detail: "exit_code、stdout 与 artifact integrity 均已通过。",
      },
      conversation: [
        ...snapshot.conversation,
        {
          messageId: `${snapshot.runId}.resume`,
          role: "assistant",
          content: `已从 ${request.checkpointId} 恢复执行。跳过已完成的 Analysis，重新进入 Implementation 并完成 Verification。`,
          at: "刚刚",
        },
      ],
    };

    this.snapshots.set(snapshot.runId, resumedSnapshot);
    this.artifacts.set(
      snapshot.runId,
      (this.artifacts.get(snapshot.runId) ?? []).map((artifact) => ({
        ...artifact,
        verified: true,
        createdRevision: nextRevision,
        createdAt: "刚刚",
      })),
    );
    for (const artifact of this.artifacts.get(snapshot.runId) ?? []) {
      const text = this.artifactText.get(artifact.artifactId) ?? "";
      this.artifactText.set(
        artifact.artifactId,
        artifact.displayName === "output/verification.json"
          ? text.replace('"awaiting_resume"', '"verified"').replace('"checks": []', '"checks": ["exit_code", "stdout", "artifact"]')
          : artifact.displayName === "logs/stdout.txt"
            ? "49\n"
            : text,
      );
    }

    const runEvents = this.events.get(snapshot.runId) ?? [];
    const nextSequence = (runEvents[runEvents.length - 1]?.sequenceNumber ?? 0) + 1;
    this.events.set(snapshot.runId, [
      ...runEvents,
      event(
        resumedSnapshot,
        nextSequence,
        `${snapshot.runId}.resumed`,
        "resume.selected",
        "Resume exact accepted",
        `${request.checkpointId} · resumed without replaying Analysis.`,
        "info",
      ),
      event(
        resumedSnapshot,
        nextSequence + 1,
        `${snapshot.runId}.verified`,
        "run.verified",
        "Run verified",
        "exit_code 0 · stdout matched expected value 49.",
        "success",
      ),
    ]);
    this.resumeIndex.set(resumeKey, {
      checkpointId: request.checkpointId,
      action: request.action,
      runId: request.runId,
    });
    return toHandle(clone(resumedSnapshot));
  }

  private appendEvent(
    snapshot: RunSnapshot,
    eventType: string,
    title: string,
    description: string,
    tone: "info" | "success" | "warning" | "error" | "neutral",
  ): void {
    const runEvents = this.events.get(snapshot.runId) ?? [];
    const nextSequence = (runEvents[runEvents.length - 1]?.sequenceNumber ?? 0) + 1;
    this.events.set(snapshot.runId, [
      ...runEvents,
      event(snapshot, nextSequence, `${snapshot.runId}.${eventType}.${nextSequence}`, eventType, title, description, tone),
    ]);
  }

  async listArtifacts(request: ListArtifactsRequest): Promise<ArtifactSummary[]> {
    this.getScopedRun(request);
    return clone(this.artifacts.get(request.runId) ?? []);
  }

  async readArtifact(request: ReadArtifactRequest): Promise<ArtifactContent> {
    this.getScopedRun(request);
    const artifact = (this.artifacts.get(request.runId) ?? []).find((a) => a.artifactId === request.artifactId);
    if (!artifact) {
      throw clientError("INVALID_REQUEST", `Artifact not found: ${request.artifactId}`, { runId: request.runId });
    }
    return {
      artifactId: artifact.artifactId,
      reference: artifact.reference,
      text: this.artifactText.get(artifact.artifactId) ?? "",
      stale: false,
      result: "ok",
    };
  }

  async readArtifactPreview(request: ReadArtifactRequest): Promise<ArtifactPreview> {
    this.getScopedRun(request);
    const artifact = (this.artifacts.get(request.runId) ?? []).find((a) => a.artifactId === request.artifactId);
    if (!artifact) {
      throw clientError("INVALID_REQUEST", `Artifact not found: ${request.artifactId}`, { runId: request.runId });
    }
    return {
      artifactId: artifact.artifactId,
      mime: "",
      dataB64: "",
      size: 0,
      stale: false,
      result: "unsupported",
    };
  }

  async subscribeEvents(request: EventStreamRequest): Promise<RunEvent[]> {
    // Simulate the long-poll hold — real sidecar blocks ~8s; the mock paces
    // the loop so chained subscriptions don't hot-spin.
    await new Promise((resolve) => setTimeout(resolve, 250));
    return this.readEvents(request);
  }

  async readEvents(request: EventStreamRequest): Promise<RunEvent[]> {
    this.getScopedRun(request);
    if (!Number.isInteger(request.afterSequence) || request.afterSequence < -1) {
      throw clientError("CURSOR_INVALID", "afterSequence must be an integer greater than or equal to -1.", {
        runId: request.runId,
      });
    }

    const oldest = this.oldestRetainedSequence.get(request.runId) ?? 1;
    // -1 is the explicit initial-load sentinel used by the desktop client.
    // It must remain valid even when the retained window starts at sequence 1.
    if (request.afterSequence !== -1 && request.afterSequence < oldest - 1) {
      throw clientError("EVENT_CURSOR_EXPIRED", "The event cursor is older than the retained event window.", {
        runId: request.runId,
      });
    }

    const events = (this.events.get(request.runId) ?? [])
      .filter((item) => item.sequenceNumber > request.afterSequence)
      .sort((left, right) => left.sequenceNumber - right.sequenceNumber);
    return clone(typeof request.limit === "number" ? events.slice(0, Math.max(0, request.limit)) : events);
  }

  /** Test/demo hook for exercising EVENT_CURSOR_EXPIRED without a real store. */
  setOldestRetainedSequence(runId: string, sequenceNumber: number): void {
    this.oldestRetainedSequence.set(runId, Math.max(1, Math.floor(sequenceNumber)));
  }

  private addSeedRun(run: RunView, index: number): void {
    const snapshot = toServiceSnapshot(run, {
      tenantId: LOCAL_TENANT_ID,
      requestId: `seed-${run.runId}`,
      revision: index + 1,
    });
    const serviceEvents = run.events.map((item, eventIndex) => toServiceEvent(item, snapshot, snapshot.revision, eventIndex + 1));
    const serviceArtifacts = run.artifacts.map((item) => toServiceArtifact(item, snapshot));
    for (const item of run.artifacts) {
      this.artifactText.set(item.artifactId, artifactTextFor(item.path, item.status === "verified"));
    }
    this.snapshots.set(run.runId, snapshot);
    this.events.set(run.runId, serviceEvents);
    this.artifacts.set(run.runId, serviceArtifacts);
    this.oldestRetainedSequence.set(run.runId, 1);
    this.requestIndex.set(keyForRequest(snapshot.tenantId, snapshot.requestId), {
      digest: digestText(snapshot.requestText),
      runId: run.runId,
    });
  }

  private getScopedRun(request: { sessionId: string; runId: string }): RunSnapshot {
    const snapshot = this.snapshots.get(request.runId);
    if (!snapshot || snapshot.tenantId !== this.identity.tenantId || snapshot.sessionId !== request.sessionId) {
      // Keep scope mismatches indistinguishable from missing Runs.
      throw clientError("RUN_NOT_FOUND", "Run not found.");
    }
    return snapshot;
  }
}

function withCancellationStatus(
  snapshot: RunSnapshot,
  status: "cancelling" | "cancelled",
): RunSnapshot {
  return {
    ...snapshot,
    status,
    revision: snapshot.revision + 1,
    updatedAt: "刚刚",
    workflows: snapshot.workflows.map((workflow) => ({
      ...workflow,
      status: workflow.status === "active" ? status : workflow.status,
      stages: workflow.stages.map((stage) => ({
        ...stage,
        status: stage.status === "running" ? "interrupted" : stage.status,
        tasks: stage.tasks.map((task) => ({
          ...task,
          status: task.status === "running" ? "interrupted" : task.status,
        })),
      })),
    })),
  };
}
