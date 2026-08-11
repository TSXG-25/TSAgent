/**
 * Desktop local JSON-lines transport contract.
 *
 * This file contains protocol facts only. Process lifecycle and request
 * correlation live in tauriSidecarTransport.ts; AgentService DTO mapping lives
 * in localAgentServiceClient.ts.
 */
export const LOCAL_TRANSPORT_METHODS = [
  "health",
  "start_run",
  "get_run",
  "resume_run",
  "cancel_run",
  "list_artifacts",
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
