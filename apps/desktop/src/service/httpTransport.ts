/**
 * Loopback-HTTP LocalTransport: for browser dev mode where no host
 * process exists to spawn the stdio sidecar.  Speaks to
 * `python -m agent.service.local_sidecar --http <port>` via POST /rpc
 * using the same request/response envelopes as localTransport.
 */
import {
  decodeLocalRpcResponse,
  LocalTransportProtocolError,
  rpcErrorToClientError,
  type LocalRpcParams,
  type LocalRpcRequest,
  type LocalTransport,
  type LocalTransportMethod,
} from "./localTransport";

export class HttpLocalTransport implements LocalTransport {
  private closed = false;
  private counter = 0;

  constructor(private readonly baseUrl: string, private readonly timeoutMs = 30000) {}

  async request<TRequest extends LocalRpcParams, TResponse>(
    method: LocalTransportMethod,
    params: TRequest,
  ): Promise<TResponse> {
    if (this.closed) {
      throw new LocalTransportProtocolError("http transport is closed");
    }
    const request: LocalRpcRequest = {
      id: `http-${++this.counter}-${Date.now().toString(36)}`,
      method,
      params,
    };
    const response = await fetch(`${this.baseUrl}/rpc`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
      signal: AbortSignal.timeout(this.timeoutMs),
    });
    if (!response.ok) {
      throw new LocalTransportProtocolError(
        `sidecar http ${response.status}: ${await response.text().catch(() => "")}`,
      );
    }
    const envelope = decodeLocalRpcResponse(await response.text());
    if (envelope.id !== request.id) {
      throw new LocalTransportProtocolError(
        `sidecar response id ${envelope.id} does not match request ${request.id}`,
      );
    }
    if (envelope.ok === true) {
      return envelope.result as TResponse;
    }
    throw rpcErrorToClientError(envelope.error);
  }

  /**
   * SSE subscription over GET /events — pushes durable run events until the
   * run goes terminal or the abort signal fires.  Browser-native transport
   * for the polling gap; callers map frames to typed events.
   */
  async subscribeEvents(
    params: {
      tenantId: string;
      sessionId: string;
      userId: string;
      runId: string;
      afterSequence?: number;
    },
    onEvent: (frame: Record<string, unknown>) => void,
    signal?: AbortSignal,
  ): Promise<void> {
    const query = new URLSearchParams({
      tenant_id: params.tenantId,
      session_id: params.sessionId,
      user_id: params.userId,
      run_id: params.runId,
      after: String(params.afterSequence ?? 0),
    });
    const response = await fetch(`${this.baseUrl}/events?${query}`, {
      headers: { Accept: "text/event-stream" },
      signal,
    });
    if (!response.ok || !response.body) {
      throw new LocalTransportProtocolError(
        `sidecar events stream failed: http ${response.status}`,
      );
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { done, value } = await reader.read();
      if (done) return;
      buffer += decoder.decode(value, { stream: true });
      let index: number;
      while ((index = buffer.indexOf("\n\n")) >= 0) {
        const frame = buffer.slice(0, index);
        buffer = buffer.slice(index + 2);
        for (const line of frame.split("\n")) {
          if (line.startsWith("data: ")) {
            const data = line.slice(6);
            if (data !== "{}") {
              onEvent(JSON.parse(data) as Record<string, unknown>);
            }
          }
        }
      }
    }
  }

  async close(): Promise<void> {
    this.closed = true;
  }
}
