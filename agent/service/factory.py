"""Public construction helpers for the AgentService boundary."""

from __future__ import annotations

from pathlib import Path

from agent.runtime_store import SqliteRuntimeStore

from .context_factory import ServiceContextFactory
from .runtime_launcher import RuntimeExecutionLauncher
from .service import AgentService


def create_default_agent_service(
    database_path: Path,
    *,
    workspace_root: Path | None = None,
    writer_id: str | None = None,
) -> AgentService:
    """Create the standard local Service without exposing Runtime internals.

    The returned Service owns the SQLite Store through its ContextFactory;
    callers should close the Service rather than closing the Store directly.
    """

    store = SqliteRuntimeStore.open(database_path)
    try:
        contexts = ServiceContextFactory(
            store,
            workspace_root=workspace_root,
            writer_id=writer_id,
        )
        return AgentService(
            runtime_store=store,
            launcher=RuntimeExecutionLauncher(),
            context_factory=contexts,
        )
    except BaseException:
        store.close()
        raise


__all__ = ["create_default_agent_service"]
