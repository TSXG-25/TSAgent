# agent/workflow/artifact.py
"""Artifact — 工作流数据模型。

- Artifact: 运行时数据单元（包含 content, summary, metadata, parents）
- InputArtifact: Stage 的输入声明
- OutputArtifact: Stage 的输出声明
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Artifact:
    """运行时数据单元。
    
    Workflow 执行过程中产生的数据。
    支持 parents 链追踪，实现 Trace / Citation / Rollback。
    
    Attributes:
        id: 唯一标识（自动生成或外部传入）
        type: 类型标识（如 "question_text", "algorithm_design"）
        content: 内容（文本、代码、结构化数据等）
        summary: 简要摘要（供 LLM 快速引用）
        storage_uri: 外部存储引用（文件路径, memory key, 或空）
        metadata: 额外信息
        created_by: 创建该 Artifact 的 stage id
        timestamp: 创建时间
        parents: 父 Artifact 列表（溯源链）
        visibility: 可见性（temporary / intermediate / final）
    """
    id: str
    type: str
    content: Any = ""
    summary: str = ""
    storage_uri: str = ""
    metadata: Optional[Dict] = None
    created_by: str = ""
    timestamp: float = 0.0
    parents: List["Artifact"] = field(default_factory=list)
    visibility: str = "intermediate"

    def add_parent(self, parent: "Artifact"):
        """添加父 Artifact，建立溯源链。"""
        if parent not in self.parents:
            self.parents.append(parent)

    def trace(self) -> List["Artifact"]:
        """递归溯源，返回从根到当前的所有 Artifact。"""
        chain = []
        for p in self.parents:
            chain.extend(p.trace())
        chain.append(self)
        return chain

    def find_root(self) -> "Artifact":
        """找到根 Artifact（最上游来源）。"""
        if not self.parents:
            return self
        return self.parents[0].find_root()


@dataclass
class InputArtifact:
    """Stage 的输入声明。
    
    声明某个 Stage 需要依赖哪个类型的 Artifact。
    """
    type: str
    description: str = ""
    optional: bool = False


@dataclass
class OutputArtifact:
    """Stage 的输出声明。
    
    声明某个 Stage 会产出什么类型的 Artifact。
    
    Attributes:
        type: 产出 Artifact 的类型
        description: 描述
        persist: 是否持久化到 ArtifactService（否则仅运行时持有）
    """
    type: str
    description: str = ""
    persist: bool = True