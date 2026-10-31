"""Path normalization helpers shared by intent and authorization."""
from pathlib import PurePosixPath


TEXT_OUTPUT_SUFFIXES = (
    ".py", ".txt", ".md", ".json", ".csv", ".yaml", ".yml",
    ".js", ".ts", ".rs", ".go", ".java", ".sh", ".html", ".css",
)


def normalize_resource_path(value: str) -> str:
    """Normalize a user-named relative path without filesystem resolution."""

    normalized = str(value or "").strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return PurePosixPath(normalized).as_posix()


__all__ = ["TEXT_OUTPUT_SUFFIXES", "normalize_resource_path"]
