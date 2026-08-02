"""Grounding — 检索层（Retrieval / Grounding Layer）。

ADR-0004: Grounder reduces the search space, not the decision space。
Grounder 给出 Top-N 候选（Candidate），决策权在 Planner。

- GroundingInput: 平铺输入（不依赖巨型 Context）
- Grounder:      ground(input, budget) -> GroundingResult
- GroundingResult: context + stats + trace（trace 属 Runtime）
"""
from agent.grounding.context import (
    Candidate, GroundingContext, GroundingResult, GroundingStats,
    GroundingTrace, GroundingBudget,
)
from agent.grounding.grounder import Grounder, GroundingInput

__all__ = [
    "Candidate", "GroundingContext", "GroundingResult", "GroundingStats",
    "GroundingTrace", "GroundingBudget", "Grounder", "GroundingInput",
]
