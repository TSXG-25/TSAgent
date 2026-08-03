#!/usr/bin/env python3
"""eval_multiturn — Context Resolution Accuracy（v1.2 核心指标）。

多轮场景：每轮用 ReferenceResolver + IntentEngine 解析，
检查 target/topic 是否匹配 expected（不跑 agent，纯解析层评估）。
"""
import json
import os
import sys

sys.path.insert(0, ".")

from agent.cognition.cognitive_context import CognitiveContext, ConversationState, ResolutionResult
from agent.cognition.reference_resolver import ReferenceResolver
from agent.cognition.intent_engine import IntentEngine

MT_DIR = "evaluation/datasets/conversation/multiturn"


def _is_code_symbol(text: str) -> bool:
    """代码符号特征：含英文字母且非路径/文件（排除中文目标与文件路径）。"""
    if not text or "/" in text or "\\" in text:
        return False
    if any(text.endswith(ext) for ext in (".py", ".js", ".ts", ".md", ".json", ".txt")):
        return False
    return any(("a" <= ch <= "z") or ("A" <= ch <= "Z") for ch in text)


class _Engine:
    def __init__(self, repository=None, memory=None):
        self.resolver = ReferenceResolver()
        self.intent = IntentEngine()
        self.state = ConversationState()
        self.repository = repository or {}
        self.memory = memory or []

    def resolve(self, user_input: str) -> dict:
        ctx = CognitiveContext(
            query=user_input,
            conversation_state=self.state,
            conversation=[],
            repository_symbols=self.repository,
            memory_resolutions=self.memory,
        )
        resolved = self.resolver.resolve(user_input, ctx)
        ctx.resolved_query = resolved.to_resolved_query()
        intent = self.intent.analyze(ctx)
        # v1.2B: State = Cache —— timeline 写入（Resolver 产出 ResolutionResult）
        # target：消歧结果优先，否则用 IntentEngine 提取的显式 target
        final_target = resolved.target or getattr(intent, "target", "") or ""
        # last_symbol：resolved.symbol 或 intent 提取的驼峰实体，或代码符号 target
        sym = resolved.symbol or ""
        if not sym:
            for e in (getattr(intent, "entities", None) or []):
                if isinstance(e, str) and e and e[0].isupper():
                    sym = e
                    break
        if not sym and final_target and _is_code_symbol(final_target):
            sym = final_target
        result = ResolutionResult(
            kind=resolved.kind,
            target=final_target,
            symbol=sym,
            confidence=resolved.confidence,
            trace=resolved.trace,
            raw=user_input,
            resolved_query=resolved.to_resolved_query(),
        )
        self.state.record(result)
        return {
            "target": final_target,
            "domain": intent.domain,
            "action": intent.action,
            "trace": resolved.resolution_trace,
        }


def main():
    from evaluation.benchmark.contract_verification import verify as verify_contract
    if not verify_contract():
        print("⚠️  Resolver Contract 已变化 —— 先解决 Contract Verification 再评估。")

    total = 0
    correct = 0
    # 分项统计（v1.2A KPI：Topic / Symbol / File / Reference / Unknown）
    buckets = {}
    for fn in sorted(os.listdir(MT_DIR)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(MT_DIR, fn)) as f:
            scenario = json.load(f)
        engine = _Engine(
            repository=scenario.get("repository", {}),
            memory=scenario.get("memory", []),
        )
        print(f"\n== {scenario['id']} ==")
        for turn in scenario["turns"]:
            exp = turn["expected"]
            got = engine.resolve(turn["input"])
            total += 1
            ok = got["target"] == exp["target"]
            correct += 1 if ok else 0
            kind = exp.get("kind", "other")
            b = buckets.setdefault(kind, {"n": 0, "ok": 0})
            b["n"] += 1
            b["ok"] += 1 if ok else 0
            mark = "✓" if ok else "✗"
            print(f"  {mark} '{turn['input']}' -> target={got['target']!r} (exp {exp['target']!r}) {got['trace'][:50]}")

    print(f"\nContext Resolution Accuracy: {correct}/{total} = {correct/total:.0%}")
    # 分项指标
    order = ["topic", "symbol", "file", "reference", "direct", "chat", "ordinal", "unknown"]
    print("--- 分项 KPI（v1.2A）---")
    for kind in order:
        if kind not in buckets:
            continue
        b = buckets[kind]
        pct = b["ok"] / b["n"] if b["n"] else 0.0
        name = {
            "topic": "Topic Continuity",
            "symbol": "Symbol Reference",
            "file": "File Reference",
            "reference": "Continuation Reference",
            "direct": "Direct Extraction",
            "chat": "Plain Chat",
            "ordinal": "Ordinal Reference",
            "unknown": "Unknown Resolution",
        }[kind]
        print(f"  {name:<24} {b['ok']}/{b['n']} = {pct:.0%}")


if __name__ == "__main__":
    main()
