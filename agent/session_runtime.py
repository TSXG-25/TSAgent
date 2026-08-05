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
from typing import Optional

from agent.memory.lifecycle import MemoryRuntime, MemoryResetReport


_SAFE_SESSION_ID = re.compile(r"^[^/\\]+$")


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
        persistent: bool = False,
    ) -> None:
        self._session_id = _validate_id(session_id, "session_id")
        self._user_id = _validate_id(user_id, "user_id")
        self._persistent = bool(persistent)
        self._closed = False
        self._agent = self._new_agent()

    @classmethod
    def create(
        cls,
        session_id: Optional[str] = None,
        *,
        user_id: Optional[str] = None,
        persistent: bool = False,
    ) -> "SessionRuntime":
        """Create a session and establish its initial lifecycle boundary."""
        sid = _validate_id(session_id or f"session-{uuid.uuid4().hex}", "session_id")
        # If no stable user identity is supplied, the session itself is the
        # namespace. This makes repeated benchmark cases isolated by default.
        uid = _validate_id(user_id or sid, "user_id")
        runtime = cls(session_id=sid, user_id=uid, persistent=persistent)
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
    def persistent(self) -> bool:
        return self._persistent

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def agent(self):
        """Return the underlying runtime for advanced integrations."""
        return self._agent

    async def run(self, user_input: str) -> str:
        """Run one request within this session."""
        if self._closed:
            raise RuntimeError("session has been destroyed")
        return await self._agent.run(user_input)

    def reset(
        self,
        *,
        conversation: bool = True,
        runtime: bool = True,
        facts: bool = False,
    ) -> Optional[MemoryResetReport]:
        """Reset selected session layers without changing the session id.

        ``runtime=True`` replaces the mutable agent/orchestrator instance and
        clears the process-local ArtifactStore.  Conversation and Facts are
        delegated to ``MemoryRuntime`` so their namespace is explicit.
        """
        if self._closed:
            raise RuntimeError("session has been destroyed")

        report = None
        if conversation or facts:
            report = MemoryRuntime.reset(
                self._user_id,
                conversation=conversation,
                facts=facts,
            )

        if runtime:
            from agent.services.artifact_service import ArtifactService

            ArtifactService.clear()
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

    def _new_agent(self):
        from agent.runtime import UniversalAgent

        return UniversalAgent(self._user_id)


__all__ = ["SessionRuntime"]
