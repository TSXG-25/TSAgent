"""Artifact Store — 统一产出存储（基于 workflow.Artifact）。

Artifact 是唯一数据模型（agent/workflow/artifact.py，支持 parents 溯源链）。
本 Service 提供跨模块的存储与查询：
- 按 task_id / type / visibility 查询
- 摘要引用（不加载 content 大对象）

Observation 只存 artifact_id 和 summary，不存大段内容。
LLM 需要时才通过 ArtifactService.load() 加载具体内容。
"""
import time
import uuid
import threading
from typing import Any, Dict, List, Optional

from agent.workflow.artifact import Artifact


class ArtifactScopeClosedError(RuntimeError):
    """Raised when a closed run-scoped artifact store is used."""


class ArtifactStore:
    """Instance-scoped Artifact Store.

    A store belongs to exactly one ``RunContext``.  The old class-level
    ``ArtifactService`` facade remains below for compatibility with legacy
    callers, but new runtime code must hold an explicit ``ArtifactStore``.

    特点：
    - content 不强制入内存（用 storage_uri 引用外部存储）
    - LLM 上下文只传 summary
    - parents 链支持 Trace / Citation / Rollback
    """

    def __init__(self, *, scope_id: str = "") -> None:
        self.scope_id = str(scope_id or "")
        self._store: Dict[str, Artifact] = {}
        self._closed = False
        self._lock = threading.RLock()

    @property
    def closed(self) -> bool:
        return self._closed

    def _ensure_open(self) -> None:
        with self._lock:
            if self._closed:
                raise ArtifactScopeClosedError(
                    f"artifact store is closed: {self.scope_id or '<unnamed>'}"
                )

    def put(
        self,
        artifact_type: str,
        storage_uri: str = "",
        summary: str = "",
        metadata: Optional[Dict] = None,
        visibility: str = "intermediate",
        parents: Optional[List[Artifact]] = None,
        artifact_id: Optional[str] = None,
        key: Optional[str] = None,
        **kwargs,
    ) -> str:
        """存入一个 Artifact，返回 ID。

        Args:
            artifact_type: 类型（code_snippet, search_result, patch, report, ...）
            storage_uri: 外部存储路径（如 workspace://login.py）
            summary: LLM 可直接读的摘要
            metadata: 额外信息（task_id 等）
            visibility: intermediate/final/temporary
            parents: 父 Artifact 列表（溯源链）
            **kwargs: 兼容字段（content, created_by, timestamp）
        """
        with self._lock:
            self._ensure_open()
            artifact_metadata = dict(metadata or {})
            if self.scope_id:
                artifact_metadata.setdefault("scope_id", self.scope_id)
            entry = Artifact(
                id=artifact_id or key or f"artifact-{uuid.uuid4().hex[:8]}",
                type=artifact_type,
                content=kwargs.get("content", ""),
                summary=summary,
                storage_uri=storage_uri,
                metadata=artifact_metadata,
                created_by=kwargs.get("created_by", ""),
                timestamp=kwargs.get("timestamp", time.time()),
                parents=list(parents or []),
                visibility=visibility,
            )
            self._store[entry.id] = entry
            return entry.id

    def get(self, artifact_id: str) -> Optional[Artifact]:
        with self._lock:
            self._ensure_open()
            return self._store.get(artifact_id)

    def get_summary(self, artifact_id: str) -> str:
        """获取摘要（给 LLM 上下文使用，不加载内容）。"""
        entry = self.get(artifact_id)
        if not entry:
            return ""
        return f"[{entry.type}] {entry.summary}"

    def get_final_artifacts(self) -> List[Artifact]:
        """获取所有 final 可见性的 Artifact（给 Answer Generator）。"""
        with self._lock:
            self._ensure_open()
            return [e for e in self._store.values() if e.visibility == "final"]

    def get_by_task(self, task_id: str) -> List[Artifact]:
        """按 task_id 查询（通过 metadata 中的 task_id）。"""
        with self._lock:
            self._ensure_open()
            return [e for e in self._store.values() if e.metadata.get("task_id") == task_id]

    def get_by_type(self, artifact_type: str) -> List[Artifact]:
        """按类型查询。"""
        with self._lock:
            self._ensure_open()
            return [e for e in self._store.values() if e.type == artifact_type]

    def items(self) -> List[Artifact]:
        """Return a snapshot of all artifacts in this scope."""
        with self._lock:
            self._ensure_open()
            return list(self._store.values())

    def clear(self) -> None:
        with self._lock:
            self._ensure_open()
            self._store.clear()

    def close(self) -> None:
        """Close this scope without purging its stored artifacts.

        Durable implementations may flush before returning. Purging is an
        explicit ``destroy`` operation so a later resume can reuse the same
        logical run id and storage view.
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True

    def destroy(self) -> None:
        """Explicitly purge this artifact scope."""
        with self._lock:
            self._store.clear()
            self._closed = True


class ArtifactService:
    """Legacy process-global facade.

    This facade is retained only for old tests and compatibility imports.
    Runtime Context code must use ``ArtifactStore`` so one Run cannot clear
    another Run's artifacts.
    """

    _legacy_store = ArtifactStore(scope_id="legacy")
    # Kept for old ContextService code that still reads this compatibility
    # attribute. New code must use ArtifactStore.items().
    _store = _legacy_store._store

    @classmethod
    def put(cls, *args, **kwargs) -> str:
        return cls._legacy_store.put(*args, **kwargs)

    @classmethod
    def get(cls, artifact_id: str) -> Optional[Artifact]:
        return cls._legacy_store.get(artifact_id)

    @classmethod
    def get_summary(cls, artifact_id: str) -> str:
        return cls._legacy_store.get_summary(artifact_id)

    @classmethod
    def get_final_artifacts(cls) -> List[Artifact]:
        return cls._legacy_store.get_final_artifacts()

    @classmethod
    def get_by_task(cls, task_id: str) -> List[Artifact]:
        return cls._legacy_store.get_by_task(task_id)

    @classmethod
    def get_by_type(cls, artifact_type: str) -> List[Artifact]:
        return cls._legacy_store.get_by_type(artifact_type)

    @classmethod
    def clear(cls) -> None:
        cls._legacy_store.clear()


__all__ = ["ArtifactScopeClosedError", "ArtifactService", "ArtifactStore"]
