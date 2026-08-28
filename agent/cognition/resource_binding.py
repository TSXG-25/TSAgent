"""Deterministic binding of explicit user resource targets.

This module carries path facts from the original request to authorization and
planning.  It does not resolve files or consult a workspace; it only preserves
what the user explicitly named.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import re


_PATH_RE = re.compile(
    r"(?<![\w/])(?:[\w.-]+/)*[\w.-]+\.(?:py|txt|md|csv|json|yaml|yml|"
    r"xlsx|xls|docx|pptx|js|ts|tsx|jsx|go|rs|java|cpp|h|sh|html|css)\b",
    re.IGNORECASE,
)
_WRITE_TARGET_RE = re.compile(
    r"(?:保存(?:到|为|成)?|写入(?:到)?|写到|输出到|落盘|另存为|写成|"
    r"生成|创建|新建|写(?:一个|一份)?(?:总结|报告|脚本|程序|文件)?\s*到)\s*"
    r"(?P<target>(?:[\w.-]+/)*[\w.-]+\.[A-Za-z0-9]+)",
    re.IGNORECASE,
)
_DIRECTORY_WRITE_RE = re.compile(
    r"(?:在|于)\s*(?P<directory>[\w./-]+)\s*(?:下|里面|中)\s*"
    r"(?:新建|创建|生成|写入|写到|保存)?\s*(?:一个|一份|文件)?\s*"
    r"(?P<filename>[\w.-]+\.[A-Za-z0-9]+)",
    re.IGNORECASE,
)


def normalize_resource_path(value: str) -> str:
    """Normalize a user-named relative path without filesystem resolution."""

    normalized = str(value or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return PurePosixPath(normalized).as_posix()


@dataclass(frozen=True)
class BoundTarget:
    """A resource target explicitly bound to the original user request."""

    operation: str
    path: str
    source: str = "explicit"


def extract_explicit_paths(text: str) -> tuple[str, ...]:
    """Return explicitly named file paths in input order."""

    bound_targets = extract_bound_targets(text)
    bound_paths = tuple(target.path for target in bound_targets)
    paths: list[str] = []
    for match in _PATH_RE.finditer(str(text or "")):
        path = normalize_resource_path(match.group(0))
        # A directory phrase such as ``在 output/ 下新建 probe.py`` contains
        # the basename as a second lexical match.  The bound target is the
        # authoritative resource fact, so do not also expose the basename.
        if "/" not in path and any(
            bound_path.endswith(f"/{path}") for bound_path in bound_paths
        ):
            continue
        if path and path.casefold() not in {item.casefold() for item in paths}:
            paths.append(path)
    for target in bound_targets:
        path = target.path
        if path and path.casefold() not in {item.casefold() for item in paths}:
            paths.append(path)
    return tuple(paths)


def extract_bound_targets(text: str) -> tuple[BoundTarget, ...]:
    """Extract explicit persistent write targets without guessing semantics."""

    value = str(text or "")
    targets: list[BoundTarget] = []

    for match in _DIRECTORY_WRITE_RE.finditer(value):
        directory = normalize_resource_path(match.group("directory")).rstrip("/")
        filename = normalize_resource_path(match.group("filename"))
        path = f"{directory}/{filename}" if directory else filename
        target = BoundTarget(operation="write", path=path)
        if target not in targets:
            targets.append(target)

    for match in _WRITE_TARGET_RE.finditer(value):
        target = BoundTarget(
            operation="write",
            path=normalize_resource_path(match.group("target")),
        )
        # The filename in a directory-scoped phrase is also matched by the
        # generic ``write ... to`` form.  Keep only the directory-bound fact.
        if any(
            target.path != existing.path
            and existing.path.endswith(f"/{target.path}")
            for existing in targets
        ):
            continue
        if target not in targets:
            targets.append(target)

    return tuple(targets)


__all__ = [
    "BoundTarget",
    "extract_bound_targets",
    "extract_explicit_paths",
    "normalize_resource_path",
]
