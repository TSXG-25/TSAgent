"""Workspace Facade — the only public API for project context.

This class is a pure Discovery Layer. It never reads or writes files.
It only resolves, finds, and looks up metadata about files.

Public API (frozen):
    resolve(spec) -> list[PathMatch]
    find(name) -> list[PathMatch]
    lookup(path) -> Optional[FileNode]
    current_context() -> WorkspaceContext
    refresh() -> None
"""
from pathlib import Path
from typing import Optional

from agent.workspace import FileNode, PathMatch, MatchSource, WorkspaceContext, WorkspaceEvent, ResolveTrace
from agent.workspace.index import ProjectIndex
from agent.workspace.resolver import PathResolver
from agent.workspace.cache import FileCache
from agent.event_bus import event_bus


class Workspace:
    """Workspace Facade — project context and path discovery.

    Never performs file I/O. Delegates to ProjectIndex and PathResolver.
    """

    def __init__(self, root: Path):
        self._root = root.resolve()

        # Internal components
        self._index = ProjectIndex(self._root)
        self._cache = FileCache()
        self._context = WorkspaceContext()

        # Resolver with callbacks into this workspace state
        self._resolver = PathResolver(
            root=self._root,
            get_index_files=lambda: self._index.all_files(),
            get_recent_files=lambda: self._context.opened_files,
            get_symbol_paths=lambda s: self._index.find_by_symbol(s),
            lookup_file=lambda p: self._index.lookup(p),
        )

        # Track if Stage 2 (symbols) has been built
        self._symbols_built = False

    # ── Public API ──

    @property
    def root(self) -> Path:
        return self._root

    def resolve(self, spec: str) -> list[PathMatch]:
        """Resolve a path spec string to ranked candidate PathMatch list.

        Combines exact → recent → symbol → prefix → fuzzy matching.
        Sorted by score DESC → source priority → path ASC.
        """
        return self._resolver.resolve(spec)

    def find(self, name: str) -> list[PathMatch]:
        """Find files by name (fuzzy filename match).

        Equivalent to resolve() but explicitly for filename search.
        """
        return self._resolver.resolve(name)

    def lookup(self, path: str) -> Optional[FileNode]:
        """Look up a file by relative path. Returns FileNode if indexed.

        Only matches exact relative paths (e.g. 'agent/runtime.py').
        For fuzzy matching, use resolve() or find().
        """
        return self._index.lookup(path)

    def file_count(self) -> int:
        """Number of files in the project index."""
        return self._index.file_count()

    def indexed_files(self) -> list[str]:
        """所有已索引文件的相对路径列表（Grounding 层检索用）。"""
        return self._index.all_files()

    def current_context(self) -> WorkspaceContext:
        """Get the current session context.

        Contains: current_file, opened_files, edited_files, active_directory, etc.
        """
        return self._context

    def refresh(self) -> None:
        """Incremental refresh: scan for changed files."""
        self._index.refresh()
        event_bus.emit(WorkspaceEvent.INDEX_REBUILT, {"root": str(self._root)})

    # ── Lifecycle ──

    def build_index(self) -> None:
        """Stage 1: fast file tree scan."""
        self._index.build()
        event_bus.emit(WorkspaceEvent.INDEX_REBUILT, {"root": str(self._root)})

    async def build_symbols_async(self) -> None:
        """Stage 2: extract symbols in background.

        Call this after build_index() in an async task.
        Does not block startup — symbols are a slow enrichment.
        """
        if self._symbols_built:
            return
        self._index.build_symbols()
        self._symbols_built = True
        event_bus.emit(WorkspaceEvent.SYMBOL_UPDATED, {"root": str(self._root)})

    # ── Context tracking (called by tools) ──

    def record_open(self, path: str) -> None:
        """Record a file open event (called by filesystem tool)."""
        self._context.record_open(path)
        event_bus.emit(WorkspaceEvent.FILE_OPENED, {"path": path, "root": str(self._root)})

    def record_edit(self, path: str) -> None:
        """Record a file edit event (called by filesystem tool)."""
        self._context.record_edit(path)
        event_bus.emit(WorkspaceEvent.FILE_CHANGED, {"path": path, "root": str(self._root)})
        self._cache.invalidate(path)  # clear cached content on edit

    def record_symbol(self, symbol: str) -> None:
        """Record a symbol reference (class/function/variable name).

        Called by tools when code reading reveals specific symbols.
        """
        self._context.record_symbol(symbol)
        event_bus.emit(WorkspaceEvent.SYMBOL_UPDATED, {"symbol": symbol, "root": str(self._root)})

    # ── Trace ──

    def enable_trace(self, enabled: bool = True) -> None:
        """Enable debug tracing for resolve() calls."""
        self._resolver.enable_trace(enabled)

    def last_trace(self) -> Optional[ResolveTrace]:
        """Get the last resolve() trace, or None if tracing is off."""
        return self._resolver.last_trace()

    # ── Related files ──

    def related(self, path: str) -> list[PathMatch]:
        """Find files related to a given file.

        Heuristics (in order):
        1. Same directory siblings
        2. Files that share symbols
        3. Test files (test_<name>.py or <name>_test.py)

        Args:
            path: relative file path (e.g. 'agent/runtime.py')

        Returns:
            Sorted list of related PathMatch objects
        """
        node = self._index.lookup(path)
        if node is None:
            return []

        related_set: dict[str, float] = {}
        rel_path = Path(path)

        # 1. Same directory siblings
        sibling_dir = str(rel_path.parent) if str(rel_path.parent) != "." else ""
        for fp in self._index.all_files():
            if fp == path:
                continue
            fp_path = Path(fp)
            if sibling_dir and str(fp_path.parent) == sibling_dir:
                related_set[fp] = max(related_set.get(fp, 0), 0.8)
            # Same stem (e.g. runtime.py ↔ runtime_test.py)
            if fp_path.stem == rel_path.stem:
                related_set[fp] = max(related_set.get(fp, 0), 0.9)

        # 2. Files that share symbols
        if node.symbols:
            for sym in node.symbols:
                symbol_paths = self._index.find_by_symbol(sym.name)
                for sp in symbol_paths:
                    if sp != path:
                        related_set[sp] = max(related_set.get(sp, 0), 0.7)

        # 3. Test files
        stem = rel_path.stem
        test_patterns = [f"test_{stem}.py", f"{stem}_test.py", f"test_{stem}", f"{stem}_test"]
        for fp in self._index.all_files():
            fp_stem = Path(fp).stem.lower()
            for pattern in test_patterns:
                if fp_stem == pattern or fp_stem.startswith(pattern):
                    related_set[fp] = max(related_set.get(fp, 0), 0.85)

        # Convert to PathMatch list
        root = self._root
        results = []
        for rel, score in related_set.items():
            full_path = (root / rel).resolve()
            results.append(PathMatch(
                path=full_path,
                score=score,
                source=MatchSource.PREFIX,
                reason=f"Related file: {rel}",
                node=self._index.lookup(rel),
            ))

        results.sort(key=lambda m: (-m.score, str(m.path)))
        return results

    # ── Internal accessors (used by resolver callbacks) ──

    @property
    def cache(self) -> FileCache:
        return self._cache

    def __repr__(self) -> str:
        files = self._index.file_count()
        ctx = self._context.current_file or "no file"
        return f"Workspace({self._root.name}: {files} files, current: {ctx})"