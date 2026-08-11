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


def parse_args(argv: Sequence[str] | None = None) -> SidecarConfig:
    parser = argparse.ArgumentParser(description="TSAgent local AgentService JSONL sidecar")
    parser.add_argument(
        "--database",
        type=Path,
        default=os.getenv("TSAGENT_RUNTIME_DB"),
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
    args = parser.parse_args(argv)
    if args.database is None:
        parser.error("--database or TSAGENT_RUNTIME_DB is required")
    return SidecarConfig(
        database_path=Path(args.database),
        workspace_root=(None if args.workspace_root is None else Path(args.workspace_root)),
        writer_id=args.writer_id,
    )


def create_service(config: SidecarConfig) -> object:
    """Construct only through the existing public AgentService factory."""

    from ..factory import create_default_agent_service

    return create_default_agent_service(
        config.database_path,
        workspace_root=config.workspace_root,
        writer_id=config.writer_id,
    )


__all__ = ["SidecarConfig", "create_service", "parse_args"]
