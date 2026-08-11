import {
  AgentServiceClientError,
  type ServiceErrorCode,
} from "../types/service";
import {
  decodeLocalRpcResponse,
  encodeLocalRpcRequest,
  LocalTransportProtocolError,
  type LocalRpcParams,
  type LocalRpcResponse,
  type LocalTransport,
  type LocalTransportMethod,
} from "./localTransport";

/**
 * The Tauri/Rust layer owns the actual child-process implementation.  This
 * seam deliberately exposes only line I/O and lifecycle; it has no
 * AgentService, Runtime, SQLite, or cancellation semantics.
 */
export interface TauriSidecarProcess {
  writeLine(line: string): Promise<void> | void;
  onStdoutLine(listener: (line: string) => void): () => void;
  onExit(listener: (error?: unknown) => void): () => void;
  close(): Promise<void>;
}

export interface TauriSidecarBridge {
  spawn(): Promise<TauriSidecarProcess>;
}

export interface TauriSidecarTransportOptions {
  requestTimeoutMs?: number;
  requestIdFactory?: (sequence: number, method: LocalTransportMethod) => string;
  onProtocolError?: (error: LocalTransportProtocolError) => void;
}

interface PendingRequest {
  resolve: (value: unknown) => void;
  reject: (error: unknown) => void;
  timer: ReturnType<typeof globalThis.setTimeout>;
}

const DEFAULT_REQUEST_TIMEOUT_MS = 30_000;

const SERVICE_ERROR_CODES = new Set<ServiceErrorCode>([
  "INVALID_REQUEST",
  "IDENTITY_MISMATCH",
  "RUN_NOT_FOUND",
  "RUN_ALREADY_ACTIVE",
  "RUN_ALREADY_CANCELLING",
  "ALREADY_CANCELLED",
  "ALREADY_TIMED_OUT",
  "RUN_NOT_CANCELLABLE",
  "ALREADY_COMPLETED",
  "RESUME_NOT_ALLOWED",
  "IDEMPOTENCY_CONFLICT",
  "EVENT_CURSOR_EXPIRED",
  "EVENT_SEQUENCE_INVALID",
  "CURSOR_INVALID",
  "STORE_BUSY",
  "PROVIDER_UNAVAILABLE",
  "SERVICE_CLOSED",
  "UNSUPPORTED_OPERATION",
  "INTERNAL_ERROR",
]);

const SAFE_MESSAGES: Partial<Record<ServiceErrorCode, string>> = {
  INVALID_REQUEST: "request is invalid",
  IDENTITY_MISMATCH: "request identity is not valid for this scope",
  RUN_NOT_FOUND: "run was not found",
  RUN_ALREADY_ACTIVE: "run is already active",
  RUN_ALREADY_CANCELLING: "run is already cancelling",
  ALREADY_CANCELLED: "run is already cancelled",
  ALREADY_TIMED_OUT: "run has already timed out",
  RUN_NOT_CANCELLABLE: "run cannot be cancelled",
  ALREADY_COMPLETED: "run is already completed",
  RESUME_NOT_ALLOWED: "run cannot be resumed",
  IDEMPOTENCY_CONFLICT: "request conflicts with an existing operation",
  EVENT_CURSOR_EXPIRED: "event cursor is no longer readable",
  EVENT_SEQUENCE_INVALID: "event sequence is invalid",
  CURSOR_INVALID: "event cursor is invalid",
  STORE_BUSY: "durable store is busy",
  PROVIDER_UNAVAILABLE: "provider is unavailable",
  SERVICE_CLOSED: "service is closed",
  UNSUPPORTED_OPERATION: "operation is not supported",
  INTERNAL_ERROR: "internal service error",
};

function serviceCode(value: string): ServiceErrorCode {
  return SERVICE_ERROR_CODES.has(value as ServiceErrorCode)
    ? (value as ServiceErrorCode)
    : "INTERNAL_ERROR";
}

function stableMessage(code: ServiceErrorCode, message?: string): string {
  if (!message || /traceback|sqlite|database|workspace|api[_ -]?key|bearer|\/[A-Za-z]/i.test(message)) {
    return SAFE_MESSAGES[code] ?? SAFE_MESSAGES.INTERNAL_ERROR!;
  }
  return message;
}

function clientError(
  code: ServiceErrorCode,
  message: string,
  retryable: boolean,
): AgentServiceClientError {
  return new AgentServiceClientError({ code, message, retryable });
}

function transportError(
  message: string,
  code: ServiceErrorCode = "PROVIDER_UNAVAILABLE",
): AgentServiceClientError {
  return clientError(code, SAFE_MESSAGES[code] ?? message, code === "PROVIDER_UNAVAILABLE");
}

/**
 * JSONL transport implementation for a Tauri-provided sidecar process.
 *
 * Request IDs are generated here and correlated by a Map.  Response order is
 * intentionally irrelevant; unknown and duplicate responses are observable
 * protocol errors but never resolve or reject an unrelated request.
 */
export class TauriSidecarTransport implements LocalTransport {
  private readonly pendingRequests = new Map<string, PendingRequest>();
  private readonly requestTimeoutMs: number;
  private readonly requestIdFactory: (sequence: number, method: LocalTransportMethod) => string;
  private readonly onProtocolError?: (error: LocalTransportProtocolError) => void;
  private process: TauriSidecarProcess | null = null;
  private processStart: Promise<TauriSidecarProcess> | null = null;
  private removeStdoutListener: (() => void) | null = null;
  private removeExitListener: (() => void) | null = null;
  private terminalError: AgentServiceClientError | null = null;
  private closed = false;
  private requestSequence = 0;

  constructor(
    private readonly bridge: TauriSidecarBridge,
    options: TauriSidecarTransportOptions = {},
  ) {
    this.requestTimeoutMs = options.requestTimeoutMs ?? DEFAULT_REQUEST_TIMEOUT_MS;
    if (!Number.isFinite(this.requestTimeoutMs) || this.requestTimeoutMs <= 0) {
      throw new Error("requestTimeoutMs must be a positive finite number");
    }
    this.requestIdFactory =
      options.requestIdFactory ??
      ((sequence, method) => `local-${method}-${sequence}-${Date.now().toString(36)}`);
    this.onProtocolError = options.onProtocolError;
  }

  async request<TRequest extends LocalRpcParams, TResponse>(
    method: LocalTransportMethod,
    params: TRequest,
  ): Promise<TResponse> {
    if (this.closed) throw transportError("transport is closed", "SERVICE_CLOSED");
    if (this.terminalError) throw this.terminalError;
    if ((method === "health" || method === "shutdown") && Object.keys(params).length > 0) {
      throw clientError("INVALID_REQUEST", `${method} does not accept params`, false);
    }

    const sequence = ++this.requestSequence;
    const id = this.requestIdFactory(sequence, method);
    if (typeof id !== "string" || id.trim().length === 0) {
      throw clientError("INTERNAL_ERROR", "transport request ID factory returned an invalid ID", false);
    }
    if (this.pendingRequests.has(id)) {
      throw clientError("INTERNAL_ERROR", "transport request ID collision", false);
    }

    const process = await this.ensureProcess();
    const requestLine = encodeLocalRpcRequest({ id, method, params });

    return new Promise<TResponse>((resolve, reject) => {
      const timer = globalThis.setTimeout(() => {
        if (!this.pendingRequests.delete(id)) return;
        reject(transportError("local sidecar request timed out"));
      }, this.requestTimeoutMs);
      this.pendingRequests.set(id, {
        resolve: (value) => resolve(value as TResponse),
        reject,
        timer,
      });

      try {
        Promise.resolve(process.writeLine(requestLine)).catch((error: unknown) => {
          this.failTransport(error);
        });
      } catch (error) {
        this.failTransport(error);
      }
    });
  }

  async close(): Promise<void> {
    if (this.closed) return;
    this.closed = true;
    const closeError = transportError("transport is closed", "SERVICE_CLOSED");
    this.rejectAll(closeError);
    this.removeListeners();
    const process = this.process;
    this.process = null;
    this.processStart = null;
    if (process) {
      try {
        await process.close();
      } catch {
        // close is intentionally idempotent and must not leak process details.
      }
    }
  }

  private async ensureProcess(): Promise<TauriSidecarProcess> {
    if (this.closed) throw transportError("transport is closed", "SERVICE_CLOSED");
    if (this.terminalError) throw this.terminalError;
    if (this.process) return this.process;
    if (this.processStart) return this.processStart;

    this.processStart = this.bridge
      .spawn()
      .then((process) => {
        if (this.closed) {
          void process.close();
          throw transportError("transport is closed", "SERVICE_CLOSED");
        }
        this.process = process;
        this.removeStdoutListener = process.onStdoutLine((line) => this.handleLine(line));
        this.removeExitListener = process.onExit((error) => this.handleExit(error));
        return process;
      })
      .catch((error: unknown) => {
        if (error instanceof AgentServiceClientError) throw error;
        const unavailable = transportError("local sidecar is unavailable");
        this.terminalError = unavailable;
        throw unavailable;
      })
      .finally(() => {
        this.processStart = null;
      });
    return this.processStart;
  }

  private handleLine(line: string): void {
    let response: LocalRpcResponse;
    try {
      response = decodeLocalRpcResponse(line);
    } catch (error) {
      const protocolError =
        error instanceof LocalTransportProtocolError
          ? error
          : new LocalTransportProtocolError("sidecar stdout response is invalid");
      this.reportProtocolError(protocolError);
      this.failTransport(protocolError, "INTERNAL_ERROR");
      return;
    }

    const pending = this.pendingRequests.get(response.id);
    if (!pending) {
      this.reportProtocolError(
        new LocalTransportProtocolError(`response has no pending request: ${response.id}`),
      );
      return;
    }

    this.pendingRequests.delete(response.id);
    globalThis.clearTimeout(pending.timer);
    if (response.ok) {
      pending.resolve(response.result);
      return;
    }

    const code = serviceCode(response.error.code);
    pending.reject(
      new AgentServiceClientError({
        code,
        message: stableMessage(code, response.error.message),
        retryable: response.error.retryable,
        ...(response.error.run_id ? { runId: response.error.run_id } : {}),
        ...(response.error.request_id ? { requestId: response.error.request_id } : {}),
        ...(response.error.details
          ? {
              details: Object.fromEntries(
                Object.entries(response.error.details).filter(([, value]) => typeof value === "string"),
              ) as Record<string, string>,
            }
          : {}),
      }),
    );
  }

  private handleExit(error?: unknown): void {
    if (this.closed) return;
    this.removeListeners();
    this.process = null;
    this.terminalError = transportError(
      typeof error === "string" ? error : "local sidecar exited",
    );
    this.rejectAll(this.terminalError);
  }

  private failTransport(error: unknown, code: ServiceErrorCode = "PROVIDER_UNAVAILABLE"): void {
    if (this.closed) return;
    const message = error instanceof Error ? error.message : "local sidecar transport failed";
    this.terminalError = transportError(message, code);
    this.rejectAll(this.terminalError);
    const process = this.process;
    this.removeListeners();
    this.process = null;
    if (process) void process.close().catch(() => undefined);
  }

  private rejectAll(error: AgentServiceClientError): void {
    for (const [id, pending] of this.pendingRequests) {
      this.pendingRequests.delete(id);
      globalThis.clearTimeout(pending.timer);
      pending.reject(error);
    }
  }

  private removeListeners(): void {
    this.removeStdoutListener?.();
    this.removeExitListener?.();
    this.removeStdoutListener = null;
    this.removeExitListener = null;
  }

  private reportProtocolError(error: LocalTransportProtocolError): void {
    try {
      this.onProtocolError?.(error);
    } catch {
      // Diagnostics must never escape the transport callback and corrupt the
      // response correlation loop.
    }
  }
}

export default TauriSidecarTransport;
