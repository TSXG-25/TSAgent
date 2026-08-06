"""Grounder — 检索层（Retrieval / Grounding Layer）。

ADR-0004:
- Grounder reduces the search space, not the decision space。
  只给出 Top-N 候选，决策权在 Planner，Grounder 不推理。
- Grounder 绝不执行工具 / 写文件 / Shell。
- 受 GroundingBudget 约束。

第一版只暴露 ground(input, budget) -> GroundingResult 一个稳定接口；
子检索用私有方法实现（先稳定接口，再拆实现）。
"""
import time
from dataclasses import dataclass, field
from typing import Any, List, Optional

from agent.grounding.context import (
    Candidate, GroundingContext, GroundingResult, GroundingStats,
    GroundingTrace, GroundingBudget,
)


@dataclass
class GroundingInput:
    """Grounding 输入（平铺字段，不依赖任何巨型 Context，防 God Object）。

    Attributes:
        query: 用户原始输入
        intent: IntentResult（检索键来源：intent.target / intent.entities）
        current_file: 当前打开文件
        opened_files: 最近打开文件
        recent_artifacts: 近期 artifact 引用
        recent_messages: 最近对话片段
    """
    query: str = ""
    intent: Optional[Any] = None
    current_file: str = ""
    opened_files: List[str] = field(default_factory=list)
    recent_artifacts: List[str] = field(default_factory=list)
    recent_messages: List[str] = field(default_factory=list)
    workspace: Optional[Any] = None

    def retrieval_keys(self) -> List[str]:
        """检索键：优先 intent 的结构化信息，而非自然语言全文。

        自然语言不是好的检索键；intent.target / entities 已做一次结构化。
        """
        keys: List[str] = []
        if self.intent is not None:
            target = getattr(self.intent, "target", "") or ""
            if target:
                keys.append(target)
            for e in (getattr(self.intent, "entities", None) or []):
                if isinstance(e, str) and e and e not in keys:
                    keys.append(e)
        if not keys and self.query:
            keys.append(self.query)
        return keys[:3]


class Grounder:
    """Grounder — 检索候选（search space reduction only）。"""

    def __init__(self, budget: Optional[GroundingBudget] = None):
        self._budget = budget or GroundingBudget()

    def ground(self, input: GroundingInput) -> GroundingResult:
        """主入口：GroundingInput → GroundingResult（context + stats + trace）。"""
        t0 = time.perf_counter()
        ctx = GroundingContext(query=input.query)
        stats = GroundingStats()
        trace = GroundingTrace(
            query=input.query,
            intent_summary=self._intent_summary(input),
        )

        keys = input.retrieval_keys()
        trace.keys_used = keys

        # 私有检索方法（第一版不拆类；接口稳定后再演化）
        ctx.candidates.extend(self._ground_files(input, keys, stats, trace))
        ctx.candidates.extend(self._ground_symbols(input, keys, stats, trace))
        ctx.candidates.extend(self._ground_tests(input, keys, stats, trace))
        ctx.conversation = self._ground_conversation(input)

        # 排序 + 裁剪（search space reduction）
        ctx.candidates.sort(key=lambda c: -c.score)
        kept = ctx.candidates[: self._budget.max_candidates]
        pruned = len(ctx.candidates) - len(kept)
        ctx.candidates = kept

        stats.candidate_count = len(ctx.candidates)
        stats.elapsed_ms = (time.perf_counter() - t0) * 1000
        trace.pruned = pruned
        trace.top5 = [c.name for c in ctx.top(5)]
        trace.latency_ms = stats.elapsed_ms

        return GroundingResult(context=ctx, stats=stats, trace=trace)

    # ── 私有检索方法 ──

    def _ground_files(self, input: GroundingInput, keys: List[str],
                      stats: GroundingStats, trace: GroundingTrace) -> List[Candidate]:
        from agent.compat.workspace import get_legacy_workspace_service

        results: List[Candidate] = []
        try:
            ws = (
                input.workspace
                if input.workspace is not None
                else get_legacy_workspace_service()
            )
        except Exception:
            return results

        budget = self._budget
        matched_names: set[str] = set()
        for key in keys:
            if stats.files_searched >= budget.max_workspace_hits:
                break
            try:
                matches = ws.find(key)
            except Exception:
                matches = []
            stats.files_searched += len(matches)
            trace.searched_hits.setdefault("workspace", trace.searched_hits.get("workspace", 0) + len(matches))
            for m in matches[:5]:
                name = str(m.path)
                if name in matched_names:
                    continue
                matched_names.add(name)
                results.append(Candidate(
                    kind="file",
                    name=name,
                    score=m.score,
                    reason=m.reason or f"matches '{key}'",
                    source="workspace",
                    metadata={"matched_key": key},
                ))

        # 路径段展开：key 作为路径段匹配所有 index 文件（补 find 文件名局限，
        # 如内容含 add 函数但文件名不含的 calculator/core.py）
        try:
            ws_obj = (
                ws.current_workspace()
                if hasattr(ws, "current_workspace")
                else ws
            )
            all_files = ws_obj.indexed_files()
        except Exception:
            all_files = []
        for key in keys:
            key_lower = (key or "").lower()
            if not key_lower:
                continue
            for rel in all_files:
                if key_lower in rel.lower():
                    if rel in matched_names:
                        continue
                    matched_names.add(rel)
                    # 目录段匹配（key == 目录名）比文件名字符串更精确 → 更高分
                    is_dir_segment = f"/{key_lower}/" in f"/{rel.lower()}/"
                    results.append(Candidate(
                        kind="file",
                        name=rel,
                        score=0.7 if is_dir_segment else 0.5,
                        reason=f"path contains '{key}'",
                        source="workspace",
                        metadata={"matched_key": key, "dir_segment": is_dir_segment},
                    ))
        return results

    def _ground_symbols(self, input: GroundingInput, keys: List[str],
                        stats: GroundingStats, trace: GroundingTrace) -> List[Candidate]:
        results: List[Candidate] = []
        if input.workspace is not None:
            # Workspace indexes expose file discovery, not repository symbol
            # search. A scoped runtime must not silently consult the global
            # WorkspaceManager for this optional enrichment.
            return results
        for key in keys:
            if stats.symbols_checked >= self._budget.max_candidates * 2:
                break
            if not key or not key[0].isupper():
                continue  # 符号名通常驼峰开头
            try:
                from agent.compat.workspace import get_legacy_workspace_service
                ws = get_legacy_workspace_service().current_workspace()
                if ws is None:
                    continue
                find_symbol = getattr(ws, "find_symbol", None)
                if not callable(find_symbol):
                    continue
                paths = find_symbol(key)
            except Exception:
                paths = []
            stats.symbols_checked += len(paths)
            trace.searched_hits.setdefault("symbol", trace.searched_hits.get("symbol", 0) + len(paths))
            for p in paths[:2]:
                results.append(Candidate(
                    kind="symbol",
                    name=str(p),
                    score=0.9,
                    reason=f"symbol '{key}'",
                    source="symbol",
                    metadata={"matched_symbol": key},
                ))
        return results

    def _ground_tests(self, input: GroundingInput, keys: List[str],
                      stats: GroundingStats, trace: GroundingTrace) -> List[Candidate]:
        """与候选文件相关的测试文件（tests/ 下同名/同模块）。"""
        results: List[Candidate] = []
        for key in keys:
            stem = key.rsplit("/", 1)[-1].rsplit(".", 1)[0] if key else ""
            if not stem:
                continue
            try:
                from agent.compat.workspace import get_legacy_workspace_service
                ws = (
                    input.workspace
                    if input.workspace is not None
                    else get_legacy_workspace_service()
                )
                matches = ws.find(stem)
            except Exception:
                matches = []
            for m in matches[:3]:
                name = str(m.path)
                if "/tests/" in name or name.startswith("tests/"):
                    results.append(Candidate(
                        kind="test",
                        name=name,
                        score=m.score * 0.95,
                        reason=f"related test for '{key}'",
                        source="workspace",
                        metadata={"related_to": key},
                    ))
        return results

    def _ground_conversation(self, input: GroundingInput) -> List[str]:
        parts: List[str] = []
        if input.current_file:
            parts.append(f"当前文件: {input.current_file}")
        if input.opened_files:
            parts.append(f"打开的文件: {', '.join(input.opened_files[-3:])}")
        parts.extend(input.recent_messages[-2:])
        return parts

    @staticmethod
    def _intent_summary(input: GroundingInput) -> str:
        intent = input.intent
        if intent is None:
            return ""
        return f"{getattr(intent, 'domain', '')}/{getattr(intent, 'action', '')} target={getattr(intent, 'target', '')}"
