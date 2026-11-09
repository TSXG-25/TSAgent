"""Configuration and construction for the Python AgentService sidecar."""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class SidecarConfig:
    database_path: Path
    workspace_root: Path | None = None
    writer_id: str | None = None
    allowed_origins: frozenset[str] = frozenset()


DEFAULT_ALLOWED_ORIGINS = frozenset(
    {"http://localhost:4173", "http://127.0.0.1:4173"}
)


DEFAULT_DATABASE_PATH = Path.home() / ".tsagent" / "runtime.db"


def parse_args(argv: Sequence[str] | None = None) -> SidecarConfig:
    parser = argparse.ArgumentParser(description="TSAgent local AgentService JSONL sidecar")
    parser.add_argument(
        "--database",
        type=Path,
        default=os.getenv("TSAGENT_RUNTIME_DB") or DEFAULT_DATABASE_PATH,
        help="file-backed SQLite Runtime Store path",
    )
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=os.getenv("TSAGENT_WORKSPACE_ROOT"),
    )
    parser.add_argument(
        "--writer-id",
        default=os.getenv("TSAGENT_WRITER_ID"),
    )
    parser.add_argument(
        "--allow-origin",
        action="append",
        default=[
            item.strip()
            for item in os.getenv("TSAGENT_SIDECAR_ALLOWED_ORIGINS", "").split(",")
            if item.strip()
        ],
        help="allowed Origin for the --http transport (repeatable); "
             "defaults to the Vite dev origin",
    )
    args = parser.parse_args(argv)
    return SidecarConfig(
        database_path=Path(args.database).expanduser(),
        workspace_root=(None if args.workspace_root is None else Path(args.workspace_root)),
        writer_id=args.writer_id,
        allowed_origins=frozenset(args.allow_origin) or DEFAULT_ALLOWED_ORIGINS,
    )


def create_service(
    config: SidecarConfig,
    *,
    bootstrap_tools: bool = True,
    defer_context_creation: bool = False,
) -> object:
    """Construct the local Service with the application registries ready.

    The sidecar is an application entry point, just like the CLI.  The
    ``AgentService`` factory intentionally does not import process-wide tool
    modules as a hidden side effect, but the Runtime compiler still needs the
    immutable application-level registry populated before a Run starts.  In
    particular, omitting this bootstrap makes the scoped ``filesystem.*``
    plans fail their static tool-existence check before they can reach the
    RunContext workspace.

    This loads registrations only; it does not initialize the legacy global
    workspace or make it a source of filesystem truth.  Actual filesystem
    execution remains bound to the RunContext by PlanExecutor.
    """

    if bootstrap_tools:
        from agent.bootstrap import load_tool_modules

        # Core deterministic tools are enough for direct callers that compile
        # a plan immediately.  Optional modules are loaded by PlanExecutor
        # when a plan actually references one; importing them here makes
        # health/start_run pay for unused dependencies.
        load_tool_modules(("filesystem",))

    # Capability resolution is an application-level registry lookup used by
    # the effect-truth gate.  Registering it here keeps the sidecar and CLI
    # on the same runtime capability surface without creating a second
    # service-local registry.
    from agent.registry.capability_registry import register_default_capabilities

    register_default_capabilities()

    from ..factory import create_default_agent_service

    return create_default_agent_service(
        config.database_path,
        workspace_root=config.workspace_root,
        writer_id=config.writer_id,
        defer_context_creation=defer_context_creation,
    )


__all__ = ["SidecarConfig", "create_service", "parse_args"]
