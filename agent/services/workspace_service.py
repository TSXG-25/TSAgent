"""WorkspaceService — Service Layer wrapper for Workspace.

This is the ONLY way Planner/Executor/Tools should access Workspace.
Service does NOT expose internal Workspace components.
"""
from pathlib import Path
from typing import Optional

from agent.workspace import FileNode, PathMatch, WorkspaceContext
from agent.workspace.manager import WorkspaceManager
from agent.workspace.workspace import Workspace
from agent.event_bus import EventBus


class WorkspaceService:
    """Workspace service — public API for Planner and Tools.

    All access goes through this service. No direct workspace imports.
    """

    def __init__(
        self,
        manager: Optional[WorkspaceManager] = None,
        *,
        event_bus: Optional[EventBus] = None,
        workspace: Optional[Workspace] = None,
        owns_manager: bool = False,
        lazy_index: bool = False,
    ):
        if workspace is not None and manager is not None:
            raise ValueError("provide workspace or manager, not both")
        self._workspace = workspace
        self._manager = (
            manager
            if manager is not None
            else (None if workspace is not None else WorkspaceManager.get_active_manager())
        )
        self._event_bus = event_bus
        self._owns_manager = owns_manager
        self._closed = False
        self._lazy_index = lazy_index
        self._index_built = False

    @classmethod
    def scoped(
        cls,
        root: Path,
        *,
        event_bus: Optional[EventBus] = None,
        build_index: bool = True,
        lazy_index: bool = False,
    ) -> "WorkspaceService":
        """Create a Run-owned workspace service for an explicit root."""
        manager = WorkspaceManager(event_bus=event_bus)
        workspace = manager.get(root)
        if build_index:
            workspace.build_index()
        service = cls(
            manager=manager,
            event_bus=event_bus,
            owns_manager=True,
            lazy_index=lazy_index,
        )
        service._index_built = build_index
        return service

    # ── Path resolution ──

    def resolve(self, spec: str) -> list[PathMatch]:
        """Resolve a path spec to ranked candidate paths."""
        ws = self._ensure_index()
        return ws.resolve(spec)

    def find(self, name: str) -> list[PathMatch]:
        """Find files by fuzzy name match."""
        ws = self._ensure_index()
        return ws.find(name)

    def lookup(self, path: str) -> Optional[FileNode]:
        """Look up exact file metadata by relative path."""
        ws = self._ensure_index()
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
        ws = self._ensure_index()
        return ws.file_count()

    def refresh(self) -> None:
        ws = self._ensure_index()
        ws.refresh()

    # ── Internal ──

    def _ensure_workspace(self):
        if self._closed:
            raise RuntimeError("workspace service is closed")
        ws = self._workspace
        if ws is None and self._manager:
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

    def _ensure_index(self):
        ws = self._ensure_workspace()
        if self._lazy_index and not self._index_built:
            ws.build_index()
            self._index_built = True
        return ws

    def close(self) -> None:
        """Release owned indexes/caches; never delete the workspace root."""
        if self._closed:
            return
        if self._owns_manager and self._manager is not None:
            self._manager.close()
        elif self._workspace is not None:
            self._workspace.close()
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed


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
