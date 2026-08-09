"""Deterministic path resolution for a scoped Runtime workspace.

The resolver deliberately knows only one root: the workspace owned by the
current ``RunContext``.  It does not consult the process cwd, a global
workspace manager, or the legacy ``tools.filesystem`` module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Union


class WorkspaceBoundaryError(PermissionError):
    """A path escaped the workspace owned by the current Run."""

    code = "WORKSPACE_BOUNDARY_VIOLATION"

    def __init__(self, path: object, root: Path) -> None:
        self.path = str(path)
        self.root = root
        super().__init__(f"{self.code}: path is outside workspace: {self.path}")


class WorkspaceResolver:
    """Resolve exact user paths under one immutable workspace root."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def resolve(
        self,
        value: Union[str, Path],
        *,
        must_exist: bool = False,
    ) -> Path:
        raw = str(value or ".").strip()
        candidate = Path(raw)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise WorkspaceBoundaryError(value, self.root) from exc
        if must_exist and not resolved.exists():
            raise FileNotFoundError(f"file does not exist in workspace: {value}")
        return resolved

    def relative(self, value: Union[str, Path]) -> str:
        """Return a stable workspace-relative reference for an in-root path."""
        return self.resolve(value).relative_to(self.root).as_posix() or "."


__all__ = ["WorkspaceBoundaryError", "WorkspaceResolver"]
