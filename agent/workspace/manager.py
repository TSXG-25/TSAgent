"""WorkspaceManager — manages one or more Workspace instances.

This is NOT a singleton. It supports multi-project usage.
The "current" workspace is tracked per-session.
"""
from pathlib import Path
from typing import Optional

from agent.workspace.workspace import Workspace

_active_manager: Optional["WorkspaceManager"] = None


class WorkspaceManager:
    """Manages workspace instances for one or more projects.

    Use WorkspaceManager.current() to get the active workspace.
    Use WorkspaceManager.get(root) to access/create a workspace for a specific root.
    """

    def __init__(self, default_root: Path | None = None):
        self._workspaces: dict[str, Workspace] = {}
        self._current: Optional[Workspace] = None

        if default_root:
            self.get(default_root)

    def get(self, root: Path) -> Workspace:
        """Get or create a workspace for a project root.

        Auto-sets as current if no current workspace is set.
        """
        resolved = root.resolve()
        key = str(resolved)

        if key not in self._workspaces:
            ws = Workspace(resolved)
            self._workspaces[key] = ws

        ws = self._workspaces[key]
        if self._current is None:
            self._current = ws
        return ws

    def set_current(self, ws: Workspace) -> None:
        """Set the currently active workspace."""
        self._current = ws

    def current(self) -> Optional[Workspace]:
        """Get the currently active workspace, or None."""
        return self._current

    def list_workspaces(self) -> list[Workspace]:
        """List all managed workspace instances."""
        return list(self._workspaces.values())

    # ── Global accessors ──

    @staticmethod
    def get_active_manager() -> Optional["WorkspaceManager"]:
        return _active_manager

    @staticmethod
    def set_active_manager(mgr: "WorkspaceManager") -> None:
        global _active_manager
        _active_manager = mgr

    @staticmethod
    def current_workspace() -> Optional[Workspace]:
        """Convenience: get current workspace from active manager."""
        if _active_manager:
            return _active_manager.current()
        return None

    def __repr__(self) -> str:
        n = len(self._workspaces)
        current = ("current" if self._current else "no current")
        return f"WorkspaceManager({n} ws, {current})"