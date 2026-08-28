"""stdin/stdout JSONL framing for the Desktop AgentService sidecar."""

from __future__ import annotations

from typing import TextIO

from ..local_protocol import (
    LocalProtocolError,
    LocalRpcFailure,
    LocalRpcRequest,
    LocalRpcResponse,
    decode_json_line,
    encode_response,
)
from .dispatcher import SidecarDispatcher


def _request_id_hint(line: str | bytes) -> str:
    try:
        value = decode_json_line(line).get("id")
    except LocalProtocolError:
        return "unknown"
    return value if isinstance(value, str) and value.strip() else "unknown"


def _write_response(stdout: TextIO, response: LocalRpcResponse) -> None:
    stdout.write(encode_response(response))
    stdout.flush()


async def serve(
    dispatcher: SidecarDispatcher,
    *,
    stdin: TextIO,
    stdout: TextIO,
    diagnostics: TextIO,
) -> int:
    """Serve one request and one response per JSONL line."""

    import asyncio

    try:
        while True:
            line = await asyncio.to_thread(stdin.readline)
            if not line:
                break
            try:
                request = LocalRpcRequest.from_json_line(line)
                response = await dispatcher.dispatch(request)
            except LocalProtocolError as error:
                from .serializer import protocol_error_response

                response = protocol_error_response(_request_id_hint(line), error)
            except Exception:
                import traceback
                from .serializer import internal_error_response

                traceback.print_exc(file=diagnostics)
                response = internal_error_response(_request_id_hint(line))

            try:
                _write_response(stdout, response)
            except (BrokenPipeError, OSError):
                print("local transport disconnected", file=diagnostics)
                break
            if dispatcher.shutdown_requested:
                break
    finally:
        # close() waits for normal Service tasks but never creates a
        # USER_CANCEL intent.  EOF is a transport event, not a business action.
        await dispatcher.close()
    return 0


__all__ = ["serve"]
