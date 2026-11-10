"""Explicit Context construction for the AgentService boundary."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any
import uuid

if TYPE_CHECKING:
    from agent.runtime_context import ApplicationContext, RunContext, SessionContext
    from agent.runtime_store import SqliteRuntimeStore

from .contracts import ResumeRunRequest, StartRunRequest


class ServiceContextFactory:
    """Owns only process-local Context objects; durable state stays in SQLite."""

    def __init__(
        self,
        store: SqliteRuntimeStore,
        *,
        workspace_root: Path | None = None,
        workspace_for_run: Callable[[str, str, str], Path] | None = None,
        writer_id: str | None = None,
    ) -> None:
        self.workspace_root = workspace_root.resolve() if workspace_root else None
        # Session→workspace binding lives in the store; make the lookup the
        # default so every construction path (sidecar, CLI, in-process)
        # resolves a bound session's workspace instead of silently falling
        # back to the process-level root.
        self.workspace_for_run = workspace_for_run or (
            lambda tenant, session, _run: (
                (p := store.session_workspace_path(tenant, session)) and Path(p)
            ) or None
        )
        self._store = store
        self._writer_id = str(writer_id or f"writer-{uuid.uuid4().hex}")
        self._application: Any | None = None
        self._sessions: dict[tuple[str, str], SessionContext] = {}
        self._closed = False

    @property
    def application(self):
        if self._application is None:
            from agent.runtime_context import ApplicationContext

            self._application = ApplicationContext(
                workspace_root=self.workspace_root,
                runtime_store=self._store,
                runtime_writer_id=self._writer_id,
            )
        return self._application

    @property
    def writer_id(self) -> str:
        return self._writer_id

    @property
    def closed(self) -> bool:
        return self._closed

    def create_run(
        self,
        request: StartRunRequest | ResumeRunRequest,
        *,
        run_id: str,
        takeover_writer: bool = False,
    ) -> RunContext:
        if self._closed:
            raise RuntimeError("service context factory is closed")
        key = (request.tenant_id, request.session_id)
        session = self._sessions.get(key)
        if session is None or session.closed:
            from agent.runtime_context import SessionContext

            session = SessionContext(
                self.application,
                session_id=request.session_id,
                user_id=request.user_id,
                tenant_id=request.tenant_id,
                memory_namespace=f"{request.tenant_id}:{request.session_id}",
                learning_namespace=f"{request.tenant_id}:{request.user_id}",
                memory_scope="user",
            )
            # ApplicationContext predates tenant-scoped session keys. Keep
            # the composite key here so equal session IDs in two tenants do
            # not collide.
            self.application._sessions[key] = session  # type: ignore[attr-defined,index]
            self._sessions[key] = session
        elif session.user_id != request.user_id:
            raise ValueError("session identity belongs to another user")

        existing = session.get_run(run_id)
        if existing is not None and not existing.closed:
            return existing
        # Start 与 Resume 共用接纳时记录的 workspace — 之后的改绑只影响
        # 下一次接纳。仅历史记录缺字段（或直接 create_run）才回落到
        # 当前会话绑定。
        recorded, recorded_root = self._recorded_workspace_root(
            request, run_id
        )
        explicit_root = str(getattr(request, "workspace_root", "") or "")
        if recorded:
            workspace = recorded_root
        elif explicit_root:
            workspace = Path(explicit_root).resolve()
        else:
            workspace = self._run_workspace(request, run_id)
        from agent.runtime_context import RunContext

        context = session.create_run(
            run_id,
            # The Service boundary owns an explicit workspace root.  Passing
            # it here is mandatory: omitting it silently routes the Runtime
            # to the legacy process-global filesystem facade.
            workspace=workspace,
            request_id=request.request_id,
            writer_id=self.writer_id,
            takeover_writer=takeover_writer,
            worker_lease=True,
        )
        context.agent_profile = dict(
            (getattr(request, "metadata", None) or {}).get("agent_profile") or {}
        )
        context.context_refs = tuple(
            getattr(request, "context_refs", ()) or ()
        )
        context.delegation_depth = int(
            (getattr(request, "metadata", None) or {}).get("delegation_depth", 0)
            or 0
        )
        return context

    def session_workspace(self, tenant_id: str, session_id: str) -> Path | None:
        """Resolved workspace for a session — bound folder, else factory root."""
        root = self.workspace_for_run(tenant_id, session_id, "") or self.workspace_root
        return root.resolve() if root is not None else None

    def _run_workspace(
        self, request: StartRunRequest | ResumeRunRequest, run_id: str
    ) -> Path | None:
        """Per-run workspace resolution — the registered callable sees the
        real run_id (per-run roots) and returns None when unbound, in which
        case the factory root applies."""
        root = self.workspace_for_run(
            request.tenant_id, request.session_id, run_id
        ) or self.workspace_root
        return root.resolve() if root is not None else None

    def _recorded_workspace_root(
        self, request: StartRunRequest | ResumeRunRequest, run_id: str
    ) -> tuple[bool, "Path | None"]:
        """(recorded, root) — whether the start intent carried the field.

        A legacy record without the key lets the caller fall back to the
        live session binding; a recorded value — including empty — is the
        accepted Run's authority and must not be rebound."""
        from agent.runtime_store.errors import DurableStoreError

        from .run_projector import request_workspace_root

        try:
            read = self._store.read_run_snapshot(
                request.tenant_id,
                run_id,
                session_id=request.session_id,
            )
        except DurableStoreError:
            # Direct create_run without a reservation (unit tests, tools).
            return False, None
        recorded = request_workspace_root(read)
        if recorded is None:
            return False, None
        return True, Path(recorded).resolve() if recorded else None

    def release_run(self, run: RunContext) -> None:
        if not run.closed:
            run.close()

    def close(self) -> None:
        if self._closed:
            return
        if self._application is not None:
            self._application.close()
        self._sessions.clear()
        self._closed = True


__all__ = ["ServiceContextFactory"]
