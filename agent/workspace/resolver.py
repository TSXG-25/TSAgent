"""PathResolver — resolves spec strings to PathMatch lists.

Strategies (in order of priority):
1. Exact — relative path that exists
2. Recent — recently opened files
3. Symbol — symbol name → file
4. Prefix — prefix of a known path
5. Fuzzy — filename match against all indexed files

Workspace Facade calls all strategies and merges results.
No file I/O — purely metadata-based resolution.

Trace is enabled via enable_trace() / last_trace().
"""
import time
from pathlib import Path
from typing import Optional

from agent.workspace import PathMatch, MatchSource, FileNode, ResolveTrace


class PathResolver:
    """Resolve path specifications to PathMatch candidates.

    Uses ProjectIndex for fast lookups, plus recent file tracking.
    All dependencies are injected as callbacks — no circular imports.

    Supports ResolveTrace via enable_trace().
    """

    def __init__(self, root: Path, get_index_files, get_recent_files, get_symbol_paths, lookup_file=None):
        """Initialize with callbacks into Workspace state.

        Args:
            root: workspace root Path
            get_index_files: callable() -> list[str] of relative paths
            get_recent_files: callable() -> list[str] of recent relative paths
            get_symbol_paths: callable(symbol: str) -> list[str] of paths
            lookup_file: optional callable(rel_path: str) -> Optional[FileNode]
        """
        self.root = root.resolve()
        self._get_index_files = get_index_files
        self._get_recent_files = get_recent_files
        self._get_symbol_paths = get_symbol_paths
        self._lookup_file = lookup_file or (lambda p: None)
        self._trace_enabled = False
        self._last_trace: Optional[ResolveTrace] = None

    def enable_trace(self, enabled: bool = True) -> None:
        self._trace_enabled = enabled

    def last_trace(self) -> Optional[ResolveTrace]:
        return self._last_trace

    def resolve(self, spec: str) -> list[PathMatch]:
        """Resolve a path spec string to candidate PathMatch list.

        Combines: exact → recent → symbol → prefix → fuzzy.
        Deduplicates by path, keeping highest score.
        """
        t0 = time.perf_counter()
        self._last_trace = None

        results: list[PathMatch] = []
        seen: set[str] = set()
        strategies_used: list[str] = []
        spec_normalized = spec.strip().replace("\\", "/")

        # Strategy 1: Exact match (relative to workspace root)
        exact = self._resolve_exact(spec_normalized)
        if exact:
            strategies_used.append("exact")
            for m in exact:
                if str(m.path) not in seen:
                    seen.add(str(m.path))
                    results.append(m)

        # Strategy 2: Recent files
        recent = self._resolve_recent(spec_normalized)
        if recent:
            strategies_used.append("recent")
            for m in recent:
                if str(m.path) not in seen:
                    seen.add(str(m.path))
                    results.append(m)

        # Strategy 3: Symbol match
        symbol = self._resolve_symbol(spec_normalized)
        if symbol:
            strategies_used.append("symbol")
            for m in symbol:
                if str(m.path) not in seen:
                    seen.add(str(m.path))
                    results.append(m)

        # Strategy 4: Prefix match
        prefix = self._resolve_prefix(spec_normalized)
        if prefix:
            strategies_used.append("prefix")
            for m in prefix:
                if str(m.path) not in seen:
                    seen.add(str(m.path))
                    results.append(m)

        # Strategy 5: Fuzzy filename match
        fuzzy = self._resolve_fuzzy(spec_normalized)
        if fuzzy:
            strategies_used.append("fuzzy")
            for m in fuzzy:
                if str(m.path) not in seen:
                    seen.add(str(m.path))
                    results.append(m)

        # Final sort: score DESC → source priority → path ASC
        source_priority = {s: i for i, s in enumerate(MatchSource)}
        results.sort(key=lambda m: (-m.score, source_priority.get(m.source, 99), str(m.path)))

        # Build trace
        if self._trace_enabled:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            top = results[0] if results else None
            self._last_trace = ResolveTrace(
                input=spec,
                duration_ms=round(elapsed_ms, 1),
                total_candidates=len(results),
                strategies_used=strategies_used,
                top_result=str(top.path) if top else None,
                top_score=top.score if top else 0.0,
                top_source=top.source.value if top else None,
            )

        return results

    # ── Strategy Implementations ──

    def _resolve_exact(self, spec: str) -> list[PathMatch]:
        """Check if spec is an existing relative path."""
        candidate = (self.root / spec).resolve()
        if candidate.exists() and candidate.is_file():
            try:
                rel = str(candidate.relative_to(self.root))
            except ValueError:
                rel = spec

            node = self._lookup_file(rel)

            return [PathMatch(
                path=candidate,
                score=1.0,
                source=MatchSource.EXACT,
                reason=f"Exact path: {rel}",
                node=node,
            )]
        return []

    def _resolve_recent(self, spec: str) -> list[PathMatch]:
        """Check if spec matches a recently opened file (stem or name)."""
        spec_lower = spec.lower()
        spec_stem = Path(spec).stem.lower()
        results: list[PathMatch] = []

        recent_files = self._get_recent_files()
        for recent_index, rel_path in enumerate(reversed(recent_files)):
            stem = Path(rel_path).stem.lower()
            name = Path(rel_path).name.lower()
            explicit_path = "/" in spec or "\\" in spec
            if explicit_path and Path(rel_path).as_posix().lower() != spec_lower:
                continue
            if stem == spec_lower or stem == spec_stem or name == spec_lower:
                full_path = (self.root / rel_path).resolve()
                node = self._get_index_files() and None  # will be populated if available
                results.append(PathMatch(
                    path=full_path,
                    # A recently opened bare name is stronger than an
                    # ambiguous same-stem fuzzy match, while an explicit
                    # filesystem path still uses the exact-path strategy.
                    score=1.01 - min(recent_index, 20) * 0.001,
                    source=MatchSource.RECENT,
                    reason=f"Recently opened: {rel_path}",
                ))

        return results

    def _resolve_symbol(self, spec: str) -> list[PathMatch]:
        """Check if spec matches a symbol name (class/function)."""
        paths = self._get_symbol_paths(spec)
        results: list[PathMatch] = []
        for rel_path in paths:
            full_path = (self.root / rel_path).resolve()
            results.append(PathMatch(
                path=full_path,
                score=0.9,
                source=MatchSource.SYMBOL,
                reason=f"Symbol '{spec}' found in {rel_path}",
            ))
        return results

    def _resolve_prefix(self, spec: str) -> list[PathMatch]:
        """Check if spec is a prefix of any indexed file path."""
        spec_normalized = spec.replace("\\", "/").lower()
        results: list[PathMatch] = []

        for rel_path in self._get_index_files():
            path_lower = rel_path.lower()
            if path_lower.startswith(spec_normalized) or path_lower.startswith(spec_normalized + "."):
                # Exact prefix match — high score
                full_path = (self.root / rel_path).resolve()
                score = 0.85 if path_lower == spec_normalized else 0.75
                results.append(PathMatch(
                    path=full_path,
                    score=score,
                    source=MatchSource.PREFIX,
                    reason=f"Path prefix match: {rel_path}",
                ))

        return results

    def _resolve_fuzzy(self, spec: str) -> list[PathMatch]:
        """Fuzzy filename match against all indexed files."""
        from agent.workspace.matcher import match_filename

        index_files = self._get_index_files()
        paths = [(self.root / p).resolve() for p in index_files]
        matches = match_filename(spec, paths)

        # Attach FileNode from index where possible
        for m in matches:
            try:
                rel = str(m.path.relative_to(self.root))
                m.node = self._lookup_file(rel)
            except (ValueError, AttributeError):
                pass

        return matches
