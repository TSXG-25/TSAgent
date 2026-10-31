"""Stable public API for TSAgent.

``TSAgent`` is a thin facade over ``AgentService``: the SQLite durable run
log is the single public execution entry, so every public run carries
durable conversation history.
"""
from __future__ import annotations

import uuid
from pathlib import Path

from agent.bootstrap import load_all
from agent.service import (
    EventStreamRequest,
    EventType,
    RunLookupRequest,
    StartRunRequest,
    create_default_agent_service,
)


class TSAgent:
    """Public application facade for one tenant/user/session."""

    def __init__(
        self,
        user_id: str = "default",
        *,
        database_path: Path = Path(".tsagent/runtime.sqlite"),
        workspace_root: Path | None = None,
        tenant_id: str = "default",
        session_id: str | None = None,
    ) -> None:
        self._user_id = str(user_id)
        self._tenant_id = str(tenant_id)
        self._session_id = str(session_id) if session_id else f"session-{uuid.uuid4().hex}"
        database_path = Path(database_path)
        database_path.parent.mkdir(parents=True, exist_ok=True)
        load_all(
            include_workspace=False,
            include_workflows=False,
            include_knowledge=False,
        )
        self._service = create_default_agent_service(
            database_path,
            workspace_root=workspace_root,
        )

    @property
    def user_id(self) -> str:
        return self._user_id

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    @property
    def session_id(self) -> str:
        return self._session_id

    async def run(self, user_input: str) -> str:
        """Run one user request through the durable AgentService boundary."""
        if not isinstance(user_input, str) or not user_input.strip():
            raise ValueError("user_input must be a non-empty string")

        request = StartRunRequest(
            tenant_id=self._tenant_id,
            user_id=self._user_id,
            session_id=self._session_id,
            run_id=f"run-{uuid.uuid4().hex}",
            request_id=f"request-{uuid.uuid4().hex}",
            request_text=user_input,
        )
        return await _run_to_answer(
            self._service,
            request,
            tenant_id=self._tenant_id,
            user_id=self._user_id,
            session_id=self._session_id,
        )

    async def close(self) -> None:
        await self._service.close()

    async def __aenter__(self) -> "TSAgent":
        return self

    async def __aexit__(self, *_exc_info) -> None:
        await self.close()


async def _run_to_answer(
    service,
    request: StartRunRequest,
    *,
    tenant_id: str,
    user_id: str,
    session_id: str,
) -> str:
    """Drive one StartRunRequest to its terminal snapshot and return output text."""
    handle = await service.start_run(request)

    stream = service.stream_events(
        EventStreamRequest(
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            run_id=handle.run_id,
            request_id=f"stream-{uuid.uuid4().hex}",
            after_sequence=0,
        )
    )
    iterator = stream.__aiter__()
    try:
        async for event in iterator:
            if (
                event.is_terminal
                or event.event_type is EventType.RUN_WAITING_USER
            ):
                break
    finally:
        close_stream = getattr(iterator, "aclose", None)
        if close_stream is not None:
            await close_stream()

    snapshot = await service.get_run(
        RunLookupRequest(
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            run_id=handle.run_id,
            request_id=f"lookup-{uuid.uuid4().hex}",
        )
    )
    if snapshot.output is not None:
        return snapshot.output.text
    return ""


__all__ = ["TSAgent"]
