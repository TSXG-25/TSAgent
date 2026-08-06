"""Session lifecycle facade.

One ``SessionRuntime`` owns one ``UniversalAgent`` instance and provides the
explicit create/reset/destroy boundary required by benchmarks and future API
servers.  A session namespace defaults to an isolated user namespace; callers
that intentionally need persistence must opt into ``persistent=True`` and/or
reuse the same ``SessionRuntime`` instance.
"""
from __future__ import annotations

import re
import uuid
from pathlib import Path
from typing import Any, Optional

from agent.memory.lifecycle import MemoryRuntime, MemoryResetReport
from agent.runtime_context import ApplicationContext, RunContext, SessionContext


_SAFE_SESSION_ID = re.compile(r"^[^/\\]+$")
DEFAULT_WORKSPACE_ROOT = Path(__file__).resolve().parent.parent


def _validate_id(value: str, label: str) -> str:
    value = str(value or "").strip()
    if not value or value in {".", ".."} or not _SAFE_SESSION_ID.fullmatch(value):
        raise ValueError(f"{label} must be a non-empty path-safe identifier")
    return value


class SessionRuntime:
    """Lifecycle wrapper for one agent session.

    ``persistent=False`` is the benchmark-safe default: creation and destroy
    both purge the namespace's facts.  Persistent application sessions retain
    facts on destroy but still clear conversation-scoped state.
    """

    def __init__(
        self,
        *,
        session_id: str,
        user_id: str,
        tenant_id: str = "default",
        persistent: bool = False,
        workspace=None,
        runtime_store: Any = None,
        runtime_store_path: Optional[Path] = None,
        runtime_writer_id: Optional[str] = None,
    ) -> None:
        self._session_id = _validate_id(session_id, "session_id")
        self._user_id = _validate_id(user_id, "user_id")
        self._tenant_id = _validate_id(tenant_id, "tenant_id")
        self._persistent = bool(persistent)
        self._workspace = workspace
        self._closed = False
        self._application_context = ApplicationContext(
            workspace_root=(workspace if isinstance(workspace, Path) else DEFAULT_WORKSPACE_ROOT),
            runtime_store=runtime_store,
            runtime_store_path=runtime_store_path,
            runtime_writer_id=runtime_writer_id,
        )
        self._context = self._application_context.create_session(
            self._session_id,
            user_id=self._user_id,
            tenant_id=self._tenant_id,
            memory_namespace=(
                f"{self._tenant_id}:{self._user_id}"
                if self._persistent
                else self._session_id
            ),
        )
        self._agent = self._new_agent()

    @classmethod
    def create(
        cls,
        session_id: Optional[str] = None,
        *,
        user_id: Optional[str] = None,
        tenant_id: str = "default",
        persistent: bool = False,
        workspace=None,
        runtime_store: Any = None,
        runtime_store_path: Optional[Path] = None,
        runtime_writer_id: Optional[str] = None,
    ) -> "SessionRuntime":
        """Create a session and establish its initial lifecycle boundary."""
        sid = _validate_id(session_id or f"session-{uuid.uuid4().hex}", "session_id")
        # If no stable user identity is supplied, the session itself is the
        # namespace. This makes repeated benchmark cases isolated by default.
        uid = _validate_id(user_id or sid, "user_id")
        runtime = cls(
            session_id=sid,
            user_id=uid,
            tenant_id=tenant_id,
            persistent=persistent,
            workspace=workspace,
            runtime_store=runtime_store,
            runtime_store_path=runtime_store_path,
            runtime_writer_id=runtime_writer_id,
        )
        runtime.reset(
            conversation=True,
            runtime=True,
            facts=not persistent,
        )
        return runtime

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def user_id(self) -> str:
        return self._user_id

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    @property
    def current_run(self) -> Optional[RunContext]:
        """The currently attached logical Run, if one exists."""
        return self._context.current_run

    @property
    def persistent(self) -> bool:
        return self._persistent

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def context(self) -> SessionContext:
        """Return the explicit session ownership context."""
        return self._context

    @property
    def agent(self):
        """Return the underlying runtime for advanced integrations."""
        return self._agent

    async def run(self, user_input: str, *, run_id: Optional[str] = None) -> str:
        """Run one request within this session."""
        if self._closed:
            raise RuntimeError("session has been destroyed")
        if run_id is not None:
            current = self.current_run
            if current is None or current.run_id != run_id:
                self.resume_run(run_id)
        return await self._agent.run(user_input)

    def start_run(
        self,
        run_id: Optional[str] = None,
        *,
        workspace=None,
        request_id: Optional[str] = None,
        checkpoint_store=None,
        run_resume_store=None,
    ) -> RunContext:
        """Explicitly start and attach a new logical Run."""
        if self._closed:
            raise RuntimeError("session has been destroyed")
        detach = getattr(self._agent, "detach_run", None)
        if callable(detach):
            detach()
        run = self._context.create_run(
            run_id,
            workspace=(workspace if workspace is not None else self._workspace or self._application_context.workspace_root),
            request_id=request_id,
            checkpoint_store=checkpoint_store,
            run_resume_store=run_resume_store,
        )
        self._agent.attach_run(run)
        return run

    def resume_run(
        self,
        run_id: str,
        *,
        workspace=None,
        request_id: Optional[str] = None,
        checkpoint_store=None,
        run_resume_store=None,
    ) -> RunContext:
        """Attach an existing in-process Run or recreate its logical id."""
        if self._closed:
            raise RuntimeError("session has been destroyed")
        detach = getattr(self._agent, "detach_run", None)
        if callable(detach):
            detach()
        run = self._context.get_run(run_id)
        if run is None:
            run = self._context.create_run(
                run_id,
                workspace=(workspace if workspace is not None else self._workspace or self._application_context.workspace_root),
                request_id=request_id,
                checkpoint_store=checkpoint_store,
                run_resume_store=run_resume_store,
            )
        self._agent.attach_run(run)
        return run

    def reset(
        self,
        *,
        conversation: bool = True,
        runtime: bool = True,
        facts: bool = False,
    ) -> Optional[MemoryResetReport]:
        """Reset selected session layers without changing the session id.

        ``runtime=True`` replaces only this session's mutable agent/runtime
        context. Conversation and Facts are delegated to ``MemoryRuntime`` so
        their namespace remains explicit; no process-global ArtifactStore is
        cleared here.
        """
        if self._closed:
            raise RuntimeError("session has been destroyed")

        report = None
        if conversation or facts:
            report = MemoryRuntime.reset(
                self._context.memory_namespace,
                conversation=conversation,
                facts=facts,
                conversation_tracker=self._context.conversation_tracker,
            )
        if conversation:
            self._context.reset_conversation()

        if runtime:
            detach = getattr(self._agent, "detach_run", None)
            if callable(detach):
                detach()
            # The old logical Run remains registered and recoverable, but a
            # runtime reset must not silently reattach it to the new Agent.
            self._agent = self._new_agent()
        return report

    def destroy(self, *, purge_facts: Optional[bool] = None) -> None:
        """Destroy this session; the operation is idempotent."""
        if self._closed:
            return
        if purge_facts is None:
            purge_facts = not self._persistent
        self.reset(
            conversation=True,
            runtime=True,
            facts=bool(purge_facts),
        )
        close = getattr(self._agent, "close", None)
        if callable(close):
            close()
        self._context.close()
        self._application_context.close()
        self._closed = True

    close = destroy

    def __enter__(self) -> "SessionRuntime":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.destroy()

    async def __aenter__(self) -> "SessionRuntime":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.destroy()

    def _new_agent(self, *, run_context: Optional[RunContext] = None):
        from agent.runtime import UniversalAgent

        return UniversalAgent(
            self._user_id,
            session_context=self._context,
            run_context=run_context,
            workspace=self._workspace or self._application_context.workspace_root,
        )


__all__ = ["SessionRuntime"]
