"""Pure Python AgentService Core for v2.3C-2."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agent.runtime_store.errors import DurableStoreError, StoreErrorCode
from agent.runtime_store import WORKER_LEASE_SECONDS

from .context_factory import ServiceContextFactory
from .contracts import (
    CreateTaskRequest,
    EventStreamRequest,
    ListRunsRequest,
    ListTasksRequest,
    ResumeRunRequest,
    RunHandle,
    RunLookupRequest,
    RunSnapshot,
    StartRunRequest,
    TaskHandle,
    TaskLookupRequest,
    TaskSnapshot,
)
from .errors import AgentServiceError, ServiceErrorCode

if TYPE_CHECKING:
    from agent.interruption import CancelRunRequest
    from agent.runtime_store import SqliteRuntimeStore
    from .event_repository import EventRepository
    from .execution_launcher import ExecutionLauncher


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
        defer_context_creation: bool = False,
    ) -> None:
        import asyncio

        self._store = runtime_store
        self._launcher = launcher
        self._contexts = context_factory or ServiceContextFactory(runtime_store)
        # Durable SQLite events are the default Source of Truth in C-3.  A
        # caller may still inject an explicit repository for deterministic
        # tests or a future adapter, but the Service no longer silently falls
        # back to an in-memory/empty stream.
        self._events: Any | None = event_repository
        self._projector: Any | None = None
        self._cancellation: Any | None = None
        self._defer_context_creation = defer_context_creation
        self._runs: dict[str, _ManagedRun] = {}
        self._approval_waiters: dict[str, Any] = {}
        self._scheduler_task: Any | None = None
        self._task_errors: dict[str, AgentServiceError] = {}
        self._operation_lock = asyncio.Lock()
        self._closed = False

    def _event_repository(self) -> Any:
        if self._events is None:
            from .event_repository import SqliteEventRepository

            self._events = SqliteEventRepository(self._store)
        return self._events

    def _run_projector(self) -> Any:
        if self._projector is None:
            from .run_projector import RunProjector

            self._projector = RunProjector()
        return self._projector

    def _cancellation_coordinator(self) -> Any:
        if self._cancellation is None:
            from agent.interruption import CancellationCoordinator

            self._cancellation = CancellationCoordinator(self._store)
        return self._cancellation

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
            StoreErrorCode.SESSION_RUN_ACTIVE: ServiceErrorCode.RUN_ALREADY_ACTIVE,
            StoreErrorCode.STORE_BUSY: getattr(
                ServiceErrorCode, "STORE_BUSY", ServiceErrorCode.INVALID_REQUEST
            ),
            StoreErrorCode.STORE_CLOSED: ServiceErrorCode.SERVICE_CLOSED,
            StoreErrorCode.INTERRUPTION_REQUEST_CONFLICT: ServiceErrorCode.REQUEST_ID_CONFLICT,
            StoreErrorCode.RUN_ALREADY_CANCELLING: ServiceErrorCode.RUN_ALREADY_CANCELLING,
            StoreErrorCode.RUN_ALREADY_CANCELLED: ServiceErrorCode.ALREADY_CANCELLED,
            StoreErrorCode.RUN_ALREADY_TIMED_OUT: ServiceErrorCode.ALREADY_TIMED_OUT,
            StoreErrorCode.RUN_NOT_CANCELLABLE: ServiceErrorCode.RUN_NOT_CANCELLABLE,
        }
        code = code_map.get(error.code, getattr(
            ServiceErrorCode, "INTERNAL_ERROR", ServiceErrorCode.INVALID_REQUEST
        ))
        return AgentServiceError(
            code,
            (
                "another Run is already active in this session"
                if error.code is StoreErrorCode.SESSION_RUN_ACTIVE
                else "durable Runtime operation failed"
            ),
            details={
                "store_code": error.code.value,
                **(
                    error.details
                    if error.code is StoreErrorCode.SESSION_RUN_ACTIVE
                    else {}
                ),
            },
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
        from .run_projector import handle_from_head

        return handle_from_head(head, request_id=request_id)

    @staticmethod
    async def _run_with_worker_lease(context: Any, operation: Any) -> Any:
        import asyncio

        view = context.durable_store_view
        if view is None:
            return await operation
        task = asyncio.create_task(operation)
        heartbeat_seconds = WORKER_LEASE_SECONDS / 3
        while not task.done():
            done, _pending = await asyncio.wait(
                {task},
                timeout=heartbeat_seconds,
            )
            if done:
                break
            try:
                view.renew_worker_lease()
            except Exception:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise
        return await task

    async def start_run(self, request: StartRunRequest) -> RunHandle:
        import asyncio

        self._ensure_open()
        from .run_projector import encode_request_reference

        async with self._operation_lock:
            self._store.ensure_session(
                request.tenant_id, request.session_id, request.user_id
            )
            task_id = self._resolve_task_id(request)
            try:
                reservation = self._store.reserve_service_start(
                    request.tenant_id,
                    request.session_id,
                    task_id=task_id,
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
                # launcher/provider call.  The local sidecar opts into the
                # deferred branch so its transport can acknowledge an
                # accepted Run before importing the cold Runtime graph.  The
                # ordinary in-process Service keeps Context construction
                # synchronous, preserving its established lifecycle timing.
                if self._defer_context_creation:
                    task = asyncio.create_task(
                        self._run_start(request, run_id),
                        name=f"tsagent-start:{run_id}",
                    )
                    self._runs[run_id] = _ManagedRun(None, task)
                else:
                    context = self._contexts.create_run(request, run_id=run_id)
                    task = asyncio.create_task(
                        self._run_start(request, run_id, context=context),
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

    async def _run_start(
        self,
        request: StartRunRequest,
        run_id: str,
        *,
        context: Any | None = None,
    ) -> None:
        import asyncio

        try:
            if context is None:
                context = await asyncio.to_thread(
                    self._contexts.create_run,
                    request,
                    run_id=run_id,
                )
            context.delegation = self
            managed = self._runs.get(run_id)
            if managed is not None:
                self._runs[run_id] = _ManagedRun(context, managed.task)
            await self._run_with_worker_lease(
                context,
                self._launcher.start(
                    session_context=context.session,
                    run_context=context,
                    request=request,
                ),
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._task_errors[run_id] = self._runtime_error(error, run_id=run_id)
        finally:
            managed = self._runs.get(run_id)
            if managed is not None and managed.run_context is context:
                self._runs.pop(run_id, None)
            if context is not None:
                self._contexts.release_run(context)

    async def get_run(self, request: RunLookupRequest) -> RunSnapshot:
        self._ensure_open()
        try:
            read = self._store.read_run_snapshot(
                request.tenant_id,
                request.run_id,
                session_id=request.session_id,
            )
            return self._run_projector().project(read, request)
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

    async def list_runs(self, request: "ListRunsRequest") -> list[RunSnapshot]:
        self._ensure_open()
        heads = self._store.list_run_heads(
            request.tenant_id,
            request.session_id,
            limit=request.limit,
        )
        snapshots: list[RunSnapshot] = []
        for head in heads:
            lookup = RunLookupRequest(
                tenant_id=request.tenant_id,
                user_id=request.user_id,
                session_id=request.session_id,
                run_id=head.run_id,
                request_id=request.request_id,
            )
            read = self._store.read_run_snapshot(
                request.tenant_id,
                head.run_id,
                session_id=request.session_id,
            )
            snapshots.append(self._run_projector().project(read, lookup))
        return snapshots

    def _resolve_task_id(self, request: StartRunRequest) -> str:
        """Every Run belongs to a Task; absent an explicit id one is
        implicitly created so the run tree is always well-formed."""
        if not request.task_id:
            import uuid

            head = self._store.create_task_head(
                request.tenant_id,
                request.session_id,
                f"task-{uuid.uuid4().hex[:12]}",
            )
            return head.task_id
        # Delegated Runs attach to a Task created in the parent session; the
        # task_id scope is tenant-wide, not per-session.
        if self._store.get_task_head(request.tenant_id, request.task_id) is None:
            raise AgentServiceError(
                ServiceErrorCode.INVALID_REQUEST,
                "task not found in the requested tenant scope",
            )
        return request.task_id

    async def create_task(self, request: CreateTaskRequest) -> TaskHandle:
        self._ensure_open()
        import uuid

        if request.parent_task_id and self._store.get_task_head(
            request.tenant_id,
            request.parent_task_id,
            session_id=request.session_id,
        ) is None:
            raise AgentServiceError(
                ServiceErrorCode.INVALID_REQUEST,
                "parent task not found in the requested session scope",
            )
        head = self._store.create_task_head(
            request.tenant_id,
            request.session_id,
            request.task_id or f"task-{uuid.uuid4().hex[:12]}",
            parent_task_id=request.parent_task_id,
            origin_planet=request.origin_planet,
            owner_profile_id=request.owner_profile_id,
        )
        return TaskHandle(
            tenant_id=head.tenant_id,
            session_id=head.session_id,
            task_id=head.task_id,
            parent_task_id=head.parent_task_id,
            origin_planet=head.origin_planet,
            owner_profile_id=head.owner_profile_id,
            created_at=head.created_at,
        )

    async def list_tasks(self, request: ListTasksRequest) -> list[TaskSnapshot]:
        self._ensure_open()
        heads = self._store.list_task_heads(
            request.tenant_id,
            request.session_id,
            limit=request.limit,
        )
        task_runs = {
            head.task_id: self._store.list_runs_for_task(
                request.tenant_id, head.task_id
            )
            for head in heads
        }
        run_ids_by_task = {
            task_id: [run.run_id for run in task_run_heads]
            for task_id, task_run_heads in task_runs.items()
        }
        latest_status_by_task = {
            task_id: task_run_heads[0].run_status
            for task_id, task_run_heads in task_runs.items()
            if task_run_heads
        }
        children_by_parent: dict[str, list[str]] = {}
        for head in heads:
            if head.parent_task_id:
                children_by_parent.setdefault(head.parent_task_id, []).append(head.task_id)
        return [
            TaskSnapshot(
                tenant_id=head.tenant_id,
                session_id=head.session_id,
                task_id=head.task_id,
                parent_task_id=head.parent_task_id,
                origin_planet=head.origin_planet,
                owner_profile_id=head.owner_profile_id,
                created_at=head.created_at,
                status=latest_status_by_task.get(head.task_id, "CREATED"),
                run_ids=tuple(run_ids_by_task.get(head.task_id, ())),
                child_task_ids=tuple(children_by_parent.get(head.task_id, ())),
            )
            for head in heads
        ]

    async def get_task(self, request: TaskLookupRequest) -> TaskSnapshot:
        self._ensure_open()
        head = self._store.get_task_head(
            request.tenant_id,
            request.task_id,
            session_id=request.session_id,
        )
        if head is None:
            raise AgentServiceError(
                ServiceErrorCode.RUN_NOT_FOUND,
                "task not found in the requested session scope",
            )
        snapshots = await self.list_tasks(
            ListTasksRequest(
                tenant_id=request.tenant_id,
                user_id=request.user_id,
                session_id=request.session_id,
                request_id=request.request_id,
            )
        )
        return next(s for s in snapshots if s.task_id == head.task_id)

    async def create_session(self, request: Any) -> Any:
        self._ensure_open()
        head = self._store.ensure_session(
            request.tenant_id, request.session_id, request.user_id
        )
        return self._session_summary(head)

    async def list_sessions(self, request: Any) -> list[Any]:
        self._ensure_open()
        heads = self._store.list_sessions(request.tenant_id, limit=request.limit)
        result = []
        for head in heads:
            session_runs = self._store.list_run_heads(
                head.tenant_id, head.session_id, limit=2000
            )
            latest = session_runs[0] if session_runs else None
            result.append(
                self._session_summary(
                    head, run_count=len(session_runs), latest_run=latest
                )
            )
        return result

    @staticmethod
    def _session_summary(
        head: Any, *, run_count: int = 0, latest_run: Any = None
    ) -> Any:
        from .contracts import SessionSummary

        return SessionSummary(
            tenant_id=head.tenant_id,
            session_id=head.session_id,
            user_id=head.user_id,
            created_at=head.created_at,
            run_count=run_count,
            latest_run_id="" if latest_run is None else latest_run.run_id,
            latest_status="" if latest_run is None else latest_run.run_status,
            latest_updated_at="" if latest_run is None else latest_run.updated_at,
        )

    async def spawn_subtask(
        self,
        run_context: Any,
        *,
        request_text: str,
        target_planet: str = "",
        profile_id: str = "",
        context_refs: tuple[str, ...] | list[str] = (),
    ) -> str:
        """Delegate a subtask: child Task -> child Run -> result back to
        the parent's tool result.  The parent run's loop blocks on this
        call; the delegation tree and handoff event are durable."""
        import asyncio
        import uuid

        view = run_context.durable_store_view
        tenant_id, session_id = view.tenant_id, view.session_id
        parent_head = self._store.get_run_head(
            tenant_id, run_context.run_id, session_id=session_id
        )
        resolved_refs = self._resolve_context_refs(
            run_context, context_refs
        )
        # Sun routing: an unspecified target resolves against the workspace
        # capability registry; the routed planet contributes its default
        # profile when the caller did not name one.
        routed_planet = ""
        if not target_planet and not profile_id:
            route = self._route_planet(tenant_id, request_text)
            if route is not None:
                target_planet = route.workspace_id
                routed_planet = route.workspace_id
                profile_id = route.default_profile_id or profile_id
        profile = None
        if profile_id:
            profile = self._store.get_profile(tenant_id, profile_id)
            if profile is None:
                raise AgentServiceError(
                    ServiceErrorCode.INVALID_REQUEST,
                    f"agent profile not found: {profile_id}",
                )
            target_planet = target_planet or profile.planet_affinity
        # The Task tree lives in the parent session; the delegated Run gets a
        # derived session — one active run per session plus context isolation.
        child_task = self._store.create_task_head(
            tenant_id,
            session_id,
            f"task-{uuid.uuid4().hex[:12]}",
            parent_task_id=parent_head.task_id if parent_head else "",
            origin_planet=target_planet,
            owner_profile_id=profile_id,
        )
        child_text = request_text
        if resolved_refs:
            lines = "\n".join(f"- {r}" for r in resolved_refs)
            child_text = f"{request_text}\n\n上下文引用（已验证可用）:\n{lines}"
        if profile is not None:
            label = profile.display_name or profile.profile_id
            planet = f" @{profile.planet_affinity}" if profile.planet_affinity else ""
            child_text = f"[Agent: {label}{planet}]\n{child_text}"
        child_session_id = f"{session_id}:subtask:{child_task.task_id}"
        import json as _json
        handoff = self._store.create_handoff(
            tenant_id,
            session_id,
            f"handoff:{child_task.task_id}",
            source_run_id=run_context.run_id,
            source_task_id=parent_head.task_id if parent_head else "",
            target_task_id=child_task.task_id,
            target_planet=target_planet,
            context_refs_json=_json.dumps(resolved_refs),
            approval_state="not_required",
        )
        handle = await self.start_run(
            StartRunRequest(
                tenant_id=tenant_id,
                user_id=run_context.user_id,
                session_id=child_session_id,
                request_id=f"subtask:{child_task.task_id}",
                request_text=child_text,
                task_id=child_task.task_id,
                metadata={
                    "agent_profile": {
                        "profile_id": profile.profile_id,
                        "model_ref": profile.model_ref,
                        "skill_refs": _json.loads(profile.skill_refs_json or "[]"),
                        "permission_policy": _json.loads(
                            profile.permission_policy_json or "{}"
                        ),
                    }
                } if profile is not None else {},
            )
        )
        self._store.update_handoff_target_run(
            tenant_id, handoff.handoff_id, handle.run_id
        )
        view.store.append_event(
            view.tenant_id,
            view.session_id,
            view.run_id,
            event_id=f"task-delegated:{view.run_id}:{child_task.task_id}",
            event_type="task.delegated",
            timestamp=self._timestamp(),
            payload={
                "child_task_id": child_task.task_id,
                "child_run_id": handle.run_id,
                "child_session_id": child_session_id,
                "parent_task_id": child_task.parent_task_id,
                "target_planet": target_planet,
                "profile_id": profile_id,
                "routed_by": "sun" if routed_planet else "caller",
                "context_refs": resolved_refs,
            },
            run_revision=view.head().current_revision,
            expected_store_generation=view.store_generation,
        )
        try:
            managed = self._runs.get(handle.run_id)
            if managed is not None and managed.task is not None:
                await managed.task
            snapshot = await self.get_run(
                RunLookupRequest(
                    tenant_id=tenant_id,
                    user_id=run_context.user_id,
                    session_id=child_session_id,
                    run_id=handle.run_id,
                    request_id=f"subtask-read:{child_task.task_id}",
                )
            )
        except Exception as error:
            view.store.append_event(
            view.tenant_id,
            view.session_id,
            view.run_id,
            event_id=f"task-result:{view.run_id}:{child_task.task_id}",
            event_type="task.result",
            timestamp=self._timestamp(),
            payload={
                    "child_task_id": child_task.task_id,
                    "child_run_id": handle.run_id,
                    "outcome": "error",
                    "detail": str(error)[:500],
                },
            run_revision=view.head().current_revision,
            expected_store_generation=view.store_generation,
        )
            return f"子任务执行失败: {error}"
        output = snapshot.output.text if snapshot.output else ""
        view.store.append_event(
            view.tenant_id,
            view.session_id,
            view.run_id,
            event_id=f"task-result:{view.run_id}:{child_task.task_id}",
            event_type="task.result",
            timestamp=self._timestamp(),
            payload={
                "child_task_id": child_task.task_id,
                "child_run_id": handle.run_id,
                "outcome": snapshot.status.value,
            },
            run_revision=view.head().current_revision,
            expected_store_generation=view.store_generation,
        )
        if snapshot.status.value == "COMPLETED" and output:
            return output[:8000]
        return (
            f"子任务 {handle.run_id} 结束于 {snapshot.status.value}"
            + (f": {output[:2000]}" if output else "")
        )

    async def list_handoffs(self, request: Any) -> list[Any]:
        self._ensure_open()
        import json as _json

        records = self._store.list_handoffs(
            request.tenant_id, request.session_id, limit=request.limit
        )
        from .contracts import HandoffSummary

        return [
            HandoffSummary(
                handoff_id=r.handoff_id,
                source_run_id=r.source_run_id,
                source_task_id=r.source_task_id,
                target_task_id=r.target_task_id,
                target_run_id=r.target_run_id,
                target_planet=r.target_planet,
                context_refs=tuple(
                    str(v) for v in _json.loads(r.context_refs_json or "[]")
                ),
                approval_state=r.approval_state,
                created_at=r.created_at,
            )
            for r in records
        ]

    async def upsert_profile(self, request: Any) -> Any:
        self._ensure_open()
        import json as _json

        record = self._store.upsert_profile(
            request.tenant_id,
            request.profile_id,
            display_name=request.display_name,
            planet_affinity=request.planet_affinity,
            model_ref=request.model_ref,
            skill_refs_json=_json.dumps(list(request.skill_refs)),
            permission_policy_json=_json.dumps(dict(request.permission_policy)),
        )
        return self._profile_summary(record)

    async def list_profiles(self, request: Any) -> list[Any]:
        self._ensure_open()
        return [
            self._profile_summary(r)
            for r in self._store.list_profiles(
                request.tenant_id, limit=request.limit
            )
        ]

    @staticmethod
    def _profile_summary(record: Any) -> Any:
        import json as _json
        from .contracts import ProfileSummary

        return ProfileSummary(
            profile_id=record.profile_id,
            display_name=record.display_name,
            planet_affinity=record.planet_affinity,
            model_ref=record.model_ref,
            skill_refs=tuple(
                str(v) for v in _json.loads(record.skill_refs_json or "[]")
            ),
            permission_policy=_json.loads(record.permission_policy_json or "{}"),
            updated_at=record.updated_at,
        )

    async def get_settings(self, request: Any) -> Any:
        self._ensure_open()
        from .. import llm as _llm_module
        from .contracts import SettingsSummary

        router = _llm_module.llm
        try:
            _provider, name = router._get_active_provider()
        except Exception:
            name = "unavailable"
        return SettingsSummary(
            provider_mode=router._provider_mode,
            active_provider=name,
            active_model=(
                _llm_module.DEEPSEEK_MODEL if name == "deepseek"
                else _llm_module.OLLAMA_MODEL if name == "ollama" else ""
            ),
            deepseek_model=_llm_module.DEEPSEEK_MODEL,
            ollama_model=_llm_module.OLLAMA_MODEL,
        )

    async def update_settings(self, request: Any) -> Any:
        """Live provider/model changes — same in-process semantics as the
        CLI /model command; next request picks them up."""
        self._ensure_open()
        from .. import llm as _llm_module

        router = _llm_module.llm
        if request.provider_mode:
            router._provider_mode = request.provider_mode
        if request.deepseek_model:
            _llm_module.DEEPSEEK_MODEL = request.deepseek_model
            router._deepseek = None
        if request.ollama_model:
            _llm_module.OLLAMA_MODEL = request.ollama_model
            router._ollama = None
        return await self.get_settings(request)

    async def upsert_workspace(self, request: Any) -> Any:
        self._ensure_open()
        import json as _json

        record = self._store.upsert_workspace(
            request.tenant_id,
            request.workspace_id,
            display_name=request.display_name,
            capabilities_json=_json.dumps(list(request.capabilities)),
            default_profile_id=request.default_profile_id,
            description=request.description,
        )
        return self._workspace_summary(record)

    async def list_workspaces(self, request: Any) -> list[Any]:
        self._ensure_open()
        return [
            self._workspace_summary(r)
            for r in self._store.list_workspaces(
                request.tenant_id, limit=request.limit
            )
        ]

    @staticmethod
    def _workspace_summary(record: Any) -> Any:
        import json as _json
        from .contracts import WorkspaceSummary

        return WorkspaceSummary(
            workspace_id=record.workspace_id,
            display_name=record.display_name,
            capabilities=tuple(
                str(v) for v in _json.loads(record.capabilities_json or "[]")
            ),
            default_profile_id=record.default_profile_id,
            description=record.description,
            updated_at=record.updated_at,
        )

    async def list_history(self, request: Any) -> list[Any]:
        """Jupiter history: request/answer pairs across the session's runs,
        derived from durable run records — never a second data source."""
        self._ensure_open()
        from .contracts import HistoryEntry

        heads = self._store.list_run_heads(
            request.tenant_id, request.session_id, limit=request.limit
        )
        entries: list[Any] = []
        for head in reversed(heads):  # chronological
            try:
                read = self._store.read_run_snapshot(
                    request.tenant_id, head.run_id, session_id=request.session_id
                )
            except DurableStoreError:
                continue
            record = {}
            if read.start_intent is not None:
                try:
                    import json as _json

                    reference = getattr(read.start_intent, "external_reference", "") or ""
                    record = _json.loads(reference[reference.index("{"):])
                except (TypeError, ValueError):
                    record = {}
            entries.append(
                HistoryEntry(
                    run_id=head.run_id,
                    task_id=head.task_id,
                    request_text=str(record.get("request_text", "")),
                    answer_text=(
                        read.output.text[:4000] if read.output else ""
                    ),
                    status=head.run_status,
                    updated_at=head.updated_at,
                )
            )
        return entries

    def _resolve_context_refs(
        self, run_context: Any, refs: Any
    ) -> list[str]:
        """Validate each context ref against its domain — delegation only
        carries references that actually resolve (no dead pointers)."""
        view = run_context.durable_store_view
        workspace = getattr(run_context, "workspace", None)
        resolved: list[str] = []
        for ref in refs or ():
            raw = str(ref)
            kind, _, target = raw.partition(":")
            if kind == "file" and target:
                if workspace is None:
                    raise AgentServiceError(
                        ServiceErrorCode.INVALID_REQUEST,
                        "context ref file: requires a workspace",
                    )
                try:
                    workspace.resolve_path(target, must_exist=True)
                except Exception as error:
                    raise AgentServiceError(
                        ServiceErrorCode.INVALID_REQUEST,
                        f"context ref not readable in workspace: {raw}",
                    ) from error
                resolved.append(f"file:{target}")
            elif kind == "task" and target:
                if self._store.get_task_head(view.tenant_id, target) is None:
                    raise AgentServiceError(
                        ServiceErrorCode.INVALID_REQUEST,
                        f"context ref task not found: {raw}",
                    )
                resolved.append(f"task:{target}")
            elif kind == "run" and target:
                if self._store.get_run_head(
                    view.tenant_id, target, session_id=view.session_id
                ) is None:
                    raise AgentServiceError(
                        ServiceErrorCode.INVALID_REQUEST,
                        f"context ref run not found in session: {raw}",
                    )
                resolved.append(f"run:{target}")
            elif kind == "artifact" and target:
                snapshot = self._store.read_run_snapshot(
                    view.tenant_id, view.run_id, session_id=view.session_id
                )
                known = {
                    str(getattr(a, "artifact_id", ""))
                    for a in getattr(snapshot, "artifacts", ()) or ()
                } | {
                    str(getattr(a, "path", ""))
                    for a in getattr(snapshot, "artifacts", ()) or ()
                }
                if target not in known:
                    raise AgentServiceError(
                        ServiceErrorCode.INVALID_REQUEST,
                        f"context ref artifact not produced by this run: {raw}",
                    )
                resolved.append(f"artifact:{target}")
            else:
                raise AgentServiceError(
                    ServiceErrorCode.INVALID_REQUEST,
                    f"unsupported context ref scheme: {raw}",
                )
        return resolved

    def _route_planet(self, tenant_id: str, request_text: str) -> Any:
        """Sun v0: score request text against each workspace's declared
        capabilities; highest score wins.  No registry match → None
        (universal executor), never a guess."""
        import json as _json

        workspaces = self._store.list_workspaces(tenant_id)
        text = request_text.lower()
        best = None
        best_score = 0
        for workspace in workspaces:
            caps = _json.loads(workspace.capabilities_json or "[]")
            haystacks = [str(workspace.workspace_id), str(workspace.display_name)]
            haystacks += [str(c) for c in caps]
            score = sum(
                1 for needle in haystacks
                if needle and needle.lower() in text
            )
            if score > best_score:
                best = workspace
                best_score = score
        return best

    async def create_schedule(self, request: Any) -> Any:
        self._ensure_open()
        import uuid

        from .contracts import ScheduleSummary

        record = self._store.create_schedule(
            request.tenant_id,
            request.schedule_id or f"sched-{uuid.uuid4().hex[:12]}",
            request.session_id,
            request.request_text,
            request.interval_seconds,
            user_id=request.user_id,
            target_planet=request.target_planet,
        )
        self._ensure_scheduler()
        return self._schedule_summary(record)

    async def list_schedules(self, request: Any) -> list[Any]:
        self._ensure_open()
        self._ensure_scheduler()
        records = self._store.list_schedules(
            request.tenant_id,
            session_id=request.session_id,
            status="ACTIVE",
            limit=request.limit,
        ) + self._store.list_schedules(
            request.tenant_id,
            session_id=request.session_id,
            status="PAUSED",
            limit=request.limit,
        )
        return [self._schedule_summary(r) for r in records]

    async def update_schedule(self, request: Any) -> Any:
        self._ensure_open()
        record = self._store.update_schedule_status(
            request.tenant_id, request.schedule_id, request.status
        )
        self._ensure_scheduler()
        return self._schedule_summary(record)

    @staticmethod
    def _schedule_summary(record: Any) -> Any:
        from .contracts import ScheduleSummary

        return ScheduleSummary(
            schedule_id=record.schedule_id,
            session_id=record.session_id,
            request_text=record.request_text,
            user_id=record.user_id,
            target_planet=record.target_planet,
            interval_seconds=record.interval_seconds,
            status=record.status,
            next_fire_at=record.next_fire_at,
            last_fired_at=record.last_fired_at,
            last_run_id=record.last_run_id,
            fire_count=record.fire_count,
            created_at=record.created_at,
        )

    def _ensure_scheduler(self) -> None:
        import asyncio

        if self._scheduler_task is None or self._scheduler_task.done():
            self._scheduler_task = asyncio.create_task(
                self._scheduler_loop(), name="tsagent-scheduler"
            )

    async def _scheduler_loop(self) -> None:
        """Saturn: claim due schedules -> fire one Run per tick.

        Each fire gets a derived session (independent context, no
        one-active-run-per-session conflict).  The durable claim advances
        next_fire_at atomically so a restart cannot double-fire.
        """
        import asyncio
        from datetime import datetime, timezone

        while True:
            await asyncio.sleep(1.0)
            if self._closed:
                return
            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            claimed = self._store.claim_due_schedules_all(now, limit=10)
            for schedule in claimed:
                try:
                    handle = await self.start_run(
                        StartRunRequest(
                            tenant_id=schedule.tenant_id,
                            user_id=schedule.user_id or "saturn",
                            session_id=(
                                f"{schedule.session_id}:sched:"
                                f"{schedule.schedule_id}"
                            ),
                            request_id=(
                                f"sched:{schedule.schedule_id}:"
                                f"{schedule.fire_count}"
                            ),
                            request_text=schedule.request_text,
                        )
                    )
                    self._store.update_schedule_run(
                        schedule.tenant_id, schedule.schedule_id, handle.run_id
                    )
                except Exception:
                    import traceback

                    traceback.print_exc()

    async def request_approval(
        self,
        run_context: Any,
        *,
        approval_id: str,
        action: str,
        scope: str = "",
        params: dict[str, Any] | None = None,
        risk_level: str = "R1",
    ) -> Any:
        """Runtime-side approval gate: persist the request, park the run in
        WAITING_USER, and block until an external ``decide_approval`` lands.

        Idempotent on approval_id — a replay after restart returns the
        already-decided record instead of re-parking the run.
        """
        import asyncio
        import json

        view = run_context.durable_store_view
        record = self._store.create_approval(
            view.tenant_id,
            view.session_id,
            view.run_id,
            approval_id,
            action=action,
            scope=scope,
            params_json=json.dumps(params or {}),
            risk_level=risk_level,
        )
        if record.status != "PENDING":
            return record
        waiter = asyncio.Event()
        self._approval_waiters[record.approval_id] = waiter
        timestamp = self._timestamp()
        try:
            view.transition_run_with_event(
                run_status="WAITING_USER",
                event_id=f"approval-requested:{view.run_id}:{record.approval_id}",
                event_type="approval.requested",
                timestamp=timestamp,
                payload={
                    "approval_id": record.approval_id,
                    "action": record.action,
                    "scope": record.scope,
                    "risk_level": record.risk_level,
                    "params": dict(params or {}),
                },
            )
            await waiter.wait()
            decided = self._store.get_approval(
                view.tenant_id, view.run_id, record.approval_id
            )
            view.transition_run_with_event(
                run_status="RUNNING",
                event_id=f"approval-decided:{view.run_id}:{record.approval_id}",
                event_type="approval.decided",
                timestamp=self._timestamp(),
                payload={
                    "approval_id": record.approval_id,
                    "decision": decided.status,
                    "decided_by": decided.decided_by,
                },
            )
            return decided
        finally:
            self._approval_waiters.pop(record.approval_id, None)

    async def decide_approval(self, request: Any) -> Any:
        """Wire decision for one pending approval (idempotent replay-safe)."""
        self._ensure_open()
        record = self._store.decide_approval(
            request.tenant_id,
            request.run_id,
            request.approval_id,
            decision="APPROVED" if request.decision == "approve" else "DENIED",
            decided_by=request.decided_by or request.user_id,
        )
        waiter = self._approval_waiters.get(request.approval_id)
        if waiter is not None:
            waiter.set()
        return self._approval_summary(record)

    async def list_approvals(self, request: Any) -> list[Any]:
        self._ensure_open()
        records = self._store.list_approvals(
            request.tenant_id,
            request.session_id,
            run_id=request.run_id or None,
            status=request.status.upper() or None,
            limit=request.limit,
        )
        return [self._approval_summary(record) for record in records]

    @staticmethod
    def _approval_summary(record: Any) -> Any:
        import json

        from .contracts import ApprovalSummary

        try:
            params = json.loads(record.params_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            params = {}
        return ApprovalSummary(
            approval_id=record.approval_id,
            run_id=record.run_id,
            action=record.action,
            scope=record.scope,
            params=params,
            risk_level=record.risk_level,
            status=record.status,
            decided_by=record.decided_by,
            decided_at=record.decided_at,
            created_at=record.created_at,
        )

    @staticmethod
    def _timestamp() -> str:
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat()

    async def pause_run(self, request: Any) -> RunHandle:
        """Suspend a locally-executing run at the next tool boundary.

        The parked asyncio task waits on a gate — no progress is lost, no
        checkpoint needed; resume_run releases the gate."""
        import asyncio

        self._ensure_open()
        async with self._operation_lock:
            head = self._store.get_run_head(
                request.tenant_id, request.run_id, session_id=request.session_id
            )
            if head is None or head.run_status != "RUNNING":
                raise AgentServiceError(
                    ServiceErrorCode.INVALID_REQUEST,
                    "run is not actively running",
                )
            managed = self._runs.get(request.run_id)
            if managed is None or managed.run_context is None:
                raise AgentServiceError(
                    ServiceErrorCode.INVALID_REQUEST,
                    "run is not executing in this service",
                )
            run_context = managed.run_context
            gate = asyncio.Event()  # starts unset → wait() parks
            run_context._pause_gate = gate
            view = run_context.durable_store_view
            timestamp = self._timestamp()
            view.transition_run_with_event(
                run_status="SUSPENDED",
                event_id=f"run_suspended:{view.run_id}:{view.head().current_revision}",
                event_type="run_suspended",
                timestamp=timestamp,
                payload={
                    "requested_by": getattr(request, "requested_by", "")
                    or request.user_id,
                },
            )
            return await self.get_run(
                RunLookupRequest(
                    tenant_id=request.tenant_id,
                    user_id=request.user_id,
                    session_id=request.session_id,
                    run_id=request.run_id,
                    request_id=f"{request.request_id}-read",
                )
            )

    async def resume_run(self, request: ResumeRunRequest) -> RunHandle:
        import asyncio

        self._ensure_open()
        async with self._operation_lock:
            context = None
            scheduled = False
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
                status = str(head.run_status).upper()
                # Gate-parked run: the asyncio task is alive, just waiting —
                # release the gate and transition back; do NOT relaunch.
                if status == "SUSPENDED":
                    managed = self._runs.get(request.run_id)
                    if managed is not None and managed.run_context is not None:
                        gate = getattr(managed.run_context, "_pause_gate", None)
                        view = managed.run_context.durable_store_view
                        view.transition_run_with_event(
                            run_status="RUNNING",
                            event_id=f"run_resumed:{view.run_id}:{view.head().current_revision}",
                            event_type="run_resumed",
                            timestamp=self._timestamp(),
                            payload={"reason": "unsuspend"},
                        )
                        if gate is not None:
                            gate.set()
                        return await self.get_run(
                            RunLookupRequest(
                                tenant_id=request.tenant_id,
                                user_id=request.user_id,
                                session_id=request.session_id,
                                run_id=request.run_id,
                                request_id=f"{request.request_id}-read",
                            )
                        )
                    # No local task (service restarted while suspended) — fall
                    # through to the normal checkpoint-resume path.
                if status in {
                    "COMPLETED",
                    "FAILED_TERMINAL",
                    "CANCELLED",
                    "TIMED_OUT",
                    "CANCELLING",
                }:
                    code = (
                        ServiceErrorCode.RUN_ALREADY_CANCELLING
                        if status == "CANCELLING"
                        else ServiceErrorCode.RESUME_NOT_ALLOWED
                    )
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

                context = self._contexts.create_run(
                    request,
                    run_id=request.run_id,
                    takeover_writer=True,
                )
                context.delegation = self
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
                scheduled = True
                return self._handle(head, request.request_id)
            except AgentServiceError:
                if context is not None and not scheduled:
                    self._contexts.release_run(context)
                raise
            except DurableStoreError as error:
                if context is not None and not scheduled:
                    self._contexts.release_run(context)
                raise self._store_error(
                    error,
                    run_id=request.run_id,
                    request_id=request.request_id,
                ) from error
            except Exception as error:
                if context is not None and not scheduled:
                    self._contexts.release_run(context)
                raise self._runtime_error(error, run_id=request.run_id) from error

    async def _run_resume(self, request: ResumeRunRequest, context: Any) -> None:
        import asyncio

        run_id = context.run_id
        try:
            await self._run_with_worker_lease(
                context,
                self._launcher.resume(run_context=context, request=request),
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

    async def cancel_run(self, request: CancelRunRequest) -> RunSnapshot:
        """Durably accept cancellation without claiming execution has stopped."""

        self._ensure_open()
        lookup = RunLookupRequest(
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            session_id=request.session_id,
            run_id=request.run_id,
            request_id=request.request_id,
        )
        # Verify the complete public identity before the Store writes the
        # control-plane intent.  The transaction revalidates scope/lifecycle.
        await self.get_run(lookup)
        try:
            self._cancellation = self._cancellation_coordinator()
            self._cancellation.request_cancel(request)
            return await self.get_run(lookup)
        except AgentServiceError:
            raise
        except DurableStoreError as error:
            raise self._store_error(
                error,
                run_id=request.run_id,
                request_id=request.request_id,
                lookup=error.code is StoreErrorCode.IDENTITY_MISMATCH,
            ) from error
        except Exception as error:
            raise self._runtime_error(error, run_id=request.run_id) from error

    async def list_artifacts(
        self,
        request: RunLookupRequest,
    ) -> tuple[Any, ...]:
        snapshot = await self.get_run(request)
        return snapshot.artifacts

    def stream_events(self, request: EventStreamRequest):
        self._ensure_open()
        return self._event_repository().stream(request)

    async def close(self) -> None:
        import asyncio

        if self._closed:
            return
        self._closed = True
        tasks = tuple(managed.task for managed in self._runs.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._runs.clear()
        if self._events is not None:
            self._events.close()
        self._contexts.close()


__all__ = ["AgentService"]
