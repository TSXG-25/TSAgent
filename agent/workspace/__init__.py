"""Workspace Layer — Project Context & Path Discovery.

Workspace is a pure Discovery Layer. It never reads or writes files.
It only knows WHERE files are, not WHAT they contain.

Public API (frozen):
    Workspace.resolve(spec) -> list[PathMatch]
    Workspace.find(name) -> list[PathMatch]
    Workspace.lookup(path) -> Optional[FileNode]
    Workspace.current_context() -> WorkspaceContext
    Workspace.refresh() -> None
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


class MatchSource(Enum):
    """Source of a path match, ordered by priority (highest first)."""
    EXACT = "exact"
    SYMBOL = "symbol"
    PREFIX = "prefix"
    RECENT = "recent"
    FUZZY = "fuzzy"


@dataclass(order=True)
class PathMatch:
    """Result of a path resolution query.

    Sorted by: score DESC → source priority → path ASC.
    """
    path: Path
    score: float = 0.0
    source: MatchSource = MatchSource.FUZZY
    reason: str = ""
    node: Optional["FileNode"] = None


@dataclass
class SymbolInfo:
    """A single symbol (class/function/method) extracted from a file."""
    name: str
    kind: str          # "class", "function", "method"
    line: int = 0
    signature: Optional[str] = None


@dataclass
class FileNode:
    """Ultra-light file metadata. No summary, no embedding.

    This is the ONLY data structure in ProjectIndex.
    """
    path: str                      # relative to workspace root
    mtime: float
    size: int
    language: str                  # "python", "markdown", "text", ...
    symbols: list[SymbolInfo] = field(default_factory=list)
    hash: str = ""                 # content hash for incremental detection


@dataclass
class WorkspaceContext:
    """Current session context within a workspace.

    This tells the Planner "what is the user looking at right now."
    """
    current_file: Optional[str] = None     # most recently opened file
    opened_files: list[str] = field(default_factory=list)
    edited_files: list[str] = field(default_factory=list)
    current_symbol: Optional[str] = None   # most recently referenced symbol
    recent_symbols: list[str] = field(default_factory=list)  # recent symbols
    active_directory: str = ""             # relative path
    git_branch: Optional[str] = None
    last_symbol: Optional[str] = None      # legacy alias

    def record_open(self, path: str) -> None:
        self.current_file = path
        if path not in self.opened_files:
            self.opened_files.append(path)
        if len(self.opened_files) > 20:
            self.opened_files = self.opened_files[-20:]

    def record_edit(self, path: str) -> None:
        if path not in self.edited_files:
            self.edited_files.append(path)
        if len(self.edited_files) > 10:
            self.edited_files = self.edited_files[-10:]

    def record_symbol(self, symbol: str) -> None:
        """Record a symbol reference (class/function/variable name).

        Called by tools when user reads code containing specific symbols.
        """
        self.current_symbol = symbol
        self.last_symbol = symbol  # keep legacy alias in sync
        if symbol not in self.recent_symbols:
            self.recent_symbols.append(symbol)
        if len(self.recent_symbols) > 10:
            self.recent_symbols = self.recent_symbols[-10:]


@dataclass
class ResolveTrace:
    """Debug trace for a single resolve() call.

    Captures strategies used, timing, and all candidates.
    """
    input: str
    duration_ms: float = 0.0
    total_candidates: int = 0
    strategies_used: list[str] = field(default_factory=list)
    top_result: Optional[str] = None
    top_score: float = 0.0
    top_source: Optional[str] = None

    def short(self) -> str:
        """One-line summary for debug output."""
        strategies = ", ".join(self.strategies_used)
        return (
            f"Resolve({self.input!r}) → "
            f"[{self.total_candidates} candidates, "
            f"top: {self.top_result} ({self.top_score:.2f}, {self.top_source})"
            f" | strategies: {strategies}"
            f" | {self.duration_ms:.1f}ms]"
        )


class WorkspaceEvent:
    """Event types emitted via EventBus for workspace changes."""
    FILE_OPENED = "workspace:file_opened"
    FILE_CHANGED = "workspace:file_changed"
    SYMBOL_UPDATED = "workspace:symbol_updated"
    WORKSPACE_SWITCHED = "workspace:switched"
    INDEX_REBUILT = "workspace:index_rebuilt"
