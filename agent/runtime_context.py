"""Explicit ownership boundaries for v2.3A Runtime state.

The context hierarchy is intentionally small:

``ApplicationContext``
    Configuration and shared immutable resources.
``SessionContext``
    User/session identity and session-owned conversation views.
``RunContext``
    One execution's mutable artifacts and event stream.

This module does not provide a new orchestrator. It only creates and closes
scoped state so later Runtime, Store, and Service layers have one owner model.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Optional

from agent.conversation import ConversationRetriever, ConversationTracker
from agent.diagnostics import RunDiagnosticsSink
from agent.event_bus import EventBus
from agent.services.artifact_service import ArtifactStore
from agent.services.memory_service import ScopedMemoryView
from agent.runtime_store import SqliteRuntimeStore
from agent.runtime_store.view import DurableRuntimeStoreView
from agent.interruption import CancellationView


class ContextClosedError(RuntimeError):
    """Raised when a closed Application/Session/Run context is used."""


def _identifier(value: Optional[str], label: str, *, generated: bool = False) -> str:
    value = str(value or "").strip()
    if not value and generated:
        value = f"{label}-{uuid.uuid4().hex}"
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError(f"{label} must be a non-empty path-safe identifier")
    return value


class ApplicationContext:
    """Application-wide immutable configuration and session factory."""

    def __init__(
        self,
        *,
        config: Optional[Mapping[str, Any]] = None,
        workspace_root: Optional[Path] = None,
        runtime_store: Optional[SqliteRuntimeStore] = None,
        runtime_store_path: Optional[Path] = None,
        runtime_writer_id: Optional[str] = None,
    ) -> None:
        if runtime_store is not None and runtime_store_path is not None:
            raise ValueError("runtime_store 与 runtime_store_path 只能提供一个")
        self.config = MappingProxyType(dict(config or {}))
        self.workspace_root = workspace_root.resolve() if workspace_root else None
        self.runtime_store = runtime_store
        if runtime_store_path is not None:
            self.runtime_store = SqliteRuntimeStore.open(runtime_store_path)
        self.runtime_writer_id = _identifier(
            runtime_writer_id or f"writer-{uuid.uuid4().hex}",
            "runtime_writer_id",
        )
        self._sessions: dict[str, SessionContext] = {}
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def create_session(
        self,
        session_id: Optional[str] = None,
        *,
        user_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        memory_namespace: Optional[str] = None,
    ) -> "SessionContext":
        if self._closed:
            raise ContextClosedError("application context is closed")
        sid = _identifier(session_id, "session_id", generated=True)
        uid = _identifier(user_id or sid, "user_id")
        if sid in self._sessions and not self._sessions[sid].closed:
            raise ValueError(f"session already exists: {sid}")
        tid = _identifier(tenant_id or "default", "tenant_id")
        namespace = _identifier(memory_namespace or sid, "memory_namespace")
        session = SessionContext(
            self,
            session_id=sid,
            user_id=uid,
            tenant_id=tid,
            memory_namespace=namespace,
        )
        self._sessions[sid] = session
        return session

    def close(self) -> None:
        if self._closed:
            return
        for session in list(self._sessions.values()):
            session.close()
        self._sessions.clear()
        if self.runtime_store is not None:
            self.runtime_store.close()
        self._closed = True


class SessionContext:
    """One user's session scope and its conversation-owned views."""

    def __init__(
        self,
        application: ApplicationContext,
        *,
        session_id: str,
        user_id: str,
        tenant_id: str,
        memory_namespace: str,
    ) -> None:
        self.application = application
        self.session_id = _identifier(session_id, "session_id")
        self.user_id = _identifier(user_id, "user_id")
        self.tenant_id = _identifier(tenant_id, "tenant_id")
        self.memory_namespace = _identifier(memory_namespace, "memory_namespace")
        self.memory_view = ScopedMemoryView(self.memory_namespace)
        self.conversation_tracker = ConversationTracker()
        self.conversation_retriever = ConversationRetriever(self.conversation_tracker)
        self._runs: dict[str, RunContext] = {}
        self._current_run_id: Optional[str] = None
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def active_run_ids(self) -> tuple[str, ...]:
        return tuple(self._runs)

    @property
    def current_run(self) -> Optional["RunContext"]:
        """Return the single attached in-process Run, if one exists."""
        if self._current_run_id is None:
            return None
        current = self._runs.get(self._current_run_id)
        if current is None or current.closed:
            return None
        return current

    def get_run(self, run_id: str) -> Optional["RunContext"]:
        return self._runs.get(run_id)

    def create_run(
        self,
        run_id: Optional[str] = None,
        *,
        workspace: Any = None,
        request_id: Optional[str] = None,
        checkpoint_store: Any = None,
        run_resume_store: Any = None,
        writer_id: Optional[str] = None,
        takeover_writer: bool = False,
    ) -> "RunContext":
        if self._closed:
            raise ContextClosedError("session context is closed")
        rid = _identifier(run_id, "run_id", generated=True)
        existing = self._runs.get(rid)
        if existing is not None and not existing.closed:
            raise ValueError(f"run already exists: {rid}")
        run = RunContext(
            self,
            run_id=rid,
            workspace=workspace,
            request_id=request_id,
            checkpoint_store=checkpoint_store,
            run_resume_store=run_resume_store,
            writer_id=writer_id,
            takeover_writer=takeover_writer,
        )
        self._runs[rid] = run
        self._current_run_id = rid
        return run

    def activate_run(self, run_id: str) -> "RunContext":
        """Mark an existing logical Run as the Session's current Run."""
        run = self._runs.get(run_id)
        if run is None:
            raise KeyError(f"unknown run: {run_id}")
        run.ensure_open()
        self._current_run_id = run_id
        return run

    def deactivate_run(self, run_id: str) -> None:
        """Detach a Run without closing or deleting its recoverable state."""
        if self._current_run_id == run_id:
            self._current_run_id = None

    def _forget_run(self, run_id: str) -> None:
        self._runs.pop(run_id, None)
        if self._current_run_id == run_id:
            self._current_run_id = None

    def reset_conversation(self) -> None:
        """Replace only this Session's in-process conversation view."""
        if self._closed:
            raise ContextClosedError("session context is closed")
        self.conversation_tracker = ConversationTracker()
        self.conversation_retriever = ConversationRetriever(self.conversation_tracker)

    def close(self) -> None:
        if self._closed:
            return
        for run in list(self._runs.values()):
            run.close()
        self.memory_view.close()
        self._runs.clear()
        self._current_run_id = None
        self._closed = True


class RunContext:
    """Mutable state owned by exactly one execution Run."""

    def __init__(
        self,
        session: SessionContext,
        *,
        run_id: str,
        workspace: Any = None,
        request_id: Optional[str] = None,
        checkpoint_store: Any = None,
        run_resume_store: Any = None,
        writer_id: Optional[str] = None,
        takeover_writer: bool = False,
    ) -> None:
        self.session = session
        self.application = session.application
        self.session_id = session.session_id
        self.user_id = session.user_id
        self.tenant_id = session.tenant_id
        self.run_id = _identifier(run_id, "run_id")
        self.request_id = _identifier(
            request_id or f"request-{uuid.uuid4().hex}",
            "request_id",
        )
        self.workspace = workspace
        self.durable_store_view: DurableRuntimeStoreView | None = None
        self.cancellation_view: CancellationView | None = None
        if self.application.runtime_store is not None:
            if checkpoint_store is not None or run_resume_store is not None:
                raise ValueError(
                    "durable Runtime Store 模式禁止同时注入 legacy Checkpoint/RunResume Store"
                )
            self.durable_store_view = DurableRuntimeStoreView(
                self.application.runtime_store,
                tenant_id=self.tenant_id,
                session_id=self.session_id,
                run_id=self.run_id,
                request_id=self.request_id,
                writer_id=writer_id or self.application.runtime_writer_id,
                takeover_fence=takeover_writer,
            )
            self.checkpoint_store = self.durable_store_view.checkpoint_store
            self.run_resume_store = self.durable_store_view.run_resume_store
            self.cancellation_view = CancellationView(
                self.application.runtime_store,
                tenant_id=self.tenant_id,
                session_id=self.session_id,
                run_id=self.run_id,
            )
        else:
            self.checkpoint_store = checkpoint_store
            self.run_resume_store = run_resume_store
        self.scope_id = f"{self.tenant_id}:{self.session_id}:{self.run_id}"
        self.artifacts = ArtifactStore(scope_id=f"run:{self.scope_id}")
        self.event_bus = EventBus(scope_id=f"run:{self.scope_id}")
        self._owns_workspace = False
        if isinstance(workspace, Path):
            from agent.services.workspace_service import WorkspaceService

            self.workspace = WorkspaceService.scoped(
                workspace,
                event_bus=self.event_bus,
                build_index=False,
                lazy_index=True,
            )
            self._owns_workspace = True
        self.clock = time.time
        self.created_at = self.clock()
        self.diagnostics = RunDiagnosticsSink(scope_id=self.scope_id)
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def ensure_open(self) -> None:
        if self._closed:
            raise ContextClosedError(f"run context is closed: {self.run_id}")

    def close(self) -> None:
        if self._closed:
            return
        self.event_bus.close()
        self.artifacts.close()
        self.diagnostics.close()
        if self.durable_store_view is not None:
            self.durable_store_view.close()
        if self._owns_workspace and self.workspace is not None:
            self.workspace.close()
        self._closed = True
        self.session._forget_run(self.run_id)

    def destroy(self) -> None:
        """Close and explicitly purge process-local artifact state."""
        if self._closed:
            self.artifacts.destroy()
            return
        self.event_bus.close()
        self.artifacts.destroy()
        self.diagnostics.close()
        if self.durable_store_view is not None:
            self.durable_store_view.close()
        if self._owns_workspace and self.workspace is not None:
            self.workspace.close()
        self._closed = True
        self.session._forget_run(self.run_id)


__all__ = [
    "ApplicationContext",
    "ContextClosedError",
    "RunContext",
    "SessionContext",
]
