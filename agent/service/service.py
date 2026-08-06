"""Pure Python AgentService Core for v2.3C-2."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from agent.runtime_store import DurableStoreError, SqliteRuntimeStore, StoreErrorCode

from .context_factory import ServiceContextFactory
from .contracts import (
    EventStreamRequest,
    ResumeRunRequest,
    RunHandle,
    RunLookupRequest,
    RunSnapshot,
    StartRunRequest,
)
from .errors import AgentServiceError, ServiceErrorCode
from .event_repository import EventRepository, SqliteEventRepository
from .execution_launcher import ExecutionLauncher
from .run_projector import (
    RunProjector,
    encode_request_reference,
    handle_from_head,
)


@dataclass
class _ManagedRun:
    run_context: Any
    task: asyncio.Task[None]


class AgentService:
    """Stable service facade over the existing durable Runtime path."""

    def __init__(
        self,
        *,
        runtime_store: SqliteRuntimeStore,
        launcher: ExecutionLauncher,
        context_factory: ServiceContextFactory | None = None,
        event_repository: EventRepository | None = None,
    ) -> None:
        self._store = runtime_store
        self._launcher = launcher
        self._contexts = context_factory or ServiceContextFactory(runtime_store)
        # Durable SQLite events are the default Source of Truth in C-3.  A
        # caller may still inject an explicit repository for deterministic
        # tests or a future adapter, but the Service no longer silently falls
        # back to an in-memory/empty stream.
        self._events = event_repository or SqliteEventRepository(runtime_store)
        self._projector = RunProjector()
        self._runs: dict[str, _ManagedRun] = {}
        self._task_errors: dict[str, AgentServiceError] = {}
        self._operation_lock = asyncio.Lock()
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def _ensure_open(self) -> None:
        if self._closed:
            raise AgentServiceError(
                ServiceErrorCode.SERVICE_CLOSED,
                "AgentService is closed",
            )

    @staticmethod
    def _store_error(
        error: DurableStoreError,
        *,
        run_id: str | None = None,
        request_id: str = "",
        lookup: bool = False,
    ) -> AgentServiceError:
        code_map = {
            StoreErrorCode.RUN_NOT_FOUND: ServiceErrorCode.RUN_NOT_FOUND,
            StoreErrorCode.IDENTITY_MISMATCH: (
                ServiceErrorCode.RUN_NOT_FOUND if lookup else ServiceErrorCode.IDENTITY_MISMATCH
            ),
            StoreErrorCode.IDEMPOTENCY_CONFLICT: ServiceErrorCode.REQUEST_ID_CONFLICT,
            StoreErrorCode.FENCE_CONFLICT: ServiceErrorCode.RUN_ALREADY_ACTIVE,
            StoreErrorCode.STORE_BUSY: getattr(
                ServiceErrorCode, "STORE_BUSY", ServiceErrorCode.INVALID_REQUEST
            ),
            StoreErrorCode.STORE_CLOSED: ServiceErrorCode.SERVICE_CLOSED,
        }
        code = code_map.get(error.code, getattr(
            ServiceErrorCode, "INTERNAL_ERROR", ServiceErrorCode.INVALID_REQUEST
        ))
        return AgentServiceError(
            code,
            "durable Runtime operation failed",
            details={"store_code": error.code.value},
        )

    @staticmethod
    def _runtime_error(error: BaseException, *, run_id: str) -> AgentServiceError:
        code = getattr(ServiceErrorCode, "INTERNAL_ERROR", ServiceErrorCode.INVALID_REQUEST)
        return AgentServiceError(
            code,
            "Runtime execution failed",
            details={"run_id": run_id},
        )

    def _handle(self, head: Any, request_id: str) -> RunHandle:
        return handle_from_head(head, request_id=request_id)

    async def start_run(self, request: StartRunRequest) -> RunHandle:
        self._ensure_open()
        async with self._operation_lock:
            try:
                reservation = self._store.reserve_service_start(
                    request.tenant_id,
                    request.session_id,
                    requested_run_id=getattr(request, "run_id", None),
                    request_id=request.request_id,
                    request_digest=request.request_digest,
                    writer_id=self._contexts.writer_id,
                    external_reference=encode_request_reference(
                        request,
                        run_id=getattr(request, "run_id", None) or "",
                    ),
                )
            except DurableStoreError as error:
                raise self._store_error(error, request_id=request.request_id) from error

            run_id = reservation.head.run_id
            if not reservation.created:
                managed = self._runs.get(run_id)
                if managed is not None and not managed.task.done():
                    return self._handle(reservation.head, request.request_id)
                return self._handle(reservation.head, request.request_id)

            try:
                # Persisted reservation precedes Context construction and any
                # launcher/provider call.
                context = self._contexts.create_run(request, run_id=run_id)
                task = asyncio.create_task(
                    self._run_start(request, context),
                    name=f"tsagent-start:{run_id}",
                )
                self._runs[run_id] = _ManagedRun(context, task)
            except DurableStoreError as error:
                raise self._store_error(
                    error,
                    run_id=run_id,
                    request_id=request.request_id,
                ) from error
            except Exception as error:
                raise self._runtime_error(error, run_id=run_id) from error
            return self._handle(reservation.head, request.request_id)

    async def _run_start(self, request: StartRunRequest, context: Any) -> None:
        run_id = context.run_id
        try:
            await self._launcher.start(
                session_context=context.session,
                run_context=context,
                request=request,
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._task_errors[run_id] = self._runtime_error(error, run_id=run_id)
        finally:
            managed = self._runs.get(run_id)
            if managed is not None and managed.run_context is context:
                self._runs.pop(run_id, None)
            self._contexts.release_run(context)

    async def get_run(self, request: RunLookupRequest) -> RunSnapshot:
        self._ensure_open()
        try:
            read = self._store.read_run_snapshot(
                request.tenant_id,
                request.run_id,
                session_id=request.session_id,
            )
            return self._projector.project(read, request)
        except AgentServiceError:
            raise
        except DurableStoreError as error:
            raise self._store_error(
                error,
                run_id=request.run_id,
                request_id=request.request_id,
                lookup=True,
            ) from error
        except Exception as error:
            raise self._runtime_error(error, run_id=request.run_id) from error

    async def resume_run(self, request: ResumeRunRequest) -> RunHandle:
        self._ensure_open()
        async with self._operation_lock:
            try:
                head = self._store.get_run_head(
                    request.tenant_id,
                    request.run_id,
                    session_id=request.session_id,
                )
                if head is None:
                    raise DurableStoreError(
                        StoreErrorCode.RUN_NOT_FOUND,
                        "run not found",
                    )
                if str(head.run_status).upper() in {"COMPLETED", "CANCELLED"}:
                    code = getattr(ServiceErrorCode, "ALREADY_COMPLETED", ServiceErrorCode.INVALID_REQUEST)
                    raise AgentServiceError(code, "Run cannot be resumed in its terminal state")
                if request.run_id in self._runs and not self._runs[request.run_id].task.done():
                    raise AgentServiceError(
                        getattr(
                            ServiceErrorCode,
                            "RUN_ALREADY_ACTIVE",
                            ServiceErrorCode.DUPLICATE_REQUEST,
                        ),
                        "Run already has an active local execution",
                    )

                context = self._contexts.create_run(request, run_id=request.run_id)
                if context.durable_store_view is None:
                    raise AgentServiceError(
                        ServiceErrorCode.INVALID_REQUEST,
                        "AgentService requires a durable Run context",
                    )
                prepared = context.durable_store_view.prepare_operation(
                    idempotency_key=request.request_id,
                    operation_type="service.resume_run",
                    request_digest=request.request_digest,
                    external_reference=f"service-resume:{request.run_id}",
                )
                if prepared.effect_state != "PREPARED" or prepared.prepared_revision != context.durable_store_view.head().current_revision:
                    self._contexts.release_run(context)
                    return self._handle(head, request.request_id)
                task = asyncio.create_task(
                    self._run_resume(request, context),
                    name=f"tsagent-resume:{request.run_id}",
                )
                self._runs[request.run_id] = _ManagedRun(context, task)
                return self._handle(head, request.request_id)
            except AgentServiceError:
                raise
            except DurableStoreError as error:
                raise self._store_error(
                    error,
                    run_id=request.run_id,
                    request_id=request.request_id,
                ) from error
            except Exception as error:
                raise self._runtime_error(error, run_id=request.run_id) from error

    async def _run_resume(self, request: ResumeRunRequest, context: Any) -> None:
        run_id = context.run_id
        try:
            await self._launcher.resume(run_context=context, request=request)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._task_errors[run_id] = self._runtime_error(error, run_id=run_id)
        finally:
            managed = self._runs.get(run_id)
            if managed is not None and managed.run_context is context:
                self._runs.pop(run_id, None)
            self._contexts.release_run(context)

    async def list_artifacts(
        self,
        request: RunLookupRequest,
    ) -> tuple[Any, ...]:
        snapshot = await self.get_run(request)
        return snapshot.artifacts

    def stream_events(self, request: EventStreamRequest):
        self._ensure_open()
        return self._events.stream(request)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        tasks = tuple(managed.task for managed in self._runs.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._runs.clear()
        self._events.close()
        self._contexts.close()


__all__ = ["AgentService"]
