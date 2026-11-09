/**
 * Desktop local JSON-lines transport contract.
 *
 * This file contains protocol facts only. Process lifecycle and request
 * correlation live in tauriSidecarTransport.ts; AgentService DTO mapping lives
 * in localAgentServiceClient.ts.
 */
import {
  AgentServiceClientError,
  type ServiceErrorCode,
} from "../types/service";

export const LOCAL_TRANSPORT_METHODS = [
  "health",
  "start_run",
  "get_run",
  "resume_run",
  "pause_run",
  "cancel_run",
  "list_artifacts",
  "read_artifact",
  "list_runs",
  "create_task",
  "list_tasks",
  "get_task",
  "list_approvals",
  "decide_approval",
  "create_session",
  "delete_session",
  "list_sessions",
  "list_handoffs",
  "list_history",
  "create_schedule",
  "list_schedules",
  "update_schedule",
  "upsert_profile",
  "list_profiles",
  "get_settings",
  "update_settings",
  "upsert_workspace",
  "list_workspaces",
  "list_capabilities",
  "web_search",
  "list_skills",
  "delegate_run",
  "subscribe_events",
  "list_facts",
  "forget_fact",
  "list_participants",
  "search_artifacts",
  "list_workflows",
  "upsert_workflow_graph",
  "list_workflow_graphs",
  "run_graph",
  "create_motif",
  "assign_motif",
  "resolve_motif",
  "list_motifs",
  "record_evaluation",
  "list_evaluations",
  "list_workspace_files",
  "list_directory",
  "pick_folder",
  "pick_files",
  "attach_files",
  "reveal_path",
  "assign_session_workspace",
  "delete_workspace",
  "read_workspace_file",
  "write_workspace_file",
  "read_events",
  "shutdown",
] as const;

export type LocalTransportMethod = (typeof LOCAL_TRANSPORT_METHODS)[number];

/** Alias used by the Desktop-3 transport/client contract. */
export type LocalMethod = LocalTransportMethod;

export type LocalRpcParams = Record<string, unknown>;

export interface LocalRpcRequest {
  id: string;
  method: LocalTransportMethod;
  params: LocalRpcParams;
}

export interface LocalRpcSuccess {
  id: string;
  ok: true;
  result: unknown;
}

export interface LocalRpcError {
  code: string;
  message: string;
  retryable: boolean;
  run_id?: string;
  request_id?: string;
  details?: Record<string, unknown>;
}

export interface LocalRpcFailure {
  id: string;
  ok: false;
  error: LocalRpcError;
}

export type LocalRpcResponse = LocalRpcSuccess | LocalRpcFailure;

export class LocalTransportProtocolError extends Error {
  readonly code = "INVALID_RESPONSE";

  constructor(message: string) {
    super(message);
    this.name = "LocalTransportProtocolError";
  }
}

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

/** Project a failure envelope onto the client error model — shared by the
 * stdio and HTTP transports so both surfaces report identical codes. */
export function rpcErrorToClientError(error: LocalRpcError): AgentServiceClientError {
  const code = serviceCode(error.code);
  return new AgentServiceClientError({
    code,
    message: stableMessage(code, error.message),
    retryable: error.retryable,
    ...(error.run_id ? { runId: error.run_id } : {}),
    ...(error.request_id ? { requestId: error.request_id } : {}),
    ...(error.details
      ? {
          details: Object.fromEntries(
            Object.entries(error.details).filter(([, value]) => typeof value === "string"),
          ) as Record<string, string>,
        }
      : {}),
  });
}

export function clientError(
  code: ServiceErrorCode,
  message: string,
  retryable: boolean,
): AgentServiceClientError {
  return new AgentServiceClientError({ code, message, retryable });
}

export function transportError(
  message: string,
  code: ServiceErrorCode = "PROVIDER_UNAVAILABLE",
): AgentServiceClientError {
  return clientError(code, SAFE_MESSAGES[code] ?? message, code === "PROVIDER_UNAVAILABLE");
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function requiredString(value: unknown, label: string): string {
  if (typeof value !== "string" || value.trim().length === 0) {
    throw new LocalTransportProtocolError(`${label} must be a non-empty string`);
  }
  return value;
}

function exactKeys(value: Record<string, unknown>, keys: readonly string[]): boolean {
  const expected = new Set(keys);
  const actual = Object.keys(value);
  return actual.length === expected.size && actual.every((key) => expected.has(key));
}

/** Encode one request envelope, including the JSONL newline. */
export function encodeLocalRpcRequest(request: LocalRpcRequest): string {
  return `${JSON.stringify(request)}\n`;
}

/** Decode and validate one response envelope from sidecar stdout. */
export function decodeLocalRpcResponse(line: string): LocalRpcResponse {
  let value: unknown;
  try {
    value = JSON.parse(line.trim());
  } catch {
    throw new LocalTransportProtocolError("sidecar stdout contains malformed JSON");
  }

  if (!isRecord(value)) {
    throw new LocalTransportProtocolError("sidecar response must be a JSON object");
  }
  requiredString(value.id, "response.id");

  if (value.ok === true && exactKeys(value, ["id", "ok", "result"])) {
    return {
      id: value.id as string,
      ok: true,
      result: value.result,
    };
  }

  if (value.ok === false && exactKeys(value, ["id", "ok", "error"])) {
    if (!isRecord(value.error)) {
      throw new LocalTransportProtocolError("sidecar error envelope is invalid");
    }
    requiredString(value.error.code, "error.code");
    if (typeof value.error.message !== "string" || typeof value.error.retryable !== "boolean") {
      throw new LocalTransportProtocolError("sidecar error envelope is invalid");
    }
    return {
      id: value.id as string,
      ok: false,
      error: {
        code: value.error.code as string,
        message: value.error.message,
        retryable: value.error.retryable,
        ...(typeof value.error.run_id === "string" ? { run_id: value.error.run_id } : {}),
        ...(typeof value.error.request_id === "string" ? { request_id: value.error.request_id } : {}),
        ...(isRecord(value.error.details) ? { details: value.error.details } : {}),
      },
    };
  }

  throw new LocalTransportProtocolError("sidecar response envelope is invalid");
}

export interface LocalTransport {
  request<TRequest extends LocalRpcParams, TResponse>(
    method: LocalTransportMethod,
    params: TRequest,
  ): Promise<TResponse>;
  close(): Promise<void>;
}
