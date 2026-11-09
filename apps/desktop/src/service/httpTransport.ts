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

  async close(): Promise<void> {
    this.closed = true;
  }
}
