/**
 * Desktop-1 local JSON-lines transport contract.
 *
 * This file defines wire envelopes only. It does not spawn a process, open a
 * port, or make the UI aware of Runtime/SQLite details. The future sidecar
 * adapter will map these envelopes to AgentService DTOs.
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

export interface LocalTransport {
  call<TResponse>(
    method: LocalTransportMethod,
    params: LocalRpcParams,
  ): Promise<TResponse>;
  close(): Promise<void>;
}
