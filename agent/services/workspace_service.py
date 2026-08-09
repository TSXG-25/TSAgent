"""WorkspaceService — Service Layer wrapper for Workspace.

This is the ONLY way Planner/Executor/Tools should access Workspace.
Service does NOT expose internal Workspace components.
"""
from pathlib import Path
import shutil
from typing import Optional

from agent.workspace import FileNode, PathMatch, WorkspaceContext
from agent.workspace.manager import WorkspaceManager
from agent.workspace.workspace import Workspace
from agent.event_bus import EventBus
from agent.security import is_internal_storage_path, is_sensitive_path, redact_sensitive_text

from .workspace_resolver import WorkspaceResolver


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

    @property
    def root(self) -> Path:
        """The immutable root owned by this scoped service."""
        return self._ensure_workspace().root

    @property
    def resolver(self) -> WorkspaceResolver:
        """Return the exact path resolver for this workspace."""
        return WorkspaceResolver(self.root)

    def resolve_path(self, path: str | Path, *, must_exist: bool = False) -> Path:
        """Resolve an exact path without fuzzy discovery or global fallback."""
        return self.resolver.resolve(path, must_exist=must_exist)

    def relative_path(self, path: str | Path) -> str:
        return self.resolver.relative(path)

    def artifact_reference(self, path: str | Path) -> str:
        """Return the canonical, workspace-relative artifact reference."""
        return self.relative_path(path)

    @staticmethod
    def _guard(path: str | Path, *, operation: str) -> None:
        if is_internal_storage_path(path):
            raise PermissionError(
                f"PROTECTED_INTERNAL_PATH: forbidden to {operation} internal storage"
            )
        if is_sensitive_path(path):
            raise PermissionError(
                f"SENSITIVE_PATH_BLOCKED: forbidden to {operation} sensitive file"
            )

    def read_text(self, path: str | Path) -> str:
        """Read a user text artifact from this Run's workspace only."""
        self._guard(path, operation="read")
        full = self.resolve_path(path, must_exist=True)
        self._guard(full, operation="read")
        if full.is_dir():
            raise IsADirectoryError(str(path))
        try:
            text = full.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(
                f"cannot decode workspace file as text: {path}"
            ) from exc
        self.record_open(self.relative_path(full))
        return redact_sensitive_text(text)

    def write_text(
        self,
        path: str | Path,
        content: str,
        *,
        mode: str = "overwrite",
    ) -> str:
        """Write a text artifact under this Run's workspace."""
        self._guard(path, operation="write")
        if Path(str(path)).suffix.lower() in {".docx", ".xlsx", ".xls", ".pptx"}:
            raise ValueError(
                "UNSUPPORTED_BINARY: Office binary files require a dedicated generator"
            )
        full = self.resolve_path(path)
        self._guard(full, operation="write")
        full.parent.mkdir(parents=True, exist_ok=True)
        if mode == "append":
            with full.open("a", encoding="utf-8") as handle:
                handle.write(str(content))
            message = f"已追加内容到 {path}"
        else:
            full.write_text(str(content), encoding="utf-8")
            message = f"已写入 {path}"
        self.record_edit(self.relative_path(full))
        return message

    def copy_file(self, source: str | Path, destination: str | Path) -> str:
        self._guard(source, operation="copy")
        self._guard(destination, operation="copy")
        source_path = self.resolve_path(source, must_exist=True)
        destination_path = self.resolve_path(destination)
        self._guard(source_path, operation="copy")
        self._guard(destination_path, operation="copy")
        if source_path == destination_path:
            raise ValueError("FILE_OPERATION_FAILED: copy source and destination match")
        if not source_path.is_file():
            raise FileNotFoundError(f"copy source is not a file: {source}")
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, destination_path)
        self.record_edit(self.relative_path(destination_path))
        return f"已复制 {source} 到 {destination}"

    def move_file(self, source: str | Path, destination: str | Path) -> str:
        self._guard(source, operation="move")
        self._guard(destination, operation="move")
        source_path = self.resolve_path(source, must_exist=True)
        destination_path = self.resolve_path(destination)
        self._guard(source_path, operation="move")
        self._guard(destination_path, operation="move")
        if source_path == destination_path:
            raise ValueError("FILE_OPERATION_FAILED: move source and destination match")
        if not source_path.is_file():
            raise FileNotFoundError(f"move source is not a file: {source}")
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source_path), str(destination_path))
        self.record_edit(self.relative_path(destination_path))
        return f"已移动 {source} 到 {destination}"

    def delete_file(self, path: str | Path) -> str:
        self._guard(path, operation="delete")
        full = self.resolve_path(path, must_exist=True)
        self._guard(full, operation="delete")
        if not full.is_file():
            raise IsADirectoryError(str(path))
        full.unlink()
        self.record_edit(self.relative_path(full))
        return f"已删除 {path}"

    def list_directory(self, path: str | Path = ".") -> str:
        self._guard(path, operation="list")
        full = self.resolve_path(path, must_exist=True)
        if not full.is_dir():
            raise NotADirectoryError(str(path))
        items = [
            f"{entry.name}{'/' if entry.is_dir() else ''}"
            for entry in full.iterdir()
            if not is_sensitive_path(entry)
        ]
        display = self.relative_path(full)
        return f"📁 {display}/ ({len(items)} 项)\n" + "\n".join(sorted(items))

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
