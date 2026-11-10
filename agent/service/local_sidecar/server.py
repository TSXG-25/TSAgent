"""stdin/stdout JSONL framing for the Desktop AgentService sidecar."""

from __future__ import annotations

from typing import TextIO

from ..local_protocol import (
    LocalProtocolError,
    LocalRpcFailure,
    LocalRpcRequest,
    LocalRpcResponse,
    LocalTransportMethod,
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
    """Serve one request and one response per JSONL line.

    ``subscribe_events`` is a long poll — it dispatches on a background
    task so the serial loop keeps answering approvals, cancels, and
    shutdown while it is pending.  All response writes serialize on one
    lock so protocol lines never interleave.
    """

    import asyncio

    # The Service may have been constructed outside an event loop — start
    # the scheduler here so an idle sidecar still fires due schedules.
    dispatcher._service._maybe_start_scheduler()

    write_lock = asyncio.Lock()
    subscriptions: set[asyncio.Task] = set()

    async def _emit(response: LocalRpcResponse) -> None:
        try:
            async with write_lock:
                _write_response(stdout, response)
        except (BrokenPipeError, OSError):
            print("local transport disconnected", file=diagnostics)

    async def _run_subscription(request: LocalRpcRequest) -> None:
        response = await dispatcher.dispatch(request)
        await _emit(response)

    try:
        while True:
            line = await asyncio.to_thread(stdin.readline)
            if not line:
                break
            try:
                request = LocalRpcRequest.from_json_line(line)
                if request.method is LocalTransportMethod.SUBSCRIBE_EVENTS:
                    task = asyncio.create_task(_run_subscription(request))
                    subscriptions.add(task)
                    task.add_done_callback(subscriptions.discard)
                    continue
                response = await dispatcher.dispatch(request)
            except LocalProtocolError as error:
                from .serializer import protocol_error_response

                response = protocol_error_response(_request_id_hint(line), error)
            except Exception:
                import traceback
                from .serializer import internal_error_response

                traceback.print_exc(file=diagnostics)
                response = internal_error_response(_request_id_hint(line))

            await _emit(response)
            if dispatcher.shutdown_requested:
                break
    finally:
        # EOF/shutdown cancels still-pending subscriptions rather than
        # waiting out their poll windows.
        for task in tuple(subscriptions):
            task.cancel()
        if subscriptions:
            await asyncio.gather(*subscriptions, return_exceptions=True)
        # close() waits for normal Service tasks but never creates a
        # USER_CANCEL intent.  EOF is a transport event, not a business action.
        await dispatcher.close()
    return 0


__all__ = ["serve"]
