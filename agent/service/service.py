"""Pure Python AgentService Core for v2.3C-2."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
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

# Derived delegated-run sessions share this id marker — one canonical
# convention used at construction and anywhere they must be filtered out.
SUBTASK_SESSION_MARKER = ":subtask:"

if TYPE_CHECKING:
    from agent.interruption import CancelRunRequest
    from agent.runtime_store import SqliteRuntimeStore
    from .event_repository import EventRepository
    from .execution_launcher import ExecutionLauncher


@dataclass
class _ManagedRun:
    run_context: Any
    task: asyncio.Task[None]


def _topo_task_order(task_nodes: list, edges: list) -> list:
    """Kahn's sort over task nodes — edges define execution order; cycles fail."""
    ids = {str(n.get("id")) for n in task_nodes}
    indeg = {i: 0 for i in ids}
    adj: dict[str, list[str]] = {i: [] for i in ids}
    for e in edges:
        src_, dst_ = str(e.get("from")), str(e.get("to"))
        if src_ in ids and dst_ in ids:
            adj[src_].append(dst_)
            indeg[dst_] += 1
    queue = sorted(i for i, d in indeg.items() if d == 0)
    order: list = []
    by_id = {str(n.get("id")): n for n in task_nodes}
    while queue:
        cur = queue.pop(0)
        order.append(by_id[cur])
        for nxt in adj[cur]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)
    if len(order) != len(task_nodes):
        raise AgentServiceError(
            ServiceErrorCode.INVALID_REQUEST,
            "workflow graph contains a cycle",
        )
    return order


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
        if context_factory is None:
            context_factory = ServiceContextFactory(runtime_store)
        self._contexts = context_factory
        # Durable SQLite events are the default Source of Truth in C-3.  A
        # caller may still inject an explicit repository for deterministic
        # tests or a future adapter, but the Service no longer silently falls
        # back to an in-memory/empty stream.
        self._events: Any | None = event_repository
        self._projector: Any | None = None
        self._cancellation: Any | None = None
        self._defer_context_creation = defer_context_creation
        self._runs: dict[str, _ManagedRun] = {}
        self._approval_waiters: dict[tuple[str, str, str], Any] = {}
        self._scheduler_task: Any | None = None
        self._graph_tasks: set[Any] = set()
        self._task_errors: dict[str, AgentServiceError] = {}
        self._operation_lock = asyncio.Lock()
        self._closed = False
        # Saturn autostart: a rebuilt Service must fire due schedules
        # without waiting for a schedule API call.
        self._maybe_start_scheduler()

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
        self._maybe_start_scheduler()

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
            StoreErrorCode.INVALID_ARGUMENT: ServiceErrorCode.INVALID_REQUEST,
        }
        code = code_map.get(error.code, getattr(
            ServiceErrorCode, "INTERNAL_ERROR", ServiceErrorCode.INVALID_REQUEST
        ))
        return AgentServiceError(
            code,
            (
                "another Run is already active in this session"
                if error.code is StoreErrorCode.SESSION_RUN_ACTIVE
                else str(error)
                if error.code is StoreErrorCode.INVALID_ARGUMENT
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
        try:
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
        except asyncio.CancelledError:
            # asyncio.wait does not propagate cancellation into the watched
            # task — release the launcher child before letting it unwind.
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise

    async def start_run(self, request: StartRunRequest) -> RunHandle:
        import asyncio

        self._ensure_open()
        from .run_projector import encode_request_reference

        async with self._operation_lock:
            self._store.ensure_session(
                request.tenant_id, request.session_id, request.user_id,
                role="owner",
            )
            task_id = self._resolve_task_id(request)
            # Already-admitted requests reuse the original Run identity —
            # replay must not allocate a new run id or invoke the workspace
            # resolver (which may create a directory as a side effect).
            admitted = self._store.find_idempotency(
                request.tenant_id, request.request_id
            )
            if admitted is not None:
                requested_run_id = str(admitted.run_id)
                external_reference = ""
            else:
                # Allocate the run id before workspace resolution — the
                # recorded root must be the accepted Run's own directory
                # (per-run roots), not the session-level fallback an empty
                # run_id resolves to.
                import uuid

                requested_run_id = (
                    str(getattr(request, "run_id", "") or "")
                    or f"run-{uuid.uuid4().hex}"
                )
                explicit_root = str(
                    getattr(request, "workspace_root", "") or ""
                ).strip()
                run_root = (
                    Path(explicit_root).resolve()
                    if explicit_root
                    else self._contexts.run_workspace(
                        request.tenant_id,
                        request.session_id,
                        requested_run_id,
                    )
                )
                if run_root is None:
                    # Deterministic root at admission: an unbound session
                    # gets an app-managed workspace, registered + bound so
                    # it is visible and later runs share it — never cwd.
                    run_root = self._default_session_workspace(
                        request.tenant_id, request.session_id,
                    )
                elif (
                    not explicit_root
                    and self._contexts.uses_default_resolver
                    and not self._store.session_workspace_id(
                        request.tenant_id, request.session_id
                    )
                ):
                    # Resolved from the process-level root with no durable
                    # binding — register the session binding so the source
                    # is a visible fact rather than an implicit fallback.
                    self._bind_session_workspace(
                        request.tenant_id, request.session_id, run_root
                    )
                context_refs = self._validate_store_refs(
                    request.tenant_id,
                    request.session_id,
                    "",
                    getattr(request, "context_refs", ()),
                    root=run_root,
                    allow_artifact=False,
                )
                external_reference = encode_request_reference(
                    request,
                    run_id=requested_run_id,
                    workspace_root=str(run_root) if run_root else "",
                    context_refs=context_refs,
                )
            try:
                reservation = self._store.reserve_service_start(
                    request.tenant_id,
                    request.session_id,
                    task_id=task_id,
                    requested_run_id=requested_run_id,
                    request_id=request.request_id,
                    request_digest=request.request_digest,
                    writer_id=self._contexts.writer_id,
                    external_reference=external_reference,
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
                failure = self._store_error(
                    error,
                    run_id=run_id,
                    request_id=request.request_id,
                )
                self._persist_start_failure(
                    tenant_id=request.tenant_id,
                    session_id=request.session_id,
                    run_id=run_id,
                    request_id=request.request_id,
                    context=None,
                    error=error,
                )
                raise failure from error
            except Exception as error:
                self._persist_start_failure(
                    tenant_id=request.tenant_id,
                    session_id=request.session_id,
                    run_id=run_id,
                    request_id=request.request_id,
                    context=None,
                    error=error,
                )
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
            self._persist_start_failure(
                tenant_id=request.tenant_id,
                session_id=request.session_id,
                run_id=run_id,
                request_id=request.request_id,
                context=context,
                error=error,
            )
        finally:
            managed = self._runs.get(run_id)
            if managed is not None and managed.run_context is context:
                self._runs.pop(run_id, None)
            if context is not None:
                self._contexts.release_run(context)

    def _persist_start_failure(
        self,
        *,
        tenant_id: str,
        session_id: str,
        run_id: str,
        request_id: str,
        context: Any | None,
        error: BaseException,
    ) -> None:
        """The accepting entry owns the start outcome: a Run whose Context
        or launcher never reached execution still commits a durable
        terminal fact, so a later get_run can read the failure reason."""
        from agent.execution_errors import stable_error_message
        import logging

        view = context.durable_store_view if context is not None else None
        if view is None:
            try:
                from agent.runtime_store.view import DurableRuntimeStoreView

                view = DurableRuntimeStoreView(
                    self._store,
                    tenant_id=tenant_id,
                    session_id=session_id,
                    run_id=run_id,
                    request_id=request_id,
                    writer_id=self._contexts.writer_id,
                )
            except DurableStoreError as error:
                logging.getLogger(__name__).warning(
                    "accepted Run %s start failure left in-memory only: %s",
                    run_id,
                    error,
                )
                return
        try:
            head = view.head()
            if head is None or str(head.run_status).upper() != "CREATED":
                # Past the accepted-only state — a failure after execution
                # began is owned by the launcher's own failure commit (and
                # by crash-recovery), not by the start entry.
                return
            view.transition_run_with_event(
                run_status="FAILED_TERMINAL",
                event_id=f"run_failed:{run_id}:{head.current_revision}",
                event_type="run_failed",
                timestamp=self._timestamp(),
                payload={
                    "failure_code": "RUNTIME_EXCEPTION",
                    "failure_class": "start",
                    "failure_detail": stable_error_message(
                        error, fallback="run start failed"
                    )[:300],
                },
            )
        except DurableStoreError as error:
            # A peer owner may have committed the terminal outcome first;
            # the durable head is the authority either way.
            logging.getLogger(__name__).warning(
                "could not persist start failure for accepted Run %s: %s",
                run_id,
                error,
            )

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

    def _default_session_workspace(
        self, tenant_id: str, session_id: str
    ) -> Path:
        """Admission-time fallback for unbound sessions: an app-managed
        directory next to the runtime store, registered in the workspace
        registry and bound to the session — the binding is visible in the
        UI and subsequent runs of the session reuse the same root."""
        if str(self._store.path) == ":memory:":
            import tempfile

            base = Path(tempfile.gettempdir())
        else:
            base = Path(self._store.path).resolve().parent
        root = base / "workspaces" / tenant_id / session_id
        root.mkdir(parents=True, exist_ok=True)
        self._bind_session_workspace(tenant_id, session_id, root)
        return root

    def _bind_session_workspace(
        self, tenant_id: str, session_id: str, root: Path
    ) -> None:
        import json as _json

        workspace_id = f"ws-auto-{session_id}"
        self._store.upsert_workspace(
            tenant_id,
            workspace_id,
            display_name=f"session {session_id} workspace",
            path=str(root),
            paths_json=_json.dumps([str(root)]),
        )
        self._store.assign_session_workspace(
            tenant_id, session_id, workspace_id
        )

    def _resolve_task_id(self, request: StartRunRequest) -> str:
        """Every Run belongs to a Task; absent an explicit id one is
        implicitly created so the run tree is always well-formed."""
        if not request.task_id:
            import uuid

            head = self._store.create_task_head(
                request.tenant_id,
                request.session_id,
                f"task-{uuid.uuid4().hex[:12]}",
                origin_planet=request.origin_planet,
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
            request.tenant_id, request.session_id, request.user_id,
            role="owner",
        )
        return self._session_summary(head)

    async def delete_session(self, request: Any) -> Any:
        self._ensure_open()
        removed = self._store.delete_session(request.tenant_id, request.session_id)
        if not removed:
            raise AgentServiceError(
                ServiceErrorCode.SESSION_NOT_FOUND,
                f"session {request.session_id} was not found",
            )
        return {"removed": True, "session_id": request.session_id}

    async def pick_folder(self, request: Any) -> Any:
        """Open the macOS native 'choose folder' dialog — real Finder picker
        for the desktop client, even when it runs in a dev browser."""
        import asyncio
        proc = await asyncio.create_subprocess_exec(
            "osascript",
            "-e", 'tell application "System Events"',
            "-e", 'activate',
            "-e", 'set chosen to POSIX path of (choose folder with prompt "选择工作区目录")',
            "-e", 'end tell',
            "-e", 'return chosen',
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await proc.communicate()
        path = out.decode().strip().rstrip("/")
        return {"path": path or None}

    async def pick_files(self, request: Any) -> Any:
        """macOS native 'choose file' dialog — multi-select Finder picker,
        same local-user trust boundary as pick_folder."""
        import asyncio
        proc = await asyncio.create_subprocess_exec(
            "osascript",
            "-e", 'tell application "System Events"',
            "-e", 'activate',
            "-e", 'set chosen to choose file with prompt "添加文件" with multiple selections allowed',
            "-e", 'set out to ""',
            "-e", 'repeat with f in chosen',
            "-e", 'set out to out & (POSIX path of f) & linefeed',
            "-e", 'end repeat',
            "-e", 'end tell',
            "-e", 'return out',
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, _ = await proc.communicate()
        paths = [p.strip() for p in out.decode().splitlines() if p.strip()]
        return {"paths": paths}

    async def attach_files(self, request: Any) -> Any:
        """Copy externally-picked files into the session workspace's hidden
        .attachments/ dir and return file: refs the run context accepts."""
        import asyncio
        import hashlib
        import shutil
        from pathlib import Path

        self._ensure_open()
        root = self._contexts.session_workspace(
            request.tenant_id, request.session_id
        )
        if root is None:
            raise AgentServiceError(
                ServiceErrorCode.INVALID_REQUEST,
                "service has no workspace root",
            )
        sources: list[Path] = []
        for raw in request.paths:
            src = Path(raw).expanduser()
            if not src.is_file():
                raise AgentServiceError(
                    ServiceErrorCode.INVALID_REQUEST,
                    f"attachment is not a file: {raw}",
                )
            sources.append(src)

        def _copy() -> list[str]:
            dest_dir = root / ".attachments"
            dest_dir.mkdir(parents=True, exist_ok=True)
            refs: list[str] = []
            for src in sources:
                digest = hashlib.sha256(src.read_bytes()).hexdigest()[:8]
                target = dest_dir / src.name
                if target.exists():
                    if hashlib.sha256(target.read_bytes()).hexdigest()[:8] == digest:
                        refs.append(f"file:.attachments/{target.name}")
                        continue
                    target = dest_dir / f"{src.stem}-{digest}{src.suffix}"
                shutil.copy2(src, target)
                refs.append(f"file:.attachments/{target.name}")
            return refs

        return {"context_refs": await asyncio.to_thread(_copy)}

    async def assign_session_workspace(self, request: Any) -> Any:
        self._store.assign_session_workspace(
            request.tenant_id, request.session_id, request.workspace_id,
        )
        return {"session_id": request.session_id, "workspace_id": request.workspace_id}

    async def delete_workspace(self, request: Any) -> Any:
        removed = self._store.delete_workspace(request.tenant_id, request.workspace_id)
        return {"removed": removed}

    async def reveal_path(self, request: Any) -> Any:
        """Open a path in the OS file manager — Finder on macOS. Params carry
        a plain `path`, same local-user trust boundary as list_directory."""
        import asyncio
        import os
        import sys
        from pathlib import Path

        raw = str(getattr(request, "path", "") or "").strip()
        if not raw:
            return {"revealed": False}
        target = Path(raw).expanduser()
        if sys.platform == "win32":
            os.startfile(str(target))  # type: ignore[attr-defined]
            return {"revealed": True}
        cmd = ("open", str(target)) if sys.platform == "darwin" else ("xdg-open", str(target))
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        return {"revealed": proc.returncode == 0}

    async def list_directory(self, request: Any) -> Any:
        """List subdirectories for the in-page folder picker. Params carry a
        plain `path` — the desktop is a local user, no tenant scoping on fs."""
        import os
        from pathlib import Path
        raw = str(getattr(request, "path", "") or "").strip()
        base = Path(raw).expanduser() if raw else Path.home()
        if not base.is_dir():
            return {"path": str(base), "parent": str(base.parent), "dirs": [], "error": "not a directory"}
        dirs = sorted(
            (d.name + "/" for d in base.iterdir() if d.is_dir() and not d.name.startswith(".")),
            key=str.lower,
        )[:200]
        return {"path": str(base), "parent": str(base.parent), "dirs": dirs, "home": os.path.expanduser("~")}

    async def list_sessions(self, request: Any) -> list[Any]:
        self._ensure_open()
        heads = self._store.list_sessions(request.tenant_id, limit=request.limit)
        result = []
        for head in heads:
            if SUBTASK_SESSION_MARKER in head.session_id:
                # Delegated child runs live under derived sessions — they are
                # run internals, not user sessions; keep them off pickers.
                continue
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
            workspace_id=getattr(head, "workspace_id", ""),
            latest_updated_at="" if latest_run is None else latest_run.updated_at,
        )

    def _provision_delegation(
        self,
        *,
        tenant_id: str,
        session_id: str,
        source_run_id: str,
        parent_task_id: str,
        request_key: str,
        request_text: str,
        target_planet: str,
        profile: Any | None,
        resolved_refs: tuple[str, ...] | list[str],
        workspace_root: str,
    ) -> dict[str, Any]:
        """One durable provisioning for every delegation entry — child
        Task, Handoff, participant, and the config the child Run must
        receive (agent_profile metadata, inherited workspace, resolved
        context refs).  Ids derive from request_key, so a retried request
        finds the same delegation fact instead of a second Handoff."""
        import hashlib
        import json as _json
        import uuid

        task_id = (
            "task-delegate-"
            + hashlib.sha256(request_key.encode()).hexdigest()[:12]
            if request_key
            else f"task-{uuid.uuid4().hex[:12]}"
        )
        child_task = self._store.create_task_head(
            tenant_id,
            session_id,
            task_id,
            parent_task_id=parent_task_id,
            origin_planet=target_planet,
            owner_profile_id=profile.profile_id if profile else "",
        )
        child_text = request_text
        if resolved_refs:
            lines = "\n".join(f"- {r}" for r in resolved_refs)
            child_text = f"{request_text}\n\n上下文引用（已验证可用）:\n{lines}"
        if profile is not None:
            label = profile.display_name or profile.profile_id
            planet = (
                f" @{profile.planet_affinity}"
                if profile.planet_affinity
                else ""
            )
            child_text = f"[Agent: {label}{planet}]\n{child_text}"
        self._store.add_participant(
            tenant_id, session_id, child_task.task_id, role="delegate"
        )
        handoff = self._store.create_handoff(
            tenant_id,
            session_id,
            f"handoff:{child_task.task_id}",
            source_run_id=source_run_id,
            source_task_id=parent_task_id,
            target_task_id=child_task.task_id,
            target_planet=target_planet,
            context_refs_json=_json.dumps(tuple(resolved_refs)),
            approval_state="not_required",
        )
        return {
            "task_id": child_task.task_id,
            "handoff_id": handoff.handoff_id,
            "session_id": session_id,
            "target_planet": target_planet,
            "workspace_root": workspace_root,
            "agent_profile": (
                {
                    "profile_id": profile.profile_id,
                    "model_ref": profile.model_ref,
                    "skill_refs": _json.loads(profile.skill_refs_json or "[]"),
                    "permission_policy": _json.loads(
                        profile.permission_policy_json or "{}"
                    ),
                }
                if profile is not None
                else {}
            ),
            "request_text": child_text,
        }

    async def spawn_subtask(
        self,
        run_context: Any,
        *,
        request_text: str,
        target_planet: str = "",
        profile_id: str = "",
        context_refs: tuple[str, ...] | list[str] = (),
        request_key: str = "",
    ) -> str:
        """Delegate a subtask: child Task -> child Run -> result back to
        the parent's tool result.  The parent run's loop blocks on this
        call; the delegation tree and handoff event are durable."""
        import asyncio

        depth = int(getattr(run_context, "delegation_depth", 0) or 0)
        if depth >= 1:
            raise AgentServiceError(
                ServiceErrorCode.INVALID_REQUEST,
                "delegated runs cannot delegate further",
            )
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
        # The derived child session has no workspace binding — inherit the
        # parent's resolved root so file refs and file ops stay in scope.
        parent_workspace = getattr(run_context, "workspace", None)
        parent_root = str(parent_workspace.root) if parent_workspace else ""
        provisioned = self._provision_delegation(
            tenant_id=tenant_id,
            session_id=session_id,
            source_run_id=run_context.run_id,
            parent_task_id=parent_head.task_id if parent_head else "",
            request_key=request_key,
            request_text=request_text,
            target_planet=target_planet,
            profile=profile,
            resolved_refs=resolved_refs,
            workspace_root=parent_root,
        )
        child_session_id = (
            f"{session_id}{SUBTASK_SESSION_MARKER}{provisioned['task_id']}"
        )
        handle = await self.start_run(
            StartRunRequest(
                tenant_id=tenant_id,
                user_id=run_context.user_id,
                session_id=child_session_id,
                request_id=f"subtask:{provisioned['task_id']}",
                request_text=provisioned["request_text"],
                task_id=provisioned["task_id"],
                workspace_root=provisioned["workspace_root"],
                context_refs=tuple(resolved_refs),
                metadata={
                    "delegation_depth": depth + 1,
                    "agent_profile": provisioned["agent_profile"],
                },
            )
        )
        self._store.update_handoff_target_run(
            tenant_id, provisioned["handoff_id"], handle.run_id
        )
        view.store.append_event(
            view.tenant_id,
            view.session_id,
            view.run_id,
            event_id=f"task-delegated:{view.run_id}:{provisioned['task_id']}",
            event_type="task.delegated",
            timestamp=self._timestamp(),
            payload={
                "child_task_id": provisioned["task_id"],
                "child_run_id": handle.run_id,
                "child_session_id": child_session_id,
                "parent_task_id": (
                    parent_head.task_id if parent_head else ""
                ),
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
                    request_id=f"subtask-read:{provisioned['task_id']}",
                )
            )
        except Exception as error:
            view.store.append_event(
            view.tenant_id,
            view.session_id,
            view.run_id,
            event_id=f"task-result:{view.run_id}:{provisioned['task_id']}",
            event_type="task.result",
            timestamp=self._timestamp(),
            payload={
                    "child_task_id": provisioned["task_id"],
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
            event_id=f"task-result:{view.run_id}:{provisioned['task_id']}",
            event_type="task.result",
            timestamp=self._timestamp(),
            payload={
                "child_task_id": provisioned["task_id"],
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
            provider_mode=router.provider_mode,
            active_provider=name,
            active_model=(
                router.deepseek_model if name == "deepseek"
                else router.ollama_model if name == "ollama" else ""
            ),
            deepseek_model=router.deepseek_model,
            ollama_model=router.ollama_model,
        )

    async def update_settings(self, request: Any) -> Any:
        """Live provider/model changes — same in-process semantics as the
        CLI /model command; next request picks them up."""
        self._ensure_open()
        from .. import llm as _llm_module

        router = _llm_module.llm
        router.configure(
            provider_mode=request.provider_mode,
            deepseek_model=request.deepseek_model,
            ollama_model=request.ollama_model,
        )
        return await self.get_settings(request)

    async def upsert_workspace(self, request: Any) -> Any:
        self._ensure_open()
        import json as _json

        # Source folders: `paths` is the full list, `path` is the primary one
        # that scopes run file ops. A request that names only `path` is a
        # single-source workspace.
        paths = list(getattr(request, "paths", ()) or ())
        if not paths and getattr(request, "path", ""):
            paths = [request.path]
        path = request.path if request.path in paths else (paths[0] if paths else "")

        record = self._store.upsert_workspace(
            request.tenant_id,
            request.workspace_id,
            display_name=request.display_name,
            path=path,
            paths_json=_json.dumps(paths),
            capabilities_json=_json.dumps(list(request.capabilities)),
            default_profile_id=request.default_profile_id,
            description=request.description,
        )
        return self._workspace_summary(record)

    async def list_workspaces(self, request: Any) -> list[Any]:
        self._ensure_open()
        return [
            self._workspace_summary(
                r, len(self._store.workspace_member_ids(request.tenant_id, r.workspace_id)),
            )
            for r in self._store.list_workspaces(
                request.tenant_id, limit=request.limit
            )
        ]

    @staticmethod
    def _workspace_summary(record: Any, member_count: int = 0) -> Any:
        import json as _json
        from .contracts import WorkspaceSummary

        return WorkspaceSummary(
            workspace_id=record.workspace_id,
            display_name=record.display_name,
            path=getattr(record, "path", ""),
            paths=tuple(
                str(v) for v in _json.loads(getattr(record, "paths_json", "") or "[]")
            ),
            member_count=member_count,
            capabilities=tuple(
                str(v) for v in _json.loads(record.capabilities_json or "[]")
            ),
            default_profile_id=record.default_profile_id,
            description=record.description,
            updated_at=record.updated_at,
        )

    async def search_artifacts(self, request: Any) -> list[Any]:
        """Jupiter deep index — artifact lookup across sessions."""
        self._ensure_open()
        rows = self._store.search_artifacts(
            request.tenant_id,
            request.query,
            session_id=(
                None if getattr(request, "all_sessions", False)
                else request.session_id
            ),
            limit=getattr(request, "limit", 200) or 200,
        )
        from .contracts import JupiterArtifactSummary

        return [
            JupiterArtifactSummary(
                session_id=r["session_id"],
                run_id=r["run_id"],
                artifact_id=r["artifact_id"],
                artifact_type=r["artifact_type"],
                reference=r["reference"],
                digest=r["digest"],
                exists=r["exists"],
                verified=r["verified"],
                producer_task_id=r["producer_task_id"],
                updated_at=r["updated_at"],
            )
            for r in rows
        ]

    @staticmethod
    def _scoped_workspace(root: Any) -> Any:
        from agent.services.workspace_service import WorkspaceService

        return WorkspaceService.scoped(root)

    def _workspace_service(self, request: Any) -> tuple[Any, Any]:
        """Scoped filesystem view + the workspace root it is bound to."""
        root = self._contexts.session_workspace(
            request.tenant_id, request.session_id
        )
        if root is None:
            raise AgentServiceError(
                ServiceErrorCode.INVALID_REQUEST,
                "service has no workspace root",
            )
        return self._scoped_workspace(root), Path(root).resolve()

    async def list_workspace_files(self, request: Any) -> list[Any]:
        """Mars forge: enumerate workspace files (names only)."""
        self._ensure_open()
        import asyncio

        workspace, root = self._workspace_service(request)
        from .contracts import WorkspaceFileSummary

        entries: list[Any] = []
        files = await asyncio.to_thread(
            lambda: sorted(
                p for p in root.rglob("*")
                if p.is_file()
                and ".tsagent" not in p.parts
                and ".attachments" not in p.parts
            )
        )
        for path in files[: (getattr(request, "limit", 500) or 500)]:
            entries.append(
                WorkspaceFileSummary(
                    path=workspace.relative_path(path),
                    size=path.stat().st_size,
                    modified_at=str(path.stat().st_mtime_ns),
                )
            )
        return entries

    async def read_workspace_file(self, request: Any) -> Any:
        """Mars forge editor: read one workspace file (guarded + redacted)."""
        self._ensure_open()
        import asyncio

        workspace, root = self._workspace_service(request)
        text, digest, redacted = await asyncio.to_thread(
            workspace.read_document, request.path
        )
        from .contracts import WorkspaceFileContent

        return WorkspaceFileContent(
            path=workspace.relative_path(request.path),
            text=text,
            digest=digest,
            redacted=redacted,
            workspace_root=str(root),
        )

    async def write_workspace_file(self, request: Any) -> Any:
        """Mars forge editor: write one workspace file — guarded path rules."""
        self._ensure_open()
        import asyncio

        workspace, root = self._workspace_service(request)
        # 文档必须仍绑定在其读取时的工作区 — 同会话改绑后不得把
        # 旧草稿写进新目录。
        if request.workspace_root and request.workspace_root != str(root):
            raise AgentServiceError(
                ServiceErrorCode.WORKSPACE_CHANGED,
                "document is not bound to the active workspace",
            )
        try:
            message, digest = await asyncio.to_thread(
                workspace.write_document,
                request.path,
                request.content,
                base_digest=str(request.base_digest or ""),
            )
        except PermissionError as exc:
            raise AgentServiceError(
                ServiceErrorCode.READ_ONLY_DOCUMENT, str(exc)
            ) from exc
        except ValueError as exc:
            if str(exc).startswith("STALE_DOCUMENT"):
                raise AgentServiceError(
                    ServiceErrorCode.STALE_DOCUMENT, str(exc)
                ) from exc
            raise
        from .contracts import WorkspaceFileContent

        return WorkspaceFileContent(
            path=workspace.relative_path(request.path),
            text=message,
            digest=digest,
            redacted=False,
        )

    async def record_evaluation(self, request: Any) -> Any:
        self._ensure_open()
        import uuid

        # 人工实验裁决入口 — Saturn 的执行裁决经内部路径直接写库；
        # 此处只接受 Earth 的人工裁决词汇表。
        from agent.runtime_store.sqlite import HUMAN_EVALUATION_VERDICTS

        if request.verdict not in HUMAN_EVALUATION_VERDICTS:
            raise AgentServiceError(
                ServiceErrorCode.INVALID_REQUEST,
                "verdict must be one of "
                + ", ".join(HUMAN_EVALUATION_VERDICTS),
                details={"verdict": request.verdict},
            )
        record = self._store.record_evaluation(
            request.tenant_id,
            request.session_id,
            request.eval_id or f"eval-{uuid.uuid4().hex[:12]}",
            name=request.name,
            run_id=request.run_id,
            baseline_ref=request.baseline_ref,
            candidate_ref=request.candidate_ref,
            score=request.score,
            verdict=request.verdict,
            notes=request.notes,
        )
        return self._evaluation_summary(record)

    async def list_evaluations(self, request: Any) -> list[Any]:
        self._ensure_open()
        records = self._store.list_evaluations(
            request.tenant_id,
            session_id=(
                None if getattr(request, "all_sessions", False)
                else request.session_id
            ),
            limit=getattr(request, "limit", 200) or 200,
        )
        return [self._evaluation_summary(r) for r in records]

    @staticmethod
    def _evaluation_summary(record: Any) -> Any:
        from .contracts import EvaluationSummary

        return EvaluationSummary(
            eval_id=record.eval_id, name=record.name, run_id=record.run_id,
            baseline_ref=record.baseline_ref,
            candidate_ref=record.candidate_ref, score=record.score,
            verdict=record.verdict, notes=record.notes,
            created_at=record.created_at,
        )

    async def create_motif(self, request: Any) -> Any:
        self._ensure_open()
        import uuid

        record = self._store.upsert_motif(
            request.tenant_id,
            request.motif_id or f"motif-{uuid.uuid4().hex[:10]}",
            text=request.text.strip(),
            source=request.source,
        )
        return self._motif_summary(record)

    async def assign_motif(self, request: Any) -> Any:
        """Delegate a motif to Pluto — the delegation key derives from the
        motif identity, so a repeated assign converges on the same child
        Task and Handoff instead of provisioning duplicates."""
        self._ensure_open()
        motif = self._store.get_motif(request.tenant_id, request.motif_id)
        if motif is None:
            raise AgentServiceError(
                ServiceErrorCode.INVALID_REQUEST,
                f"motif not found: {request.motif_id}",
            )
        if motif.status == "resolved":
            raise AgentServiceError(
                ServiceErrorCode.INVALID_REQUEST,
                f"motif already resolved: {request.motif_id}",
            )
        from .contracts import DelegateRunRequest

        delegation = await self.delegate_run(
            DelegateRunRequest(
                tenant_id=request.tenant_id,
                user_id=request.user_id,
                session_id=request.session_id,
                request_id=f"motif-assign:{request.motif_id}",
                parent_run_id=request.parent_run_id,
                request_text=(
                    f"[母题探索] {motif.text}\n"
                    "自由发挥——围绕这个母题去探索，真正搞明白后回报结论。"
                ),
                target_planet="pluto",
            )
        )
        if motif.status == "exploring":
            if motif.assigned_task_id != delegation["task_id"]:
                raise AgentServiceError(
                    ServiceErrorCode.INVALID_REQUEST,
                    f"motif already assigned to another task: {request.motif_id}",
                )
            return delegation
        self._store.update_motif(
            request.tenant_id,
            request.motif_id,
            status="exploring",
            assigned_task_id=delegation["task_id"],
        )
        return delegation

    def _require_motif(self, tenant_id: str, motif_id: str) -> Any:
        motif = self._store.get_motif(tenant_id, motif_id)
        if motif is None:
            raise AgentServiceError(
                ServiceErrorCode.INVALID_REQUEST,
                f"motif not found: {motif_id}",
            )
        return motif

    def _require_motif_run(self, tenant_id: str, motif: Any, run_id: str) -> None:
        """The reporting run must be a Run of the motif's assigned task —
        checked by identity, never by a paged list."""
        head = self._store.get_run_head(tenant_id, run_id)
        if head is None or head.task_id != motif.assigned_task_id:
            raise AgentServiceError(
                ServiceErrorCode.INVALID_REQUEST,
                f"run {run_id} is not assigned to motif {motif.motif_id}",
            )

    async def report_motif(self, request: Any) -> Any:
        """A delegated run reports its understanding — run-bound evidence,
        awaiting the human verdict.  Not a resolution yet."""
        self._ensure_open()
        motif = self._require_motif(request.tenant_id, request.motif_id)
        if motif.status != "exploring":
            raise AgentServiceError(
                ServiceErrorCode.INVALID_REQUEST,
                f"motif is not awaiting a report: {request.motif_id}",
            )
        self._require_motif_run(
            request.tenant_id, motif, request.resolution_run_id
        )
        self._store.update_motif(
            request.tenant_id,
            request.motif_id,
            status="reported",
            resolution_run_id=request.resolution_run_id,
            resolution=request.resolution.strip(),
        )
        return self._motif_summaries(request.tenant_id)

    async def reject_motif(self, request: Any) -> Any:
        """Human verdict rejecting the report — the send-back is durable:
        the motif returns to exploring and the rejected report is cleared."""
        self._ensure_open()
        motif = self._require_motif(request.tenant_id, request.motif_id)
        if motif.status != "reported":
            raise AgentServiceError(
                ServiceErrorCode.INVALID_REQUEST,
                f"motif has no report to reject: {request.motif_id}",
            )
        self._store.update_motif(
            request.tenant_id,
            request.motif_id,
            status="exploring",
            resolution_run_id="",
            resolution="",
        )
        return self._motif_summaries(request.tenant_id)

    async def resolve_motif(self, request: Any) -> Any:
        """Motif resolved — the motif and the run reporting it are verified
        before anything is written, and the motif is marked resolved only
        after the memory commit returns committed evidence."""
        self._ensure_open()
        motif = self._require_motif(request.tenant_id, request.motif_id)
        if motif.status == "resolved":
            if motif.resolution_run_id != request.resolution_run_id:
                raise AgentServiceError(
                    ServiceErrorCode.INVALID_REQUEST,
                    f"motif already resolved by run {motif.resolution_run_id}: {motif.motif_id}",
                )
            return self._motif_summaries(request.tenant_id)
        if motif.status != "reported":
            raise AgentServiceError(
                ServiceErrorCode.INVALID_REQUEST,
                f"motif has no report awaiting verdict: {motif.motif_id}",
            )
        if motif.resolution_run_id != request.resolution_run_id:
            raise AgentServiceError(
                ServiceErrorCode.INVALID_REQUEST,
                f"run {request.resolution_run_id} did not report motif {motif.motif_id}",
            )
        from agent.memory import MemoryPersistenceBoundary
        from agent.memory.learning import (
            MemoryLearningDecision,
            MemoryPolicyProjection,
        )

        decision = MemoryLearningDecision(
            action="STORE",
            memory_type="fact",
            scope="user",
            canonical_key=f"motif.{request.motif_id}",
            value=request.resolution.strip(),
            provenance={
                # 同一裁决的稳定提交身份 — 记忆已写、母题更新失败后，
                # 任意 request_id 的重试都在同一证据上收敛。
                "evidence_id": (
                    f"motif-resolution:{request.motif_id}:{request.resolution_run_id}"
                ),
                "source_kind": "motif",
                "source_ref": request.motif_id,
            },
            reason_code="motif_resolution",
        )
        policy = MemoryPolicyProjection(
            scope="user",
            namespace=f"{request.tenant_id}:{request.user_id}",
            authorized_source_kinds=frozenset({"motif"}),
        )
        evidence = MemoryPersistenceBoundary.commit(decision, policy)
        if not evidence.committed:
            raise AgentServiceError(
                ServiceErrorCode.INTERNAL_ERROR,
                "motif resolution was not committed to memory: "
                + (evidence.error or evidence.reason_code or "commit rejected"),
                retryable=True,
            )
        self._store.update_motif(
            request.tenant_id,
            request.motif_id,
            status="resolved",
            resolution_run_id=request.resolution_run_id,
            resolution=request.resolution.strip(),
        )
        return self._motif_summaries(request.tenant_id)

    def _motif_summary(self, record: Any) -> Any:
        from .contracts import MotifSummary

        # The run head is the authority on which session owns the evidence
        # run — list_motifs is tenant-scoped, so the UI cannot infer it.
        resolution_session_id = ""
        if record.resolution_run_id:
            head = self._store.get_run_head(
                record.tenant_id, record.resolution_run_id
            )
            resolution_session_id = head.session_id if head else ""
        return MotifSummary(
            motif_id=record.motif_id,
            text=record.text,
            source=record.source,
            status=record.status,
            assigned_task_id=record.assigned_task_id,
            resolution_run_id=record.resolution_run_id,
            resolution_session_id=resolution_session_id,
            resolution=record.resolution,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    def _motif_summaries(self, tenant_id: str) -> list[Any]:
        return [self._motif_summary(r) for r in self._store.list_motifs(tenant_id)]

    async def list_motifs(self, request: Any) -> list[Any]:
        self._ensure_open()
        records = self._store.list_motifs(
            request.tenant_id,
            status=request.status or None,
            limit=request.limit or 100,
        )
        return [self._motif_summary(r) for r in records]

    async def upsert_workflow_graph(self, request: Any) -> Any:
        self._ensure_open()
        import json as _json
        from .contracts import _thaw_json

        record = self._store.upsert_workflow_graph(
            request.tenant_id,
            request.graph_id,
            name=request.name,
            definition_json=_json.dumps(
                _thaw_json(dict(request.definition or {})), ensure_ascii=False
            ),
            owner=request.owner or request.user_id,
        )
        return self._graph_summary(record)

    async def run_graph(self, request: Any) -> list[Any]:
        """Execute a persisted canvas graph — task nodes are delegated
        child runs and edges are success dependencies: a node starts
        only after every upstream node completed.  input nodes provide
        {name} substitutions into request_text.  The whole graph is
        validated before the first Run — unknown node types, malformed
        edges or missing profiles are rejected with INVALID_REQUEST
        before any Task/Handoff/Run is created."""
        self._ensure_open()
        import json as _json
        from collections.abc import Mapping

        record = self._store.get_workflow_graph(request.tenant_id, request.graph_id)
        if record is None:
            raise AgentServiceError(
                ServiceErrorCode.RUN_NOT_FOUND,
                f"workflow graph not found: {request.graph_id}",
            )
        definition = _json.loads(record.definition_json)
        raw_nodes = definition.get("nodes", [])
        edges = definition.get("edges", [])
        # 整图预检：未知节点类型 / 缺 request_text / 非法边 / 未知 profile
        # 一律在产生任何 Task/Handoff/Run 之前明确拒绝。
        node_ids: set[str] = set()
        for node in raw_nodes:
            node_id = str(node.get("id", ""))
            node_type = str(node.get("type", ""))
            if node_type not in {"task", "input"}:
                raise AgentServiceError(
                    ServiceErrorCode.INVALID_REQUEST,
                    f"unknown node type '{node_type}' in node {node_id}",
                )
            if node_type == "task" and not str(
                node.get("request_text", "")
            ).strip():
                raise AgentServiceError(
                    ServiceErrorCode.INVALID_REQUEST,
                    f"task node has no request_text: {node_id}",
                )
            node_ids.add(node_id)
        for edge in edges:
            if not isinstance(edge, Mapping):
                raise AgentServiceError(
                    ServiceErrorCode.INVALID_REQUEST,
                    "graph edges must be {from, to} objects",
                )
            for key in ("from", "to"):
                if str(edge.get(key, "")) not in node_ids:
                    raise AgentServiceError(
                        ServiceErrorCode.INVALID_REQUEST,
                        f"edge references unknown node: {edge.get(key)}",
                    )
        known_profiles = {
            profile.profile_id
            for profile in self._store.list_profiles(request.tenant_id)
        }
        task_nodes = [n for n in raw_nodes if n.get("type") == "task"]
        if not task_nodes:
            raise AgentServiceError(
                ServiceErrorCode.INVALID_REQUEST,
                "workflow graph has no executable task nodes",
            )
        for node in task_nodes:
            profile_id = str(node.get("profile_id", ""))
            if profile_id and profile_id not in known_profiles:
                raise AgentServiceError(
                    ServiceErrorCode.INVALID_REQUEST,
                    f"agent profile not found: {profile_id}",
                )
        nodes = {str(n.get("id")): n for n in raw_nodes}
        inputs = {
            str(n.get("name")): str(n.get("value", ""))
            for n in nodes.values() if n.get("type") == "input"
        }
        order = _topo_task_order(task_nodes, edges)
        from .contracts import GraphRunStepSummary

        # 一次图执行的持久身份 — 节点→子 Run 的关联无法从 handoff 重建
        # （SKIPPED/PENDING 不产生 handoff），故在执行中逐节点落事实行。
        execution_id = f"gexec:{request.graph_id}:{request.request_id}"
        for position, node in enumerate(order):
            self._store.upsert_graph_execution_node(
                request.tenant_id,
                execution_id,
                str(node["id"]),
                graph_id=request.graph_id,
                session_id=request.session_id,
                position=position,
                status="PENDING",
            )

        # 图执行在 Service 管理的后台任务里推进 — run_graph 在接纳
        # （整图预检 + 计划）后返回，不持有 JSONL 控制流等待子 Run。
        import asyncio

        task = asyncio.create_task(
            self._graph_execute(request, execution_id, order, edges, inputs),
            name=f"graph:{request.graph_id}:{request.request_id}",
        )
        self._graph_tasks.add(task)
        task.add_done_callback(self._graph_tasks.discard)
        return [
            GraphRunStepSummary(
                node_id=str(node["id"]),
                task_id="",
                run_id="",
                session_id="",
                status="PENDING",
                execution_id=execution_id,
            )
            for node in order
        ]

    async def get_graph_execution(self, request: Any) -> Any:
        """Re-query one execution's durable node facts. Nodes bound to a
        Run project their status from the authoritative run head — the
        stored row is a driver checkpoint and goes stale the moment the
        driver exits without writing the terminal fact. Nodes with no Run
        (PENDING/ADMITTED/SKIPPED) read their stored status; a lost driver
        leaves them INTERRUPTED via exit marking or restart convergence."""
        self._ensure_open()
        from .contracts import GraphExecutionSummary, GraphRunStepSummary

        nodes = self._store.list_graph_execution_nodes(
            request.tenant_id, request.execution_id
        )
        steps: list[Any] = []
        for n in nodes:
            status = n.status
            if n.run_id:
                head = self._store.get_run_head(
                    request.tenant_id,
                    n.run_id,
                    session_id=n.child_session_id or None,
                )
                if head is not None:
                    status = head.run_status
            steps.append(
                GraphRunStepSummary(
                    node_id=n.node_id,
                    task_id=n.task_id,
                    run_id=n.run_id,
                    session_id=n.child_session_id,
                    status=status,
                    execution_id=n.execution_id,
                    detail=n.detail,
                )
            )
        return GraphExecutionSummary(
            execution_id=request.execution_id,
            graph_id=nodes[0].graph_id if nodes else "",
            session_id=nodes[0].session_id if nodes else "",
            steps=tuple(steps),
        )

    _GRAPH_NODE_OPEN = frozenset({"PENDING", "ADMITTED", "RUNNING"})

    def _graph_mark_unfinished(
        self,
        tenant_id: str,
        execution_id: str,
        *,
        status: str,
        detail: str,
    ) -> None:
        """The in-process driver is gone; nodes it never advanced can
        never move again — record that explicitly instead of leaving a
        permanent PENDING. Existing task/run/session ids are preserved."""
        for n in self._store.list_graph_execution_nodes(tenant_id, execution_id):
            if n.status not in self._GRAPH_NODE_OPEN:
                continue
            self._store.upsert_graph_execution_node(
                tenant_id,
                execution_id,
                n.node_id,
                graph_id=n.graph_id,
                session_id=n.session_id,
                position=n.position,
                task_id=n.task_id,
                run_id=n.run_id,
                child_session_id=n.child_session_id,
                status=status,
                detail=detail,
            )

    async def list_graph_executions(self, request: Any) -> list[Any]:
        """Distinct executions for a session — the workspace re-queries
        each by execution_id for live node state."""
        self._ensure_open()
        records = self._store.list_graph_executions(
            request.tenant_id, request.session_id
        )
        return [
            {
                "execution_id": execution_id,
                "graph_id": graph_id,
                "updated_at": updated_at,
            }
            for execution_id, graph_id, updated_at in records
        ]

    async def _graph_execute(
        self,
        request: Any,
        execution_id: str,
        order: list,
        edges: list,
        inputs: dict[str, str],
    ) -> None:
        """Advance the graph chain in the background: provision + start
        each node, wait for its terminal head, then gate dependents on
        their own predecessors — an independent branch still runs when
        a sibling fails."""
        import asyncio
        import logging

        from .contracts import (
            DelegateRunRequest,
            RunLookupRequest,
            StartRunRequest,
        )

        order_index = {
            str(node["id"]): i for i, node in enumerate(order)
        }
        deps: dict[str, set[str]] = {}
        for edge in edges:
            deps.setdefault(str(edge["to"]), set()).add(str(edge["from"]))
        results: dict[str, str] = {}
        run_by_node: dict[str, str] = {}
        session_by_node: dict[str, str] = {}
        try:
            for node in order:
                node_id = str(node["id"])
                if self._closed:
                    self._graph_mark_unfinished(
                        request.tenant_id,
                        execution_id,
                        status="INTERRUPTED",
                        detail="service closed",
                    )
                    return
                preds = deps.get(node_id, set())
                if any(
                    results.get(pred) != "COMPLETED" for pred in preds
                ):
                    # 成功依赖：前任未完成的节点不被接纳 — 不产生
                    # Task/Handoff/Run；无依赖的兄弟分支继续。
                    results[node_id] = "SKIPPED"
                    self._store.upsert_graph_execution_node(
                        request.tenant_id,
                        execution_id,
                        node_id,
                        graph_id=request.graph_id,
                        session_id=request.session_id,
                        status="SKIPPED",
                        detail="predecessor not COMPLETED",
                    )
                    continue
                request_text = str(node.get("request_text", "")).strip()
                for key, value in inputs.items():
                    request_text = request_text.replace(
                        "{" + key + "}", value
                    )
                parent_run_id = request.parent_run_id
                delegate_session = request.session_id
                if preds:
                    # 依赖节点的委派归属在拓扑上最近完成的前任之下。
                    pred = max(preds, key=lambda p: order_index[p])
                    parent_run_id = run_by_node[pred]
                    delegate_session = session_by_node[pred]
                delegation = await self.delegate_run(
                    DelegateRunRequest(
                        tenant_id=request.tenant_id,
                        user_id=request.user_id,
                        session_id=delegate_session,
                        request_id=f"{request.request_id}-{node_id}",
                        parent_run_id=parent_run_id,
                        request_text=request_text,
                        target_planet=str(node.get("target_planet", "")),
                        profile_id=str(node.get("profile_id", "")),
                    )
                )
                child_session = (
                    f"{delegate_session}{SUBTASK_SESSION_MARKER}"
                    f"{delegation['task_id']}"
                )
                # 节点→子 Run 关联先落事实行，再启动 — 后台状态查询在
                # Run 存活期间就能看到真实身份。
                self._store.upsert_graph_execution_node(
                    request.tenant_id,
                    execution_id,
                    node_id,
                    graph_id=request.graph_id,
                    session_id=request.session_id,
                    task_id=str(delegation["task_id"]),
                    run_id="",
                    child_session_id=child_session,
                    status="ADMITTED",
                )
                started = await self.start_run(
                    StartRunRequest(
                        tenant_id=request.tenant_id,
                        user_id=request.user_id,
                        session_id=child_session,
                        request_id=f"{request.request_id}-{node_id}-run",
                        request_text=delegation["request_text"],
                        task_id=delegation["task_id"],
                        workspace_root=delegation["workspace_root"],
                        metadata={
                            "delegation_depth": 1,
                            "agent_profile": delegation["agent_profile"],
                        },
                    )
                )
                self._store.update_handoff_target_run(
                    request.tenant_id,
                    delegation["handoff_id"],
                    started.run_id,
                )
                self._store.upsert_graph_execution_node(
                    request.tenant_id,
                    execution_id,
                    node_id,
                    graph_id=request.graph_id,
                    session_id=request.session_id,
                    task_id=str(delegation["task_id"]),
                    run_id=started.run_id,
                    child_session_id=child_session,
                    status="RUNNING",
                )
                managed = self._runs.get(started.run_id)
                if managed is not None and managed.task is not None:
                    try:
                        await managed.task
                    except Exception:
                        pass  # terminal state is read from the durable head
                snapshot = await self.get_run(
                    RunLookupRequest(
                        tenant_id=request.tenant_id,
                        user_id=request.user_id,
                        session_id=child_session,
                        run_id=started.run_id,
                        request_id=(
                            f"{request.request_id}-{node_id}-status"
                        ),
                    )
                )
                results[node_id] = snapshot.status.value
                run_by_node[node_id] = started.run_id
                session_by_node[node_id] = child_session
                self._store.upsert_graph_execution_node(
                    request.tenant_id,
                    execution_id,
                    node_id,
                    graph_id=request.graph_id,
                    session_id=request.session_id,
                    task_id=str(delegation["task_id"]),
                    run_id=started.run_id,
                    child_session_id=child_session,
                    status=snapshot.status.value,
                )
        except asyncio.CancelledError:
            self._graph_mark_unfinished(
                request.tenant_id,
                execution_id,
                status="INTERRUPTED",
                detail="graph driver cancelled",
            )
            raise
        except Exception:
            logging.getLogger(__name__).exception(
                "graph execution failed: %s", request.graph_id
            )
            self._graph_mark_unfinished(
                request.tenant_id,
                execution_id,
                status="FAILED",
                detail="graph driver raised",
            )

    async def list_workflow_graphs(self, request: Any) -> list[Any]:
        self._ensure_open()
        records = self._store.list_workflow_graphs(
            request.tenant_id,
            limit=getattr(request, "limit", 200) or 200,
        )
        return [self._graph_summary(r) for r in records]

    @staticmethod
    def _graph_summary(record: Any) -> Any:
        from .contracts import WorkflowGraphSummary

        return WorkflowGraphSummary(
            graph_id=record.graph_id,
            name=record.name,
            definition_json=record.definition_json,
            owner=record.owner,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    async def list_participants(self, request: Any) -> list[Any]:
        self._ensure_open()
        records = self._store.list_participants(
            request.tenant_id, request.session_id
        )
        from .contracts import ParticipantSummary

        return [
            ParticipantSummary(
                participant_id=r.participant_id,
                role=r.role,
                joined_at=r.joined_at,
            )
            for r in records
        ]

    async def list_facts(self, request: Any) -> list[Any]:
        """Memory governance read — facts with provenance."""
        self._ensure_open()
        import asyncio

        from ..memory.long_term import list_fact_records

        records = await asyncio.to_thread(
            list_fact_records,
            f"{request.tenant_id}:{request.user_id}",
            scope=getattr(request, "scope", None) or None,
            limit=getattr(request, "limit", 500) or 500,
        )
        from .contracts import FactRecordSummary

        return [FactRecordSummary(**r) for r in records]

    async def forget_fact(self, request: Any) -> Any:
        """Memory governance delete — the user removes one remembered fact."""
        self._ensure_open()
        import asyncio

        from ..memory.long_term import forget_fact

        removed = await asyncio.to_thread(
            forget_fact,
            f"{request.tenant_id}:{request.user_id}",
            request.category,
            request.key,
            scope=getattr(request, "scope", None) or None,
        )
        from .contracts import FactForgetResult

        return FactForgetResult(
            category=request.category, key=request.key, removed=bool(removed)
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

    def _validate_store_refs(
        self,
        tenant_id: str,
        session_id: str,
        run_id: str,
        refs: Any,
        *,
        root: Path | None = None,
        allow_artifact: bool = True,
    ) -> list[str]:
        """Store-level context-ref validation — same schemes as the loop
        path, no live run_context needed.  ``root`` is the caller-resolved
        workspace (defaults to the session resolution); ``allow_artifact``
        gates the artifact scheme out of start_run, where no producing run
        can exist yet."""
        resolved: list[str] = []
        for ref in refs or ():
            raw = str(ref)
            kind, _, target = raw.partition(":")
            if kind == "file" and target:
                root = root or self._contexts.session_workspace(
                    tenant_id, session_id
                )
                if root is None:
                    raise AgentServiceError(
                        ServiceErrorCode.INVALID_REQUEST,
                        "context ref file: requires a workspace",
                    )
                import asyncio  # noqa: F401 — sync resolve
                resolved_path = root / target
                if not resolved_path.exists():
                    raise AgentServiceError(
                        ServiceErrorCode.INVALID_REQUEST,
                        f"context ref not readable in workspace: {raw}",
                    )
                resolved.append(f"file:{target}")
            elif kind == "task" and target:
                if self._store.get_task_head(tenant_id, target) is None:
                    raise AgentServiceError(
                        ServiceErrorCode.INVALID_REQUEST,
                        f"context ref task not found: {raw}",
                    )
                resolved.append(f"task:{target}")
            elif kind == "run" and target:
                # Tenant-wide like task: — a ref forwarded to a derived
                # subtask session still points at a durable parent run.
                if self._store.get_run_head(
                    tenant_id, target, session_id=None
                ) is None:
                    raise AgentServiceError(
                        ServiceErrorCode.INVALID_REQUEST,
                        f"context ref run not found in session: {raw}",
                    )
                resolved.append(f"run:{target}")
            elif kind == "artifact" and target and allow_artifact:
                snapshot = self._store.read_run_snapshot(
                    tenant_id, run_id, session_id=session_id
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

    # Sun routing aliases: capability names (English, registry-declared)
    # expand to the request phrasings that should match them.  The alias
    # map is the routing knowledge, the match mechanism stays stringy.
    _CAPABILITY_ALIASES = {
        "research": ("调研", "研究", "搜索", "查", "资料", "新闻", "news", "search", "web"),
        "browser": ("浏览器", "网页", "web", "click", "click"),
        "code": ("代码", "编程", "实现", "写", "修", "bug", "debug", "程序", "code", "coding"),
        "filesystem": ("文件", "目录", "file", "读", "写入", "filesystem"),
        "test": ("测试", "单测", "pytest", "验证", "test", "tests"),
        "analysis": ("分析", "统计", "analysis", "analyze", "data"),
        "memory": ("记忆", "记住", "memory", "remember"),
        "schedule": ("定时", "周期", "schedule", "cron", "每天", "每小时"),
    }

    def _route_planet(self, tenant_id: str, request_text: str) -> Any:
        """Sun v1: score request text against each workspace's declared
        capabilities — alias-expanded so natural phrasing matches the
        registry's English capability names.  Highest score wins; no
        match → None (universal executor), never a guess."""
        import json as _json

        workspaces = self._store.list_workspaces(tenant_id)
        text = request_text.lower()
        best = None
        best_score = 0
        for workspace in workspaces:
            caps = [str(c) for c in _json.loads(workspace.capabilities_json or "[]")]
            terms = [str(workspace.workspace_id), str(workspace.display_name)] + caps
            for cap in caps:
                terms += list(self._CAPABILITY_ALIASES.get(cap.lower(), ()))
            score = sum(1 for t in terms if t and t.lower() in text)
            if score > best_score:
                best = workspace
                best_score = score
        return best

    async def delegate_run(self, request: Any) -> Any:
        """UI delegation: child Task + Handoff edge durably provisioned.

        Mirrors spawn_subtask's durable sequence but resolves the parent by
        run_id — the child Run is started separately via start_run so the
        desktop controller keeps its hydration path.
        """
        self._ensure_open()

        from .errors import AgentServiceError, ServiceErrorCode
        from agent.runtime_store.errors import DurableStoreError

        tenant_id, session_id = request.tenant_id, request.session_id
        try:
            parent_head = self._store.get_run_head(
                tenant_id, request.parent_run_id, session_id=session_id
            )
        except DurableStoreError:
            # run exists but belongs to another session — for this session's
            # scope it is simply not found; surface a real error, not 500.
            parent_head = None
        if parent_head is None:
            raise AgentServiceError(
                ServiceErrorCode.RUN_NOT_FOUND,
                f"run was not found: {request.parent_run_id}",
            )
        profile = None
        if request.profile_id:
            profile = self._store.get_profile(tenant_id, request.profile_id)
            if profile is None:
                raise AgentServiceError(
                    ServiceErrorCode.INVALID_REQUEST,
                    f"agent profile not found: {request.profile_id}",
                )
        target_planet = request.target_planet or (profile.planet_affinity if profile else "")
        refs = self._validate_store_refs(
            tenant_id,
            session_id,
            request.parent_run_id,
            request.context_refs,
        )
        # The returned descriptor carries the child's full start config —
        # agent_profile metadata, inherited workspace and the enriched
        # request_text — so the separate start_run matches spawn_subtask's.
        return self._provision_delegation(
            tenant_id=tenant_id,
            session_id=session_id,
            source_run_id=request.parent_run_id,
            parent_task_id=parent_head.task_id if parent_head else "",
            request_key=request.request_id,
            request_text=request.request_text,
            target_planet=target_planet,
            profile=profile,
            resolved_refs=refs,
            workspace_root=str(
                self._contexts.session_workspace(tenant_id, session_id)
                or ""
            ),
        )

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
            auto_eval=getattr(request, "auto_eval", False),
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
            user_id=record.user_id, auto_eval=record.auto_eval,
            target_planet=record.target_planet,
            interval_seconds=record.interval_seconds,
            status=record.status,
            next_fire_at=record.next_fire_at,
            last_fired_at=record.last_fired_at,
            last_run_id=record.last_run_id,
            fire_count=record.fire_count,
            created_at=record.created_at,
        )

    def _maybe_start_scheduler(self) -> None:
        """Start the scheduler under a running loop — construction may
        happen outside one (sync entry points); the first service call
        under a loop performs the same lazy start."""
        import asyncio

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        self._ensure_scheduler()

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
                    import json as _json

                    # target_planet 同时进入执行路由 — 与 planet_affinity
                    # 匹配的 profile 经 metadata.agent_profile 交给 Runtime。
                    profile = (
                        next(
                            (
                                p
                                for p in self._store.list_profiles(
                                    schedule.tenant_id
                                )
                                if p.planet_affinity
                                == schedule.target_planet
                            ),
                            None,
                        )
                        if schedule.target_planet
                        else None
                    )
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
                            origin_planet=schedule.target_planet,
                            metadata={
                                "agent_profile": {
                                    "profile_id": profile.profile_id,
                                    "model_ref": profile.model_ref,
                                    "skill_refs": _json.loads(
                                        profile.skill_refs_json or "[]"
                                    ),
                                    "permission_policy": _json.loads(
                                        profile.permission_policy_json or "{}"
                                    ),
                                }
                                if profile is not None
                                else {},
                            },
                        )
                    )
                    self._store.update_schedule_run(
                        schedule.tenant_id, schedule.schedule_id, handle.run_id
                    )
                    if schedule.auto_eval:
                        asyncio.create_task(
                            self._auto_eval(schedule, handle)
                        )
                except Exception:
                    import logging

                    logging.getLogger(__name__).exception(
                        "schedule fire failed: %s", schedule.schedule_id
                    )

    async def _auto_eval(self, schedule: Any, handle: Any) -> None:
        """Watch a schedule-fired run to terminal state, then persist its
        evaluation record — an experiment log entry per fire."""
        import asyncio

        managed = self._runs.get(handle.run_id)
        if managed is None:
            return
        try:
            await asyncio.wait_for(asyncio.shield(managed.task), timeout=86400)
        except Exception:
            pass
        head = self._store.get_run_head(
            schedule.tenant_id, handle.run_id,
            session_id=managed.run_context.session_id
            if managed.run_context else schedule.session_id,
        )
        status = getattr(head, "run_status", "FAILED_TERMINAL") or "FAILED_TERMINAL"
        self._store.record_evaluation(
            schedule.tenant_id,
            schedule.session_id,
            f"eval:{schedule.schedule_id}:{schedule.fire_count}",
            name=f"schedule:{schedule.schedule_id}",
            run_id=handle.run_id,
            score=1.0 if status == "COMPLETED" else 0.0,
            verdict="pass" if status == "COMPLETED" else "fail",
            notes=f"{schedule.request_text} → {status}",
        )

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
        WAITING_USER, and block until a committed ``decide_approval`` lands.

        Idempotent on approval_id — a replay after restart returns the
        already-decided record instead of re-parking the run.  The waiter
        observes the durable record, so a decision committed by another
        Service sharing this SQLite store also releases the gate.
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
        waiter_key = (view.tenant_id, view.run_id, record.approval_id)
        self._approval_waiters[waiter_key] = waiter
        timestamp = self._timestamp()
        try:
            view.transition_run_with_event(
                run_status="WAITING_USER",
                event_id=f"run_waiting_user:{view.run_id}:{record.approval_id}",
                event_type="run_waiting_user",
                timestamp=timestamp,
                payload={
                    "approval_id": record.approval_id,
                    "action": record.action,
                    "scope": record.scope,
                    "risk_level": record.risk_level,
                    "params": dict(params or {}),
                },
            )
            view.store.append_event(
                view.tenant_id,
                view.session_id,
                view.run_id,
                event_id=f"approval.requested:{view.run_id}:{record.approval_id}",
                event_type="approval.requested",
                timestamp=timestamp,
                run_revision=view.head().current_revision,
                payload={
                    "approval_id": record.approval_id,
                    "action": record.action,
                    "scope": record.scope,
                    "risk_level": record.risk_level,
                    "params": dict(params or {}),
                },
            )
            decided = record
            while decided.status == "PENDING":
                try:
                    await asyncio.wait_for(waiter.wait(), timeout=0.5)
                except asyncio.TimeoutError:
                    pass
                refreshed = self._store.get_approval(
                    view.tenant_id, view.run_id, record.approval_id
                )
                if refreshed is None:
                    raise RuntimeError(
                        f"durable approval record vanished: {record.approval_id}"
                    )
                decided = refreshed
            view.transition_run_with_event(
                run_status="RUNNING",
                event_id=f"run_resumed:{view.run_id}:{record.approval_id}",
                event_type="run_resumed",
                timestamp=self._timestamp(),
                payload={
                    "approval_id": record.approval_id,
                    "decision": decided.status,
                    "decided_by": decided.decided_by,
                },
            )
            view.store.append_event(
                view.tenant_id,
                view.session_id,
                view.run_id,
                event_id=f"approval.decided:{view.run_id}:{record.approval_id}",
                event_type="approval.decided",
                timestamp=self._timestamp(),
                run_revision=view.head().current_revision,
                payload={
                    "approval_id": record.approval_id,
                    "decision": decided.status,
                    "decided_by": decided.decided_by,
                },
            )
            return decided
        finally:
            self._approval_waiters.pop(waiter_key, None)

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
        waiter = self._approval_waiters.get(
            (request.tenant_id, request.run_id, request.approval_id)
        )
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

    def _artifact_scoped_read(self, request: Any) -> tuple[Any, Any]:
        """Locate one artifact's durable row + its recorded workspace —
        shared by read_artifact/read_artifact_preview so both resolve the
        same path and root."""
        # The projected ArtifactSummary carries an opaque artifact://
        # reference; the durable fact row holds the real workspace path.
        try:
            read = self._store.read_run_snapshot(
                request.tenant_id,
                request.run_id,
                session_id=request.session_id,
            )
        except DurableStoreError as error:
            raise self._store_error(
                error,
                run_id=request.run_id,
                request_id=request.request_id,
                lookup=True,
            ) from error
        artifact = next(
            (
                item
                for item in read.artifacts
                if item.artifact_id == request.artifact_id
            ),
            None,
        )
        if artifact is None:
            raise AgentServiceError(
                ServiceErrorCode.INVALID_REQUEST,
                f"artifact not found for this run: {request.artifact_id}",
            )
        from .run_projector import request_workspace_root
        # The run's resolved root is persisted on its start intent — the
        # recorded root is authoritative even when explicitly empty.
        # Records written before the field existed carry no key; those
        # alone fall back to the session resolution (durable boundary).
        recorded = request_workspace_root(read)
        if recorded is not None:
            root = Path(recorded).resolve() if recorded else None
        else:
            root = self._contexts.session_workspace(
                request.tenant_id, request.session_id
            )
        if root is None:
            raise AgentServiceError(
                ServiceErrorCode.INVALID_REQUEST,
                "service has no workspace root",
            )
        return artifact, self._scoped_workspace(root)

    async def read_artifact(self, request: Any) -> Any:
        """Read one artifact's file via the session workspace — same guarded,
        redacted path as read_workspace_file. ``stale`` marks a file whose
        bytes no longer match the committed digest."""
        self._ensure_open()
        import asyncio

        artifact, workspace = self._artifact_scoped_read(request)

        def _read() -> tuple[str, str, str]:
            # 一次受保护字节读取 → 正文投影 + digest + stale 必须同源 —
            # 预览期间文件被替换也不会拼出混合版本。
            try:
                projected, digest, _redacted = workspace.read_document(
                    artifact.reference
                )
            except FileNotFoundError:
                return "", "", "missing"
            except (ValueError, IsADirectoryError):
                return "", "", "unsupported"
            return projected, digest, "ok"

        text, digest, result = await asyncio.to_thread(_read)
        from .contracts import ArtifactContent

        return ArtifactContent(
            artifact_id=artifact.artifact_id,
            reference=artifact.reference,
            text=text,
            stale=result == "ok" and digest != artifact.digest,
            result=result,
        )

    _PREVIEW_MAX_BYTES = 8 * 1024 * 1024
    _PREVIEW_MIMES = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        # SVG stays on the redacted text path (read_artifact) — raw bytes
        # would bypass sensitive-content projection.
        ".mp4": "video/mp4",
        ".webm": "video/webm",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
    }

    async def read_artifact_preview(self, request: Any) -> Any:
        """Constrained binary preview — same guarded workspace path as
        read_artifact, base64 over the transport. Mime comes from a fixed
        allowlist by extension; unsupported types and files past the cap
        return defined results rather than raw errors."""
        self._ensure_open()
        import asyncio
        import base64

        artifact, workspace = self._artifact_scoped_read(request)
        mime = self._PREVIEW_MIMES.get(
            Path(artifact.reference).suffix.lower()
        )
        from .contracts import ArtifactPreview

        if mime is None:
            return ArtifactPreview(
                artifact_id=artifact.artifact_id,
                mime="",
                data_b64="",
                size=0,
                stale=False,
                result="unsupported",
            )

        def _read() -> tuple[str, int, str, bool]:
            try:
                data, digest = workspace.read_binary(
                    artifact.reference, max_bytes=self._PREVIEW_MAX_BYTES
                )
            except FileNotFoundError:
                return "", 0, "missing", False
            except OverflowError:
                return "", 0, "too_large", False
            except (ValueError, IsADirectoryError):
                return "", 0, "unsupported", False
            return (
                base64.b64encode(data).decode("ascii"),
                len(data),
                "ok",
                digest != artifact.digest,
            )

        data_b64, size, result, stale = await asyncio.to_thread(_read)
        return ArtifactPreview(
            artifact_id=artifact.artifact_id,
            mime=mime,
            data_b64=data_b64,
            size=size,
            stale=stale,
            result=result,
        )

    def stream_events(self, request: EventStreamRequest):
        self._ensure_open()
        return self._event_repository().stream(request)

    async def close(self) -> None:
        import asyncio

        if self._closed:
            return
        loop = asyncio.get_running_loop()
        owned = [managed.task for managed in self._runs.values()]
        if self._scheduler_task is not None:
            owned.append(self._scheduler_task)
        owned.extend(self._graph_tasks)
        foreign = [
            task
            for task in owned
            if task.get_loop() is not loop and not task.done()
        ]
        if foreign:
            # 仍活跃的外部 loop 任务无法在此取消或等待 — 属调用方
            # 生命周期错配，如实报错而非静默遗留。已完成的任务
            # 不参与等待，在下方收集时跳过。
            raise RuntimeError(
                "AgentService.close() must run on the event loop that "
                f"owns the Service — {len(foreign)} active task(s) are "
                "bound to a different loop"
            )
        self._closed = True
        if self._scheduler_task is not None:
            self._scheduler_task.cancel()
        tasks = []
        for managed in self._runs.values():
            task = managed.task
            if task.get_loop() is not loop:
                continue  # foreign done tasks — live ones rejected above
            context = managed.run_context
            if getattr(context, "_pause_gate", None) is not None:
                # 进程内暂停不跨进程存活：关闭时收束本地 gate 等待；不在
                # durable 上落隐式用户取消 — Run 保持 SUSPENDED。
                task.cancel()
            tasks.append(task)
        if (
            self._scheduler_task is not None
            and self._scheduler_task.get_loop() is loop
        ):
            tasks.append(self._scheduler_task)
        for task in tuple(self._graph_tasks):
            if task.get_loop() is not loop:
                continue
            task.cancel()
            tasks.append(task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._runs.clear()
        if self._events is not None:
            self._events.close()
        self._contexts.close()


__all__ = ["AgentService"]
