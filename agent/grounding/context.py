"""grounding context — 纯数据模型。

ADR-0004: Grounder reduces the search space, not the decision space。
Grounder 给出 Top-N 候选（Candidate），决策权在 Planner。

GroundingContext 是纯数据（Model），不负责 Prompt 渲染（View 在 PlannerPromptBuilder）。
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Candidate:
    """统一候选（file/symbol/test/artifact 共用一种结构）。

    Attributes:
        kind: "file" | "symbol" | "test" | "artifact"
        name: 路径 / 符号名 / 测试文件 / artifact 引用
        score: 相关度 (0~1)
        reason: 人类可读的命中原因
        source: 来源（workspace/repository/symbol/conversation/artifact）
        metadata: provenance（如 {"matched_symbol": "add"}）
    """
    kind: str = "file"
    name: str = ""
    score: float = 0.0
    reason: str = ""
    source: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "name": self.name,
            "score": round(self.score, 3),
            "reason": self.reason,
            "source": self.source,
            "metadata": self.metadata,
        }


@dataclass
class GroundingContext:
    """Grounding 结果（纯数据，Planner 的世界模型）。"""
    query: str = ""
    candidates: List[Candidate] = field(default_factory=list)
    conversation: List[str] = field(default_factory=list)

    @property
    def files(self) -> List[Candidate]:
        return [c for c in self.candidates if c.kind in ("file", "test")]

    @property
    def symbols(self) -> List[Candidate]:
        return [c for c in self.candidates if c.kind == "symbol"]

    def top(self, n: int = 5) -> List[Candidate]:
        return sorted(self.candidates, key=lambda c: -c.score)[:n]


@dataclass
class GroundingStats:
    """Grounding 执行统计（benchmark 直接消费）。"""
    files_searched: int = 0
    symbols_checked: int = 0
    candidate_count: int = 0
    elapsed_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "files_searched": self.files_searched,
            "symbols_checked": self.symbols_checked,
            "candidate_count": self.candidate_count,
            "elapsed_ms": round(self.elapsed_ms, 1),
        }


@dataclass
class GroundingTrace:
    """Grounding 全链路 trace（属于 Runtime，不属于 Planner）。"""
    query: str = ""
    intent_summary: str = ""
    keys_used: List[str] = field(default_factory=list)
    searched_hits: Dict[str, int] = field(default_factory=dict)   # source -> hit count
    pruned: int = 0
    top5: List[str] = field(default_factory=list)
    latency_ms: float = 0.0

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "intent_summary": self.intent_summary,
            "keys_used": self.keys_used,
            "searched_hits": self.searched_hits,
            "pruned": self.pruned,
            "top5": self.top5,
            "latency_ms": round(self.latency_ms, 1),
        }


@dataclass
class GroundingResult:
    """Grounding 输出：context + stats + trace。"""
    context: GroundingContext = field(default_factory=GroundingContext)
    stats: GroundingStats = field(default_factory=GroundingStats)
    trace: GroundingTrace = field(default_factory=GroundingTrace)


@dataclass
class GroundingBudget:
    """Grounding 资源预算（500k repo 不改 Grounder，只改 budget）。"""
    max_candidates: int = 5
    max_workspace_hits: int = 20
    max_repository_hits: int = 10
    max_latency_ms: float = 2000.0
