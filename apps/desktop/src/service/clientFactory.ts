import {
  AgentServiceClientError,
  type DesktopAgentServiceClient,
  type DesktopAgentServiceMode,
  type DesktopIdentity,
  type RunEvent,
  type RunHandle,
  type RunSnapshot,
  type ArtifactContent,
  type ArtifactPreview,
  type ArtifactSummary,
  type StartRunRequest,
  type GetRunRequest,
  type CancelRunRequest,
  type ApprovalSummary,
  type SessionSummary,
  type HandoffSummary,
  type HistoryEntry,
  type ScheduleSummary,
  type ProfileSummary,
  type CapabilitySummary,
  type WebSearchResult,
  type SkillSummary,
  type DelegateRunResult,
  type FactRecordSummary,
  type FactForgetResult,
  type ParticipantSummary,
  type JupiterArtifactSummary,
  type WorkflowDefinitionSummary,
  type WorkflowGraphSummary,
  type GraphExecutionRef,
  type GraphExecutionSummary,
  type GraphRunStepSummary,
  type MotifSummary,
  type WorkspaceFileSummary,
  type WorkspaceFileContent,
  type EvaluationSummary,
  type SettingsSummary,
  type WorkspaceSummary,
  type DirectoryListing,
  type TaskHandle,
  type TaskRecord,
  type PauseRunRequest,
  type ResumeRunRequest,
  type ListArtifactsRequest,
  type EventStreamRequest,
} from "../types/service";
import { LocalAgentServiceClient } from "./localAgentServiceClient";
import { MockAgentServiceClient } from "./mockAgentService";
import {
  TauriSidecarTransport,
  type TauriSidecarBridge,
} from "./tauriSidecarTransport";
import { HttpLocalTransport } from "./httpTransport";

export interface DesktopClientFactoryConfig {
  /** `mock` is the browser-development default; `local` is always explicit. */
  mode?: string;
  identity?: Partial<DesktopIdentity>;
  bridge?: TauriSidecarBridge;
  /** Loopback URL of the HTTP sidecar used when no bridge is present. */
  sidecarUrl?: string;
  requestTimeoutMs?: number;
}

const DEFAULT_MOCK_IDENTITY: DesktopIdentity = {
  tenantId: "tenant-local",
  userId: "user-local",
  sessionId: "session-desktop-mock",
};

let generatedLocalSessionId: string | undefined;

function generatedSessionId(): string {
  if (generatedLocalSessionId) return generatedLocalSessionId;
  const suffix = globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
  generatedLocalSessionId = `session-desktop-${suffix}`;
  return generatedLocalSessionId;
}

function identityForMode(
  mode: DesktopAgentServiceMode,
  configured: Partial<DesktopIdentity> | undefined,
): DesktopIdentity {
  const defaults = mode === "mock" ? DEFAULT_MOCK_IDENTITY : { tenantId: "", userId: "", sessionId: generatedSessionId() };
  return {
    tenantId: configured?.tenantId?.trim() || defaults.tenantId,
    userId: configured?.userId?.trim() || defaults.userId,
    sessionId: configured?.sessionId?.trim() || defaults.sessionId,
  };
}

function unavailableError(code: "INVALID_REQUEST" | "PROVIDER_UNAVAILABLE", message: string): AgentServiceClientError {
  return new AgentServiceClientError({ code, message, retryable: code === "PROVIDER_UNAVAILABLE" });
}

/**
 * A visible failure client keeps the composition root total: selecting local
 * mode with incomplete host configuration renders Backend unavailable. It
 * never substitutes the Mock client.
 */
export class UnavailableDesktopAgentServiceClient implements DesktopAgentServiceClient {
  readonly mode: DesktopAgentServiceMode;
  identity: DesktopIdentity;

  constructor(mode: DesktopAgentServiceMode, identity: DesktopIdentity, private readonly error: AgentServiceClientError) {
    this.mode = mode;
    this.identity = identity;
  }

  async ready(): Promise<void> {
    throw this.error;
  }

  async close(): Promise<void> {}

  async listRuns(scope: { tenantId: string; sessionId: string }): Promise<RunSnapshot[]> {
    void scope;
    throw this.error;
  }

  async createTask(): Promise<TaskHandle> {
    throw this.error;
  }

  async listTasks(scope: { tenantId: string; sessionId: string }): Promise<TaskRecord[]> {
    void scope;
    throw this.error;
  }

  async getTask(): Promise<TaskRecord> {
    throw this.error;
  }

  async listApprovals(): Promise<ApprovalSummary[]> {
    throw this.error;
  }

  async decideApproval(): Promise<ApprovalSummary> {
    throw this.error;
  }

  async deleteSession(): Promise<{ removed: boolean }> {
    throw this.error;
  }

  async createSession(): Promise<SessionSummary> {
    throw this.error;
  }

  async listWindows(): Promise<never> { throw this.error; }
  async openWindow(): Promise<never> { throw this.error; }
  async closeWindow(): Promise<never> { throw this.error; }
  async navigateWindow(): Promise<never> { throw this.error; }
  async claimWindow(): Promise<never> { throw this.error; }
  async releaseWindow(): Promise<never> { throw this.error; }
  async displayTicket(): Promise<never> { throw this.error; }

  async listSessions(): Promise<SessionSummary[]> {
    throw this.error;
  }

  async listHandoffs(): Promise<HandoffSummary[]> {
    throw this.error;
  }

  async upsertWorkspace(): Promise<WorkspaceSummary> {
    throw this.error;
  }

  async listWorkspaces(): Promise<WorkspaceSummary[]> {
    throw this.error;
  }

  async assignSessionWorkspace(): Promise<{ sessionId: string; workspaceId: string }> {
    throw this.error;
  }

  async resolveSessionWorkspace(): Promise<{ sessionId: string; workspaceId: string; path: string; bound: boolean }> {
    throw this.error;
  }

  async deleteWorkspace(): Promise<{ removed: boolean }> {
    throw this.error;
  }

  async pickFolder(): Promise<{ path: string | null }> {
    throw this.error;
  }

  async pickFiles(): Promise<{ paths: string[] }> {
    throw this.error;
  }

  async attachFiles(): Promise<{ contextRefs: string[] }> {
    throw this.error;
  }

  async listDirectory(): Promise<DirectoryListing> {
    throw this.error;
  }

  async revealPath(): Promise<{ revealed: boolean }> {
    throw this.error;
  }

  async updateSettings(): Promise<SettingsSummary> {
    throw this.error;
  }

  async getSettings(): Promise<SettingsSummary> {
    throw this.error;
  }

  async upsertProfile(): Promise<ProfileSummary> {
    throw this.error;
  }

  async listProfiles(): Promise<ProfileSummary[]> {
    throw this.error;
  }

  async listCapabilities(): Promise<CapabilitySummary[]> {
    throw this.error;
  }

  async webSearch(): Promise<WebSearchResult> {
    throw this.error;
  }

  async recordEvaluation(): Promise<EvaluationSummary> {
    throw this.error;
  }

  async listEvaluations(): Promise<EvaluationSummary[]> {
    throw this.error;
  }

  async listWorkspaceFiles(): Promise<WorkspaceFileSummary[]> {
    throw this.error;
  }

  async readWorkspaceFile(): Promise<WorkspaceFileContent> {
    throw this.error;
  }

  async writeWorkspaceFile(): Promise<WorkspaceFileContent> {
    throw this.error;
  }

  async upsertWorkflowGraph(): Promise<WorkflowGraphSummary> {
    throw this.error;
  }

  async createMotif(): Promise<MotifSummary> {
    throw this.error;
  }

  async assignMotif(): Promise<DelegateRunResult> {
    throw this.error;
  }

  async resolveMotif(): Promise<MotifSummary[]> {
    throw this.error;
  }

  async reportMotif(): Promise<MotifSummary[]> {
    throw this.error;
  }

  async rejectMotif(): Promise<MotifSummary[]> {
    throw this.error;
  }

  async listMotifs(): Promise<MotifSummary[]> {
    throw this.error;
  }

  async runGraph(): Promise<GraphRunStepSummary[]> {
    throw this.error;
  }

  async getGraphExecution(): Promise<GraphExecutionSummary> {
    throw this.error;
  }

  async listGraphExecutions(): Promise<GraphExecutionRef[]> {
    throw this.error;
  }

  async listWorkflowGraphs(): Promise<WorkflowGraphSummary[]> {
    throw this.error;
  }

  async listWorkflows(): Promise<WorkflowDefinitionSummary[]> {
    throw this.error;
  }

  async searchArtifacts(): Promise<JupiterArtifactSummary[]> {
    throw this.error;
  }

  async listParticipants(): Promise<ParticipantSummary[]> {
    throw this.error;
  }

  async listFacts(): Promise<FactRecordSummary[]> {
    throw this.error;
  }

  async forgetFact(): Promise<FactForgetResult> {
    throw this.error;
  }

  async listSkills(): Promise<SkillSummary[]> {
    throw this.error;
  }

  async delegateRun(): Promise<DelegateRunResult> {
    throw this.error;
  }

  async createSchedule(): Promise<ScheduleSummary> {
    throw this.error;
  }

  async listSchedules(): Promise<ScheduleSummary[]> {
    throw this.error;
  }

  async updateSchedule(): Promise<ScheduleSummary> {
    throw this.error;
  }

  async listHistory(): Promise<HistoryEntry[]> {
    throw this.error;
  }

  async startRun(_request: StartRunRequest): Promise<RunHandle> {
    throw this.error;
  }

  async getRun(_request: GetRunRequest): Promise<RunSnapshot> {
    throw this.error;
  }

  async cancelRun(_request: CancelRunRequest): Promise<RunSnapshot> {
    throw this.error;
  }

  async pauseRun(_request: PauseRunRequest): Promise<RunHandle> {
    throw this.error;
  }

  async resumeRun(_request: ResumeRunRequest): Promise<RunHandle> {
    throw this.error;
  }

  async listArtifacts(_request: ListArtifactsRequest): Promise<ArtifactSummary[]> {
    throw this.error;
  }

  async readArtifact(): Promise<ArtifactContent> {
    throw this.error;
  }

  async readArtifactPreview(): Promise<ArtifactPreview> {
    throw this.error;
  }

  async readEvents(_request: EventStreamRequest): Promise<RunEvent[]> {
    throw this.error;
  }

  async subscribeEvents(_request: EventStreamRequest): Promise<RunEvent[]> {
    throw this.error;
  }
}

function unavailable(
  mode: DesktopAgentServiceMode,
  identity: DesktopIdentity,
  code: "INVALID_REQUEST" | "PROVIDER_UNAVAILABLE",
  message: string,
): DesktopAgentServiceClient {
  return new UnavailableDesktopAgentServiceClient(mode, identity, unavailableError(code, message));
}

export function createAgentServiceClientFromConfig(config: DesktopClientFactoryConfig = {}): DesktopAgentServiceClient {
  const rawMode = config.mode?.trim() || "mock";
  const mode: DesktopAgentServiceMode = rawMode === "local" ? "local" : "mock";
  const identity = identityForMode(mode, config.identity);

  if (rawMode !== "mock" && rawMode !== "local") {
    return unavailable("mock", identity, "INVALID_REQUEST", `unsupported desktop service mode: ${rawMode}`);
  }

  if (mode === "mock") {
    return new MockAgentServiceClient(undefined, identity);
  }

  if (!identity.tenantId || !identity.userId) {
    return unavailable(
      mode,
      identity,
      "INVALID_REQUEST",
      "local AgentService requires VITE_TSAGENT_TENANT_ID and VITE_TSAGENT_USER_ID",
    );
  }

  if (!config.bridge) {
    // Browser dev mode: no host bridge — talk to the sidecar's HTTP
    // transport instead (`python -m agent.service.local_sidecar --http`).
    const httpTransport = new HttpLocalTransport(
      config.sidecarUrl ?? "http://127.0.0.1:8765",
      config.requestTimeoutMs ?? 30000,
    );
    return new LocalAgentServiceClient(httpTransport, { identity });
  }

  const transport = new TauriSidecarTransport(config.bridge, {
    ...(config.requestTimeoutMs === undefined ? {} : { requestTimeoutMs: config.requestTimeoutMs }),
  });
  return new LocalAgentServiceClient(transport, { identity });
}

function injectedSidecarBridge(): TauriSidecarBridge | undefined {
  if (typeof window === "undefined") return undefined;
  return window.__TSAGENT_SIDECAR_BRIDGE__;
}

/**
 * Desktop composition root. The mode is explicit and local failures are
 * surfaced as an unavailable backend; there is no implicit Mock fallback.
 */
export function createAgentServiceClient(): DesktopAgentServiceClient {
  return createAgentServiceClientFromConfig({
    mode: import.meta.env.VITE_AGENT_SERVICE_MODE,
    identity: {
      tenantId: import.meta.env.VITE_TSAGENT_TENANT_ID,
      userId: import.meta.env.VITE_TSAGENT_USER_ID,
      sessionId: import.meta.env.VITE_TSAGENT_SESSION_ID,
    },
    bridge: injectedSidecarBridge(),
    sidecarUrl: import.meta.env.VITE_TSAGENT_SIDECAR_URL,
  });
}
