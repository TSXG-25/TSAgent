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
from ..wire_schema import WIRE_METHODS, WireMethod


Handler = Callable[[Mapping[str, Any]], Awaitable[Any]]


class SidecarDispatcher:
    """Thin allowlisted adapter; it owns no Runtime or business decisions."""

    def __init__(self, service: Any, *, diagnostics: TextIO) -> None:
        self._service = service
        self._diagnostics = diagnostics
        self._shutdown_requested = False
        self._closed = False
        # Plain methods forward straight to the AgentService method the
        # wire schema names; the rest have a dedicated handler below.
        self._handlers: dict[LocalTransportMethod, Handler] = {
            method: (
                self._forward(spec)
                if spec.service
                else getattr(self, f"_{method.value}")
            )
            for method, spec in WIRE_METHODS.items()
        }

    def _forward(self, spec: WireMethod) -> Handler:
        async def handler(params: Mapping[str, Any]) -> Any:
            request = self._from_dto(spec.params.from_dict, params)
            return await getattr(self._service, spec.service)(request)

        return handler

    @property
    def shutdown_requested(self) -> bool:
        return self._shutdown_requested

    async def dispatch(self, request: LocalRpcRequest) -> LocalRpcResponse:
        if self._closed:
            from .serializer import internal_error_response

            return internal_error_response(request.id)
        handler = self._handlers[request.method]
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

    async def _cancel_run(self, params: Mapping[str, Any]) -> Any:
        from agent.interruption import CancelRunRequest

        # USER_CANCEL is the only public cancellation reason.  The desktop
        # wire request omits it intentionally; the adapter supplies the
        # frozen public default before DTO construction.
        cancel_params = dict(params)
        cancel_params.setdefault("reason", "USER_CANCEL")
        request = self._from_dto(CancelRunRequest.from_dict, cancel_params)
        return await self._service.cancel_run(request)

    async def _list_capabilities(self, params: Mapping[str, Any]) -> Any:
        """Expose the runtime capability registry to the desktop UI — scene
        'not installed' badges reflect the real registry, not UI guesses."""
        from agent.registry.capability_registry import registry

        return [
            {"capability": name, "tools": registry.get_tools_for_capability(name)}
            for name in registry.get_all_capabilities()
        ]

    async def _web_search(self, params: Mapping[str, Any]) -> Any:
        """Mercury search — real provider chain (duckduckgo -> deepseek);
        surfaces provider errors instead of fabricating results."""
        from agent.search.providers import run_search

        query = str(params.get("query", "")).strip()
        if not query:
            raise ValueError("query must be a non-empty string")
        outcome = await run_search(query, max_results=int(params.get("max_results", 8)))
        return {
            "provider": outcome.provider,
            "content": outcome.content,
            "sources": outcome.sources,
        }

    async def _list_skills(self, params: Mapping[str, Any]) -> Any:
        """Real skill index (SKILL.md frontmatter) — Uranus binds profiles to
        actual skill ids, not made-up names."""
        from agent.skills import skills_index

        return [
            {"skill_id": sid, "name": s.name, "description": s.description}
            for sid, s in skills_index().items()
        ]

    async def _pick_folder(self, params: Mapping[str, Any]) -> Any:
        return await self._service.pick_folder()

    async def _pick_files(self, params: Mapping[str, Any]) -> Any:
        return await self._service.pick_files()

    async def _reveal_path(self, params: Mapping[str, Any]) -> Any:
        return await self._service.reveal_path(str(params.get("path", "")))

    async def _list_directory(self, params: Mapping[str, Any]) -> Any:
        return await self._service.list_directory(str(params.get("path", "")))

    async def _list_workflows(self, params: Mapping[str, Any]) -> Any:
        """Real workflow definitions — Uranus canvas binds nodes to actual
        registered workflows, not made-up names."""
        import workflows  # importing the package registers each Workflow
        from agent.registry.workflow_registry import workflow_registry

        return [
            {
                "workflow_id": name,
                "description": getattr(wf, "description", "") or "",
                "version": getattr(wf, "version", "") or "",
            }
            for name in workflow_registry.list()
            for wf in [workflow_registry.get(name)]
        ]

    async def _read_events(self, params: Mapping[str, Any]) -> list[Any]:
        # A poll must return promptly when a live Run has no new event.  The
        # durable event repository remains the source of truth; this timeout
        # is only transport framing.
        return await self._stream_events_bounded(
            params, first_timeout=0.05, gap_timeout=0.01
        )

    async def _subscribe_events(self, params: Mapping[str, Any]) -> list[Any]:
        """Long-poll event push — holds the request up to ~8s until new
        durable events arrive, then returns them.  Same request shape and
        semantics as read_events; the client chains calls for live runs."""
        return await self._stream_events_bounded(
            params, first_timeout=8.0, gap_timeout=0.25
        )

    async def _stream_events_bounded(
        self,
        params: Mapping[str, Any],
        *,
        first_timeout: float,
        gap_timeout: float,
    ) -> list[Any]:
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
                    event = await asyncio.wait_for(
                        anext(iterator),
                        timeout=first_timeout if not events else gap_timeout,
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
