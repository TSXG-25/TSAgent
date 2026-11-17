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

    Every request dispatches on its own task — responses correlate by
    ``id``, so a slow call (a Finder dialog, a web search) or a pending
    ``subscribe_events`` long poll never stalls approvals, cancels or
    reads.  ``shutdown`` first lets in-flight calls answer.  All
    response writes serialize on one lock so protocol lines never
    interleave; the first failed write stops reading new requests.
    """

    import asyncio
    import traceback

    from .serializer import internal_error_response, protocol_error_response

    # The Service may have been constructed outside an event loop — start
    # the scheduler here so an idle sidecar still fires due schedules.
    dispatcher._service._maybe_start_scheduler()

    write_lock = asyncio.Lock()
    transport_dead = asyncio.Event()
    calls: set[asyncio.Task] = set()
    subscriptions: set[asyncio.Task] = set()

    async def _emit(response: LocalRpcResponse) -> None:
        if transport_dead.is_set():
            return
        try:
            async with write_lock:
                _write_response(stdout, response)
        except (BrokenPipeError, OSError):
            print("local transport disconnected", file=diagnostics)
            transport_dead.set()

    async def _handle(request: LocalRpcRequest, line: str) -> None:
        try:
            response = await dispatcher.dispatch(request)
        except Exception:
            traceback.print_exc(file=diagnostics)
            response = internal_error_response(_request_id_hint(line))
        await _emit(response)

    try:
        while not transport_dead.is_set():
            line = await asyncio.to_thread(stdin.readline)
            if not line or transport_dead.is_set():
                break
            try:
                request = LocalRpcRequest.from_json_line(line)
            except LocalProtocolError as error:
                await _emit(protocol_error_response(_request_id_hint(line), error))
                continue
            if request.method is LocalTransportMethod.SHUTDOWN:
                await asyncio.gather(*calls)
                await _handle(request, line)
                if dispatcher.shutdown_requested:
                    break
                continue
            task = asyncio.create_task(_handle(request, line))
            pool = subscriptions if request.method is LocalTransportMethod.SUBSCRIBE_EVENTS else calls
            pool.add(task)
            task.add_done_callback(pool.discard)
    finally:
        # EOF/shutdown cancels still-pending subscriptions rather than
        # waiting out their poll windows; ordinary calls run to completion.
        for task in tuple(subscriptions):
            task.cancel()
        await asyncio.gather(*subscriptions, *calls, return_exceptions=True)
        # close() waits for normal Service tasks but never creates a
        # USER_CANCEL intent.  EOF is a transport event, not a business action.
        await dispatcher.close()
    return 0


__all__ = ["serve"]
