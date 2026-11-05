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
            LocalTransportMethod.LIST_RUNS: self._list_runs,
            LocalTransportMethod.CREATE_TASK: self._create_task,
            LocalTransportMethod.LIST_TASKS: self._list_tasks,
            LocalTransportMethod.GET_TASK: self._get_task,
            LocalTransportMethod.LIST_APPROVALS: self._list_approvals,
            LocalTransportMethod.DECIDE_APPROVAL: self._decide_approval,
            LocalTransportMethod.CREATE_SESSION: self._create_session,
            LocalTransportMethod.LIST_SESSIONS: self._list_sessions,
            LocalTransportMethod.LIST_HANDOFFS: self._list_handoffs,
            LocalTransportMethod.LIST_HISTORY: self._list_history,
            LocalTransportMethod.CREATE_SCHEDULE: self._create_schedule,
            LocalTransportMethod.LIST_SCHEDULES: self._list_schedules,
            LocalTransportMethod.UPDATE_SCHEDULE: self._update_schedule,
            LocalTransportMethod.UPSERT_PROFILE: self._upsert_profile,
            LocalTransportMethod.LIST_PROFILES: self._list_profiles,
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

    async def _list_runs(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import ListRunsRequest

        request = self._from_dto(ListRunsRequest.from_dict, params)
        return await self._service.list_runs(request)

    async def _create_task(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import CreateTaskRequest

        request = self._from_dto(CreateTaskRequest.from_dict, params)
        return await self._service.create_task(request)

    async def _list_tasks(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import ListTasksRequest

        request = self._from_dto(ListTasksRequest.from_dict, params)
        return await self._service.list_tasks(request)

    async def _get_task(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import TaskLookupRequest

        request = self._from_dto(TaskLookupRequest.from_dict, params)
        return await self._service.get_task(request)

    async def _list_approvals(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import ListApprovalsRequest

        request = self._from_dto(ListApprovalsRequest.from_dict, params)
        return await self._service.list_approvals(request)

    async def _decide_approval(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import DecideApprovalRequest

        request = self._from_dto(DecideApprovalRequest.from_dict, params)
        return await self._service.decide_approval(request)

    async def _create_session(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import CreateSessionRequest

        request = self._from_dto(CreateSessionRequest.from_dict, params)
        return await self._service.create_session(request)

    async def _list_sessions(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import ListSessionsRequest

        request = self._from_dto(ListSessionsRequest.from_dict, params)
        return await self._service.list_sessions(request)

    async def _list_handoffs(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import ListHandoffsRequest

        request = self._from_dto(ListHandoffsRequest.from_dict, params)
        return await self._service.list_handoffs(request)

    async def _list_history(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import ListHistoryRequest

        request = self._from_dto(ListHistoryRequest.from_dict, params)
        return await self._service.list_history(request)

    async def _create_schedule(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import CreateScheduleRequest

        request = self._from_dto(CreateScheduleRequest.from_dict, params)
        return await self._service.create_schedule(request)

    async def _list_schedules(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import ListSchedulesRequest

        request = self._from_dto(ListSchedulesRequest.from_dict, params)
        return await self._service.list_schedules(request)

    async def _update_schedule(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import UpdateScheduleRequest

        request = self._from_dto(UpdateScheduleRequest.from_dict, params)
        return await self._service.update_schedule(request)

    async def _upsert_profile(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import UpsertProfileRequest

        request = self._from_dto(UpsertProfileRequest.from_dict, params)
        return await self._service.upsert_profile(request)

    async def _list_profiles(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import ListProfilesRequest

        request = self._from_dto(ListProfilesRequest.from_dict, params)
        return await self._service.list_profiles(request)

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
