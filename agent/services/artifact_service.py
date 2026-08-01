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
from typing import Any, Dict, List, Optional

from agent.workflow.artifact import Artifact


class ArtifactService:
    """全局 Artifact Store（操作 workflow.Artifact）。

    特点：
    - content 不强制入内存（用 storage_uri 引用外部存储）
    - LLM 上下文只传 summary
    - parents 链支持 Trace / Citation / Rollback
    """

    _store: Dict[str, Artifact] = {}

    @classmethod
    def put(
        cls,
        artifact_type: str,
        storage_uri: str = "",
        summary: str = "",
        metadata: Optional[Dict] = None,
        visibility: str = "intermediate",
        parents: Optional[List[Artifact]] = None,
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
        entry = Artifact(
            id=f"artifact-{uuid.uuid4().hex[:8]}",
            type=artifact_type,
            content=kwargs.get("content", ""),
            summary=summary,
            storage_uri=storage_uri,
            metadata=metadata or {},
            created_by=kwargs.get("created_by", ""),
            timestamp=kwargs.get("timestamp", time.time()),
            parents=list(parents or []),
            visibility=visibility,
        )
        cls._store[entry.id] = entry
        return entry.id

    @classmethod
    def get(cls, artifact_id: str) -> Optional[Artifact]:
        return cls._store.get(artifact_id)

    @classmethod
    def get_summary(cls, artifact_id: str) -> str:
        """获取摘要（给 LLM 上下文使用，不加载内容）。"""
        entry = cls._store.get(artifact_id)
        if not entry:
            return ""
        return f"[{entry.type}] {entry.summary}"

    @classmethod
    def get_final_artifacts(cls) -> List[Artifact]:
        """获取所有 final 可见性的 Artifact（给 Answer Generator）。"""
        return [e for e in cls._store.values() if e.visibility == "final"]

    @classmethod
    def get_by_task(cls, task_id: str) -> List[Artifact]:
        """按 task_id 查询（通过 metadata 中的 task_id）。"""
        return [e for e in cls._store.values() if e.metadata.get("task_id") == task_id]

    @classmethod
    def get_by_type(cls, artifact_type: str) -> List[Artifact]:
        """按类型查询。"""
        return [e for e in cls._store.values() if e.type == artifact_type]

    @classmethod
    def clear(cls):
        cls._store.clear()