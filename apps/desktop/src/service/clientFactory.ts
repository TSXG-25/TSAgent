import {
  AgentServiceClientError,
  type DesktopAgentServiceClient,
  type DesktopAgentServiceMode,
  type DesktopIdentity,
  type RunEvent,
  type RunHandle,
  type RunSnapshot,
  type ArtifactSummary,
  type StartRunRequest,
  type GetRunRequest,
  type CancelRunRequest,
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

export interface DesktopClientFactoryConfig {
  /** `mock` is the browser-development default; `local` is always explicit. */
  mode?: string;
  identity?: Partial<DesktopIdentity>;
  bridge?: TauriSidecarBridge;
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
class UnavailableDesktopAgentServiceClient implements DesktopAgentServiceClient {
  readonly mode: DesktopAgentServiceMode;
  readonly identity: DesktopIdentity;

  constructor(mode: DesktopAgentServiceMode, identity: DesktopIdentity, private readonly error: AgentServiceClientError) {
    this.mode = mode;
    this.identity = identity;
  }

  async ready(): Promise<void> {
    throw this.error;
  }

  async listRuns(): Promise<RunSnapshot[]> {
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

  async resumeRun(_request: ResumeRunRequest): Promise<RunHandle> {
    throw this.error;
  }

  async listArtifacts(_request: ListArtifactsRequest): Promise<ArtifactSummary[]> {
    throw this.error;
  }

  async readEvents(_request: EventStreamRequest): Promise<RunEvent[]> {
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
    return unavailable(
      mode,
      identity,
      "PROVIDER_UNAVAILABLE",
      "local AgentService backend is unavailable; no sidecar bridge is configured",
    );
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
  });
}
