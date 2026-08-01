"""WorkspaceService — Service Layer wrapper for Workspace.

This is the ONLY way Planner/Executor/Tools should access Workspace.
Service does NOT expose internal Workspace components.
"""
from pathlib import Path
from typing import Optional

from agent.workspace import FileNode, PathMatch, WorkspaceContext
from agent.workspace.manager import WorkspaceManager


class WorkspaceService:
    """Workspace service — public API for Planner and Tools.

    All access goes through this service. No direct workspace imports.
    """

    def __init__(self, manager: Optional[WorkspaceManager] = None):
        self._manager = manager or WorkspaceManager.get_active_manager()

    # ── Path resolution ──

    def resolve(self, spec: str) -> list[PathMatch]:
        """Resolve a path spec to ranked candidate paths."""
        ws = self._ensure_workspace()
        return ws.resolve(spec)

    def find(self, name: str) -> list[PathMatch]:
        """Find files by fuzzy name match."""
        ws = self._ensure_workspace()
        return ws.find(name)

    def lookup(self, path: str) -> Optional[FileNode]:
        """Look up exact file metadata by relative path."""
        ws = self._ensure_workspace()
        return ws.lookup(path)

    # ── Context ──

    def current_file(self) -> Optional[str]:
        """Get the currently open file (relative path)."""
        ws = self._ensure_workspace()
        return ws.current_context().current_file

    def current_workspace(self):
        """Get the current Workspace instance."""
        return self._ensure_workspace()

    def current_context(self) -> WorkspaceContext:
        """Get full workspace context (opened files, edited files, etc.)."""
        ws = self._ensure_workspace()
        return ws.current_context()

    def record_open(self, path: str) -> None:
        """Record a file open event."""
        ws = self._ensure_workspace()
        ws.record_open(path)

    def record_edit(self, path: str) -> None:
        """Record a file edit event."""
        ws = self._ensure_workspace()
        ws.record_edit(path)

    # ── Index ──

    def file_count(self) -> int:
        ws = self._ensure_workspace()
        return ws.file_count()

    def refresh(self) -> None:
        ws = self._ensure_workspace()
        ws.refresh()

    # ── Internal ──

    def _ensure_workspace(self):
        ws = None
        if self._manager:
            ws = self._manager.current()
        if ws is None:
            # Fallback: try active manager
            mgr = WorkspaceManager.get_active_manager()
            if mgr:
                ws = mgr.current()
        if ws is None:
            raise RuntimeError(
                "No active workspace. "
                "Call bootstrap.init_workspace() first."
            )
        return ws


# Global instance for easy access
_workspace_service: Optional[WorkspaceService] = None


def get_workspace_service() -> WorkspaceService:
    global _workspace_service
    if _workspace_service is None:
        _workspace_service = WorkspaceService()
    return _workspace_service


def set_workspace_service(service: WorkspaceService) -> None:
    global _workspace_service
    _workspace_service = service