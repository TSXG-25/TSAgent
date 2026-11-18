"""Run-scoped workspace change evidence.

A run snapshots the bound workspace at admission (``snapshot_workspace``)
and again at terminal time.  ``diff_workspace`` classifies the delta so the
Service can show what actually changed without inventing attribution:

- files changed during the run that were clean at baseline → ``agent_files``
- files already dirty at baseline that changed again → ``ambiguous`` —
  the run touched them but the pre-existing user edit cannot be separated
- files dirty at baseline and untouched → ``user_dirty_preserved`` — proof
  the run left pre-existing user changes alone

Unified diff text comes from difflib against retained baseline content, so
git and non-git workspaces take the same path.  Git is used only for
``status --porcelain`` dirty detection.  Nothing here mutates the workspace:
no init, no stash, no reset.
"""
from __future__ import annotations

import difflib
import hashlib
import subprocess
from pathlib import Path
from typing import Any

_SKIP_DIRS = {
    ".git", "__pycache__", ".tsagent", "node_modules", ".venv", "venv",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
    "dist", "build", "htmlcov", ".next", ".nuxt", "coverage",
}
_MAX_FILES = 20000
_HASH_CAP = 8 * 1024 * 1024          # files larger than this are size/mtime-only
_TEXT_CAP = 64 * 1024                # baseline content retained only below this
_CONTENT_BUDGET = 4 * 1024 * 1024    # total retained baseline content
_DIFF_CAP_PER_FILE = 4000
_DIFF_CAP_TOTAL = 20000
_DIFF_FILE_CAP = 40


def _is_text(data: bytes) -> bool:
    return b"\x00" not in data[:8192]


def _iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        try:
            if not path.is_file() or path.is_symlink():
                continue
        except OSError:
            continue
        rel = path.relative_to(root).as_posix()
        if any(part in _SKIP_DIRS for part in rel.split("/")):
            continue
        yield path, rel


def _git_dirty(root: Path) -> tuple[set[str], bool]:
    """Pre-existing dirty/untracked paths per ``git status --porcelain``."""
    if not (root / ".git").exists():
        return set(), False
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain=v1",
             "--untracked-files=all"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return set(), True
    if proc.returncode != 0:
        return set(), True
    dirty: set[str] = set()
    for line in proc.stdout.splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        dirty.add(path.strip('"'))
    return dirty, True


def _root_path(root: Any) -> Path:
    """WorkspaceService has ``.root``; a plain Path/str is used as-is.

    Do not ``getattr(root, "root", root)`` — ``Path.root`` exists and is the
    anchor ``/``, which would turn a Path input into a whole-filesystem walk.
    """
    if isinstance(root, (str, Path)):
        return Path(root)
    return Path(root.root)


def snapshot_workspace(root: Any) -> dict[str, Any]:
    """Hash-snapshot every workspace file plus bounded text content.

    Retained ``content`` enables unified diffs for non-git workspaces; it is
    capped per file and in total, and anything beyond the cap still gets a
    hash entry so changes are detected (only the diff text is unavailable).
    """
    root_path = _root_path(root)
    files: dict[str, dict[str, Any]] = {}
    content: dict[str, str] = {}
    budget = _CONTENT_BUDGET
    truncated = False
    count = 0
    for path, rel in _iter_files(root_path):
        count += 1
        if count > _MAX_FILES:
            truncated = True
            break
        try:
            stat = path.stat()
            if stat.st_size > _HASH_CAP:
                files[rel] = {"size": stat.st_size, "mtime": stat.st_mtime_ns}
                continue
            data = path.read_bytes()
        except OSError:
            continue
        files[rel] = {
            "size": len(data),
            "sha": hashlib.sha256(data).hexdigest()[:16],
        }
        if len(data) <= _TEXT_CAP and _is_text(data) and budget > 0:
            text = data.decode("utf-8", errors="replace")
            budget -= len(text)
            content[rel] = text
    dirty, has_git = _git_dirty(root_path)
    return {
        "root": str(root_path),
        "files": files,
        "content": content,
        "dirty": sorted(dirty),
        "git": has_git,
        "truncated": truncated,
    }


def _read_text(root: Path, rel: str, cap: int) -> str | None:
    try:
        data = (root / rel).read_bytes()
    except OSError:
        return None
    if len(data) > cap or not _is_text(data):
        return None
    return data.decode("utf-8", errors="replace")


def _unified(rel: str, old: str, new: str) -> str:
    return "".join(difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"a/{rel}",
        tofile=f"b/{rel}",
        n=3,
    ))[:_DIFF_CAP_PER_FILE]


def diff_workspace(
    before: dict[str, Any], after: dict[str, Any]
) -> dict[str, Any]:
    """Classify the delta between two ``snapshot_workspace`` results."""
    b_files: dict[str, Any] = before.get("files", {})
    a_files: dict[str, Any] = after.get("files", {})
    dirty_at_start = set(before.get("dirty") or ())
    b_content: dict[str, str] = before.get("content", {})
    root = Path(str(after.get("root") or before.get("root") or "."))

    added = sorted(set(a_files) - set(b_files))
    deleted = sorted(set(b_files) - set(a_files))
    modified = sorted(
        path for path in a_files.keys() & b_files.keys()
        if a_files[path] != b_files[path]
    )
    changed = set(added) | set(modified) | set(deleted)
    agent_files = sorted(path for path in changed if path not in dirty_at_start)
    ambiguous = sorted(path for path in changed if path in dirty_at_start)
    user_preserved = sorted(path for path in dirty_at_start if path not in changed)

    diffs: dict[str, str] = {}
    diff_truncated = before.get("truncated") or after.get("truncated")
    budget = _DIFF_CAP_TOTAL
    for rel in [*agent_files, *ambiguous][:_DIFF_FILE_CAP]:
        if budget <= 0:
            diff_truncated = True
            break
        old = b_content.get(rel)
        new = _read_text(root, rel, _TEXT_CAP) if rel not in deleted else ""
        if old is None and rel not in added:
            diff_truncated = True  # baseline content was over the retention cap
            continue
        if new is None and rel not in deleted:
            diff_truncated = True  # now binary/oversized — no text diff
            continue
        text = _unified(rel, old or "", new or "")
        if not text:
            continue
        diffs[rel] = text
        budget -= len(text)

    return {
        "git": bool(after.get("git") or before.get("git")),
        "added": added,
        "modified": modified,
        "deleted": deleted,
        "agent_files": agent_files,
        "ambiguous_files": ambiguous,
        "user_dirty_preserved": user_preserved,
        "diff": diffs,
        "diff_truncated": bool(diff_truncated),
    }
