"""ProjectIndex — lightweight file tree scanner.

Stage 1 (fast): scans tree, mtime, size, language.
Stage 2 (async bg): extracts symbols via shallow parsing.

No summary, no embedding, no chunking.
"""
import hashlib
import os
import time
from pathlib import Path
from typing import Optional

from agent.security import is_internal_storage_path
from agent.workspace import FileNode, SymbolInfo

IGNORE_DIRS = {
    ".git", "venv", ".venv", "node_modules", "dist", "build",
    "__pycache__", ".repo_index", ".pytest_cache", ".mypy_cache",
    ".egg-info", ".sass-cache", ".DS_Store",
}

IGNORE_EXTENSIONS = {
    ".pyc", ".pyo", ".so", ".dll", ".dylib",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
    ".woff", ".woff2", ".ttf", ".eot",
    ".zip", ".tar", ".gz", ".bz2",
    ".mp3", ".mp4", ".avi", ".mov",
    ".pdf", ".doc", ".xls",
}

MAX_FILE_SIZE = 1024 * 1024  # 1MB — skip binary/large files

LANGUAGE_MAP: dict[str, str] = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".tsx": "typescriptreact",
    ".jsx": "javascriptreact",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".php": "php",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".sql": "sql",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".xml": "xml",
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
    ".less": "less",
    ".md": "markdown",
    ".rst": "restructuredtext",
    ".txt": "text",
    ".toml": "toml",
    ".ini": "ini",
    ".cfg": "ini",
    ".env": "dotenv",
    ".lock": "lock",
}


class ProjectIndex:
    """Lightweight file tree index.

    Only contains FileNode objects — no summary, no embedding.
    """

    def __init__(self, root: Path):
        self.root = root.resolve()
        self._files: dict[str, FileNode] = {}          # relative path → FileNode
        self._symbol_to_paths: dict[str, list[str]] = {}  # symbol → [paths]
        self._built = False

    # ── Public API ──

    def build(self) -> None:
        """Stage 1: fast scan — tree, mtime, size, language, hash."""
        self._files = {}
        self._symbol_to_paths = {}
        start = time.perf_counter()

        for file_path in self._iter_files():

            try:
                stat = file_path.stat()
                rel_path = str(file_path.relative_to(self.root))
                language = self._detect_language(file_path)
                content_hash = self._compute_hash(file_path, stat)

                node = FileNode(
                    path=rel_path,
                    mtime=stat.st_mtime,
                    size=stat.st_size,
                    language=language,
                    hash=content_hash,
                )
                self._files[rel_path] = node
            except (OSError, ValueError):
                continue

        elapsed = time.perf_counter() - start
        print(f"  📂 ProjectIndex: scanned {len(self._files)} files in {elapsed:.3f}s")
        self._built = True

    def build_symbols(self) -> None:
        """Stage 2: shallow symbol extraction for Python files.

        Call this in a background async task after Stage 1.
        """
        start = time.perf_counter()
        symbol_count = 0

        for node in self._files.values():
            if node.language != "python":
                continue

            full_path = self.root / node.path
            try:
                content = full_path.read_text(encoding="utf-8", errors="ignore")
                symbols = self._extract_python_symbols(content)
                if symbols:
                    node.symbols = symbols
                    for sym in symbols:
                        self._symbol_to_paths.setdefault(sym.name, []).append(node.path)
                    symbol_count += len(symbols)
            except Exception:
                continue

        elapsed = time.perf_counter() - start
        print(f"  📂 ProjectIndex: extracted {symbol_count} symbols in {elapsed:.3f}s")

    def lookup(self, path: str) -> Optional[FileNode]:
        """Look up a file by relative path (exact match only)."""
        return self._files.get(path)

    def find_by_symbol(self, symbol: str) -> list[str]:
        """Find file paths that contain a given symbol."""
        return self._symbol_to_paths.get(symbol, [])

    def all_files(self) -> list[str]:
        """Return all indexed file paths (relative)."""
        return list(self._files.keys())

    def all_directories(self) -> list[str]:
        """Return all unique directories containing indexed files."""
        dirs: set[str] = set()
        for rel_path in self._files:
            parent = Path(rel_path).parent
            if str(parent) != ".":
                dirs.add(str(parent))
        return sorted(dirs)

    def file_count(self) -> int:
        return len(self._files)

    def refresh(self) -> None:
        """Incremental refresh: only re-index changed files."""
        changed = False
        for file_path in self._iter_files():

            try:
                stat = file_path.stat()
                rel_path = str(file_path.relative_to(self.root))
                existing = self._files.get(rel_path)

                if existing and existing.mtime == stat.st_mtime and existing.size == stat.st_size:
                    continue  # unchanged

                # File changed or new
                changed = True
                language = self._detect_language(file_path)
                content_hash = self._compute_hash(file_path, stat)
                node = FileNode(
                    path=rel_path,
                    mtime=stat.st_mtime,
                    size=stat.st_size,
                    language=language,
                    hash=content_hash,
                )
                self._files[rel_path] = node
            except (OSError, ValueError):
                continue

        if changed:
            print(f"  📂 ProjectIndex: incremental refresh complete ({len(self._files)} files)")

    # ── Helpers ──

    def _iter_files(self):
        """Yield indexable files while pruning ignored directories early.

        Filtering after ``Path.rglob`` still walks every dependency tree. A
        workspace may contain a JavaScript checkout or a Python environment,
        so the indexer must prune those directories during traversal rather
        than merely discard their files after enumeration.
        """
        for directory, dirnames, filenames in os.walk(self.root, topdown=True):
            dirnames[:] = [
                name
                for name in dirnames
                if not self._should_prune_directory(Path(directory) / name)
            ]
            for name in filenames:
                file_path = Path(directory) / name
                if not self._should_ignore(file_path):
                    yield file_path

    def _should_ignore(self, path: Path) -> bool:
        if any(part in IGNORE_DIRS for part in path.parts):
            return True
        if is_internal_storage_path(path):
            return True
        if path.suffix.lower() in IGNORE_EXTENSIONS:
            return True
        try:
            if path.stat().st_size > MAX_FILE_SIZE:
                return True
        except OSError:
            return True
        return False

    def _should_prune_directory(self, path: Path) -> bool:
        """Return whether traversal should stop before entering ``path``.

        A nested Git checkout is a separate repository, not part of the
        current project's source graph. Pruning it avoids walking vendored
        harnesses and their dependency trees while preserving normal files in
        the current workspace.
        """
        if self._should_ignore(path):
            return True
        return (path / ".git").exists()

    def _detect_language(self, path: Path) -> str:
        return LANGUAGE_MAP.get(path.suffix.lower(), "text")

    def _compute_hash(self, path: Path, stat: os.stat_result) -> str:
        """Fast hash: only first 4KB + last 4KB for large files."""
        try:
            if stat.st_size > 65536:
                with open(path, "rb") as f:
                    head = f.read(4096)
                    f.seek(-4096, 2)
                    tail = f.read(4096)
                return hashlib.md5(head + tail).hexdigest()[:16]
            with open(path, "rb") as f:
                return hashlib.md5(f.read()).hexdigest()[:16]
        except Exception:
            return ""

    def _extract_python_symbols(self, content: str) -> list[SymbolInfo]:
        """Shallow Python symbol extraction (no AST)."""
        symbols: list[SymbolInfo] = []
        for i, line in enumerate(content.splitlines(), 1):
            stripped = line.strip()

            if stripped.startswith("class "):
                name = stripped.replace("class ", "").split("(")[0].replace(":", "").strip()
                symbols.append(SymbolInfo(name=name, kind="class", line=i))

            elif stripped.startswith("def "):
                name = stripped.replace("def ", "").split("(")[0].strip()
                # Detect method vs function by indentation
                indent = len(line) - len(line.lstrip())
                kind = "method" if indent > 0 else "function"
                sig_end = stripped.find("):")
                signature = stripped[:sig_end + 2] if sig_end > 0 else None
                symbols.append(SymbolInfo(name=name, kind=kind, line=i, signature=signature))

            elif stripped.startswith("async def "):
                name = stripped.replace("async def ", "").split("(")[0].strip()
                indent = len(line) - len(line.lstrip())
                kind = "method" if indent > 0 else "function"
                symbols.append(SymbolInfo(name=name, kind=kind, line=i))

        return symbols
