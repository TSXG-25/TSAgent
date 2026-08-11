/**
 * Transport seam for the future Tauri sidecar adapter.
 *
 * The MVP does not open a port or spawn a process. A JSON-lines stdio,
 * Unix-domain-socket, or local HTTP implementation can satisfy this seam
 * later without leaking transport details into AgentServiceClient callers.
 */
export interface LocalTransport {
  call<TResponse>(method: string, payload: unknown): Promise<TResponse>;
  close(): Promise<void>;
}
