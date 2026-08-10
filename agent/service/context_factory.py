"""Explicit Context construction for the AgentService boundary."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

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
        self.workspace_for_run = workspace_for_run
        self.application = ApplicationContext(
            workspace_root=self.workspace_root,
            runtime_store=store,
            runtime_writer_id=writer_id,
        )
        self._sessions: dict[tuple[str, str], SessionContext] = {}
        self._closed = False

    @property
    def writer_id(self) -> str:
        return self.application.runtime_writer_id

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
            session = SessionContext(
                self.application,
                session_id=request.session_id,
                user_id=request.user_id,
                tenant_id=request.tenant_id,
                memory_namespace=f"{request.tenant_id}:{request.user_id}",
            )
            # ApplicationContext predates tenant-scoped session keys.  Keep
            # the composite key here so equal session IDs in two tenants do
            # not collide while the underlying durable identity stays exact.
            self.application._sessions[key] = session  # type: ignore[attr-defined,index]
            self._sessions[key] = session
        elif session.user_id != request.user_id:
            raise ValueError("session identity belongs to another user")

        existing = session.get_run(run_id)
        if existing is not None and not existing.closed:
            return existing
        workspace = (
            self.workspace_for_run(
                request.tenant_id,
                request.session_id,
                run_id,
            )
            if self.workspace_for_run is not None
            else self.workspace_root
        )
        if workspace is not None:
            workspace = workspace.resolve()
        return session.create_run(
            run_id,
            # The Service boundary owns an explicit workspace root.  Passing
            # it here is mandatory: omitting it silently routes the Runtime
            # to the legacy process-global filesystem facade.
            workspace=workspace,
            request_id=request.request_id,
            writer_id=self.writer_id,
            takeover_writer=takeover_writer,
        )

    def release_run(self, run: RunContext) -> None:
        if not run.closed:
            run.close()

    def close(self) -> None:
        if self._closed:
            return
        self.application.close()
        self._sessions.clear()
        self._closed = True


__all__ = ["ServiceContextFactory"]
