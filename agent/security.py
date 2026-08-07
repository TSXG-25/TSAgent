"""Shared security guards for workspace and process-boundary tools.

The agent may inspect and execute user-scoped project content, but it must not
turn credentials into model-visible output.  Keep these checks small and
dependency-free so every IO boundary can use the same policy.
"""
from __future__ import annotations

import re
from pathlib import Path


_SENSITIVE_BASENAMES = {
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "credentials",
    "credentials.json",
    "secrets",
    "secrets.json",
    "secret.json",
}
_SENSITIVE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".jks"}
_INTERNAL_STORAGE_DIRS = {
    ".repo_index",
    ".tsagent",
    "semantic_memory",
    "long_term_memory",
    "short_term",
    "runtime_store",
    "checkpoint_store",
    "checkpoints",
}
_INTERNAL_STORAGE_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".db-wal", ".db-shm"}


def is_sensitive_path(path: str | Path) -> bool:
    """Return whether a path commonly contains credentials or private keys."""
    raw = str(path).replace("\\", "/")
    parts = [part.lower() for part in Path(raw).parts]
    basename = Path(raw).name.lower()

    if basename == ".env" or basename.startswith(".env."):
        return True
    if basename in _SENSITIVE_BASENAMES or basename.endswith(tuple(_SENSITIVE_SUFFIXES)):
        return True
    if any(part in {".ssh", ".aws", ".azure", ".kube"} for part in parts):
        return True
    return bool(
        re.search(
            r"(?:secret|secrets|credential|credentials|password|token)(?:[._-]|$)",
            basename,
        )
    )


def is_internal_storage_path(path: str | Path) -> bool:
    """Return whether a path belongs to Agent-owned persistence.

    These files are implementation state, not user Workspace artifacts.  A
    normal file-read task must not expose or decode them; dedicated memory and
    runtime-store APIs are the only supported access paths.
    """
    raw = str(path).replace("\\", "/")
    parts = {part.lower() for part in Path(raw).parts}
    basename = Path(raw).name.lower()
    if parts & _INTERNAL_STORAGE_DIRS:
        return True
    return basename.endswith(tuple(_INTERNAL_STORAGE_SUFFIXES))


_SENSITIVE_VALUE_PATTERNS = (
    re.compile(
        r"(?i)(\b(?:openai|deepseek|anthropic|google)?[_-]?api[_-]?key\s*[:=]\s*)([^\s,;\"']+)"
    ),
    re.compile(
        r"(?i)(\b(?:secret|access[_-]?token|password|auth[_-]?token)\s*[:=]\s*)([^\s,;\"']+)"
    ),
    re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"(?i)\b(?:bearer|token)\s+[A-Za-z0-9._~-]{16,}\b"),
)


def redact_sensitive_text(text: str) -> str:
    """Redact common credential formats from tool output."""
    result = str(text)
    for pattern in _SENSITIVE_VALUE_PATTERNS:
        if pattern.groups >= 2:
            result = pattern.sub(r"\1[REDACTED]", result)
        else:
            result = pattern.sub("[REDACTED]", result)
    return result


def is_sensitive_command(command: str) -> bool:
    """Return whether a shell/Python command attempts credential disclosure."""
    normalized = str(command).lower()
    if any(name in normalized for name in (
        "$openai_api_key",
        "${openai_api_key}",
        "$deepseek_api_key",
        "${deepseek_api_key}",
        "printenv",
        "openai_api_key",
        "deepseek_api_key",
    )):
        return True
    if re.search(r"(?:^|[\s;&|])env(?:\s|$|[;&|])", normalized):
        return True
    if ".env" in normalized:
        return True
    if re.search(r"(?:secret|secrets|credential|credentials|password|token)[._/-]", normalized):
        return True
    return False
