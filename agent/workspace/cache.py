"""FileCache — LRU content cache for file contents.

This is a passive cache: it only stores content that has been read
through it. It does NOT initiate file I/O on its own.

Workspace Facade does not expose cache operations directly.
Cache is only used internally by Workspace to accelerate repeated reads.
"""
import time
from collections import OrderedDict
from pathlib import Path
from typing import Optional


class FileCache:
    """LRU file content cache.

    Configurable max entries and max size per entry.
    Thread-safe for single-threaded async use.
    """

    def __init__(self, max_entries: int = 100, max_entry_size: int = 512 * 1024):
        self._max_entries = max_entries
        self._max_entry_size = max_entry_size
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._hits = 0
        self._misses = 0

    def get(self, path: str) -> Optional[str]:
        """Get cached content for a path. Returns None on miss."""
        if path in self._cache:
            entry = self._cache.pop(path)  # move to end (most recent)
            if not self._is_expired(entry):
                self._cache[path] = entry
                self._hits += 1
                return entry.content
            else:
                # Expired, don't put back
                self._misses += 1
                return None
        self._misses += 1
        return None

    def set(self, path: str, content: str, ttl_seconds: float = 300.0) -> None:
        """Store content in cache.

        Args:
            path: relative or absolute file path (used as key)
            content: file content string
            ttl_seconds: time-to-live (default 5 minutes)
        """
        if len(content.encode("utf-8")) > self._max_entry_size:
            return  # Don't cache large files

        # Evict if at capacity
        while len(self._cache) >= self._max_entries:
            self._cache.popitem(last=False)

        self._cache[path] = CacheEntry(
            content=content,
            cached_at=time.monotonic(),
            ttl=ttl_seconds,
        )

    def invalidate(self, path: str) -> None:
        """Remove a single entry from cache."""
        self._cache.pop(path, None)

    def invalidate_prefix(self, prefix: str) -> None:
        """Remove all entries starting with a prefix."""
        keys = [k for k in self._cache if k.startswith(prefix)]
        for k in keys:
            self._cache.pop(k, None)

    def clear(self) -> None:
        """Clear entire cache."""
        self._cache.clear()

    def stats(self) -> dict:
        """Return cache statistics."""
        total = self._hits + self._misses
        hit_rate = self._hits / total if total > 0 else 0.0
        return {
            "entries": len(self._cache),
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(hit_rate, 3),
            "max_entries": self._max_entries,
        }

    # ── Internal ──

    def _is_expired(self, entry: "CacheEntry") -> bool:
        return (time.monotonic() - entry.cached_at) > entry.ttl


class CacheEntry:
    __slots__ = ("content", "cached_at", "ttl")

    def __init__(self, content: str, cached_at: float, ttl: float):
        self.content = content
        self.cached_at = cached_at
        self.ttl = ttl