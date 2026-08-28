"""Explicit JSONL method dispatch over the public AgentService DTO boundary."""

from __future__ import annotations

import traceback
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, TextIO

from ..local_protocol import (
    LocalProtocolError,
    LocalRpcRequest,
    LocalRpcResponse,
    LocalRpcSuccess,
    LocalTransportMethod,
    PROTOCOL_VERSION,
)


Handler = Callable[[Mapping[str, Any]], Awaitable[Any]]


class SidecarDispatcher:
    """Thin allowlisted adapter; it owns no Runtime or business decisions."""

    def __init__(self, service: Any, *, diagnostics: TextIO) -> None:
        self._service = service
        self._diagnostics = diagnostics
        self._shutdown_requested = False
        self._closed = False
        self._handlers: dict[LocalTransportMethod, Handler] = {
            LocalTransportMethod.HEALTH: self._health,
            LocalTransportMethod.START_RUN: self._start_run,
            LocalTransportMethod.GET_RUN: self._get_run,
            LocalTransportMethod.RESUME_RUN: self._resume_run,
            LocalTransportMethod.CANCEL_RUN: self._cancel_run,
            LocalTransportMethod.LIST_ARTIFACTS: self._list_artifacts,
            LocalTransportMethod.READ_EVENTS: self._read_events,
            LocalTransportMethod.SHUTDOWN: self._shutdown,
        }

    @property
    def shutdown_requested(self) -> bool:
        return self._shutdown_requested

    async def dispatch(self, request: LocalRpcRequest) -> LocalRpcResponse:
        if self._closed:
            from .serializer import internal_error_response

            return internal_error_response(request.id)
        handler = self._handlers.get(request.method)
        if handler is None:  # pragma: no cover - LocalRpcRequest validates the allowlist
            from .serializer import protocol_error_response

            return protocol_error_response(
                request.id,
                LocalProtocolError(
                    "UNSUPPORTED_OPERATION",
                    f"unsupported local method: {request.method.value}",
                ),
            )
        try:
            result = await handler(request.params)
            if request.method is LocalTransportMethod.SHUTDOWN:
                self._shutdown_requested = True
            return LocalRpcSuccess(request.id, self._to_wire(result))
        except LocalProtocolError as error:
            from .serializer import protocol_error_response

            return protocol_error_response(request.id, error)
        except Exception as error:
            from ..errors import AgentServiceError
            from .serializer import internal_error_response

            if isinstance(error, AgentServiceError):
                from .serializer import service_error_response

                return service_error_response(request, error)
            traceback.print_exc(file=self._diagnostics)
            return internal_error_response(request.id)

    @staticmethod
    def _to_wire(value: Any) -> Any:
        if hasattr(value, "to_dict") and callable(value.to_dict):
            return SidecarDispatcher._to_wire(value.to_dict())
        if isinstance(value, Mapping):
            return {
                str(key): SidecarDispatcher._to_wire(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [SidecarDispatcher._to_wire(item) for item in value]
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        raise LocalProtocolError(
            "INTERNAL_MODEL_LEAK",
            "service result contains unsupported object",
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._service.close()

    async def _health(self, params: Mapping[str, Any]) -> dict[str, Any]:
        del params
        return {
            "status": "ok",
            "protocol_version": PROTOCOL_VERSION,
            "service": "ready",
        }

    async def _start_run(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import StartRunRequest

        request = self._from_dto(StartRunRequest.from_dict, params)
        return await self._service.start_run(request)

    async def _get_run(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import RunLookupRequest

        request = self._from_dto(RunLookupRequest.from_dict, params)
        return await self._service.get_run(request)

    async def _resume_run(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import ResumeRunRequest

        request = self._from_dto(ResumeRunRequest.from_dict, params)
        return await self._service.resume_run(request)

    async def _cancel_run(self, params: Mapping[str, Any]) -> Any:
        from agent.interruption import CancelRunRequest

        # USER_CANCEL is the only public cancellation reason.  The desktop
        # wire request omits it intentionally; the adapter supplies the
        # frozen public default before DTO construction.
        cancel_params = dict(params)
        cancel_params.setdefault("reason", "USER_CANCEL")
        request = self._from_dto(CancelRunRequest.from_dict, cancel_params)
        return await self._service.cancel_run(request)

    async def _list_artifacts(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import RunLookupRequest

        request = self._from_dto(RunLookupRequest.from_dict, params)
        return await self._service.list_artifacts(request)

    async def _read_events(self, params: Mapping[str, Any]) -> list[Any]:
        import asyncio
        from ..contracts import EventStreamRequest

        request = self._from_dto(EventStreamRequest.from_dict, params)
        stream = self._service.stream_events(request)
        iterator = stream.__aiter__()
        events: list[Any] = []
        limit = request.limit or 100
        try:
            while len(events) < limit:
                try:
                    # A poll must return promptly when a live Run has no new
                    # event.  The durable event repository remains the source
                    # of truth; this timeout is only transport framing.
                    event = await asyncio.wait_for(
                        anext(iterator),
                        timeout=0.05 if not events else 0.01,
                    )
                except asyncio.TimeoutError:
                    break
                except StopAsyncIteration:
                    break
                events.append(event)
                if event.is_terminal:
                    break
        finally:
            close_stream = getattr(iterator, "aclose", None)
            if close_stream is not None:
                await close_stream()
        return events

    async def _shutdown(self, params: Mapping[str, Any]) -> dict[str, str]:
        del params
        return {"status": "shutting_down"}

    @staticmethod
    def _from_dto(factory: Callable[[Mapping[str, Any]], Any], params: Mapping[str, Any]) -> Any:
        try:
            return factory(params)
        except (TypeError, ValueError, KeyError) as error:
            raise LocalProtocolError(
                "INVALID_REQUEST",
                "request params do not match the AgentService contract",
            ) from error


__all__ = ["SidecarDispatcher"]
