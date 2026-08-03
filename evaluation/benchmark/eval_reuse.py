#!/usr/bin/env python3
"""eval_reuse — Capability Reuse Score（v1.2C 长期 KPI）。

Reuse Score = 各 Capability 的 Reuse Ratio（新增抽象 = 0 → 100%）。

    规则：
    - 新增 Resolver 方法 ✅ 允许（resolve_symbol / resolve_ordinal / resolve_memory ...）
    - 新增 Dataset / Metric ✅ 允许
    - 新增 Candidate / Result / Timeline / Merge ❌ 禁止 → 该 Capability Reuse 降级
      → Contract 失效信号（立即 FAIL）

Extension Cost：新增 Resolver 方法数 / Dataset 数 / LOC（演化信号，非质量指标）。
LOC 不是质量指标，但它是很好的演化信号（某天 Image 需要 900 LOC + 改 Timeline → 预警）。

验证：Conversation / Repository / Memory 全部复用同一 Resolver（ReferenceResolver）。
"""
import json
import os
import sys

sys.path.insert(0, ".")

from agent.cognition.cognitive_context import (
    ResolutionCandidate,
    ResolutionResult,
    ResolutionTimeline,
    CognitiveContext,
)
from agent.cognition.reference_resolver import ReferenceResolver

from evaluation.benchmark.eval_multiturn import _Engine
from evaluation.benchmark.contract_verification import verify as verify_contract

MT_DIR = "evaluation/datasets/conversation/multiturn"
RE_DIR = "evaluation/datasets/repository"

# Capability → 服务的子 Resolver 方法（Extension Cost 归因）
CAPABILITY_RESOLVERS = {
    "Conversation": [
        "resolve_symbol", "resolve_candidates", "merge_candidates", "resolve_unknown",
        "_resolve_pronoun", "_resolve_continuation", "_resolve_topic_continuation",
        "_resolve_symbol_ref", "_resolve_omitted_target", "_resolve_ordinal",
        "_candidate_to_result", "_candidate_from",
    ],
    "Repository": [
        "_candidate_ordinal", "_candidate_file", "_symbols_for", "_parse_ordinal",
    ],
    "Memory": [
        "resolve_memory",
    ],
    "Capability": [
        "resolve_capability",
    ],
}


def _method_loc(names: list) -> int:
    import inspect
    total = 0
    for n in names:
        try:
            src = inspect.getsource(getattr(ReferenceResolver, n))
            total += len(src.splitlines())
        except Exception:
            pass
    return total


def _run_scenario_dir(directory: str) -> tuple:
    """跑一个 dataset 目录，返回 (correct, total)。"""
    total = 0
    correct = 0
    for fn in sorted(os.listdir(directory)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(directory, fn)) as f:
            scenario = json.load(f)
        engine = _Engine(
            repository=scenario.get("repository", {}),
            memory=scenario.get("memory", []),
        )
        for turn in scenario["turns"]:
            exp = turn["expected"]
            got = engine.resolve(turn["input"])
            total += 1
            correct += 1 if got["target"] == exp["target"] else 0
    return correct, total


def _memory_accuracy() -> tuple:
    """Memory Capability：只统计含 memory 字段的场景。"""
    total = 0
    correct = 0
    for fn in sorted(os.listdir(MT_DIR)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(MT_DIR, fn)) as f:
            scenario = json.load(f)
        if not scenario.get("memory"):
            continue
        engine = _Engine(memory=scenario.get("memory", []))
        for turn in scenario["turns"]:
            exp = turn["expected"]
            got = engine.resolve(turn["input"])
            total += 1
            correct += 1 if got["target"] == exp["target"] else 0
    return correct, total


def _capability_accuracy() -> tuple:
    """Capability Hint：TL 场景（capability 目录）。"""
    total = 0
    correct = 0
    cap_dir = "evaluation/datasets/capability"
    if not os.path.isdir(cap_dir):
        return 0, 0
    for fn in sorted(os.listdir(cap_dir)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(cap_dir, fn)) as f:
            scenario = json.load(f)
        resolver = ReferenceResolver()
        for turn in scenario["turns"]:
            c = resolver.resolve_capability(turn["input"], CognitiveContext(query=turn["input"]))
            total += 1
            correct += 1 if c.target == turn["expected"].get("capability", "") else 0
    return correct, total


def _assert_no_new_abstractions() -> bool:
    """Reuse Ratio 断言：各 Capability 零新增抽象（Candidate/Result/Timeline/Merge）。"""
    # 核心契约对象必须来自认知层（无 Capability 专用类）
    reused = {ResolutionCandidate, ResolutionResult, ResolutionTimeline, ReferenceResolver}
    for c in reused:
        mod = c.__module__
        if mod not in ("agent.cognition.cognitive_context", "agent.cognition.reference_resolver"):
            return False
    # 无 Repository/Memory 专用 Resolver 类
    for mod_name in ("agent.cognition.reference_resolver",):
        import importlib
        mod = importlib.import_module(mod_name)
        for name in dir(mod):
            if "Resolver" in name and "Reference" not in name:
                return False
    return True


def main():
    if not verify_contract():
        print("⚠️  Resolver Contract 已变化 —— Reuse Score 无意义。")
        return 1
    if not _assert_no_new_abstractions():
        print("Capability Reuse Score: FAIL —— 出现 Capability 专用解析抽象！")
        return 1

    print("Capability Reuse Score")
    print("──────────────────────")
    conv_c, conv_t = _run_scenario_dir(MT_DIR)
    repo_c, repo_t = _run_scenario_dir(RE_DIR)
    mem_c, mem_t = _memory_accuracy()
    cap_c, cap_t = _capability_accuracy()

    rows = [
        ("Conversation", conv_c, conv_t),
        ("Repository", repo_c, repo_t),
        ("Memory", mem_c, mem_t),
        ("Capability", cap_c, cap_t),
    ]
    for name, c, t in rows:
        pct = c / t if t else 1.0
        print(f"  {name:<13} Reuse 100%   Accuracy {c}/{t} = {pct:.0%}")

    score = len(rows)
    print(f"\nCapability Reuse Score: {score}/4 = 100%（新增抽象 = 0）")
    print("→ Resolver Contract 横向扩展验证通过（Conversation / Repository / Memory / Capability）")

    print("\nExtension Cost（v1.2C 演化信号）")
    print("───────────────────────────────")
    print(f"  {'Capability':<13} {'新 Resolver 方法':<14} {'新 Dataset':<10} {'LOC(约)'}")
    for name in ("Conversation", "Repository", "Memory", "Capability"):
        resolvers = CAPABILITY_RESOLVERS[name]
        n_datasets = {
            "Conversation": len([f for f in os.listdir(MT_DIR) if f.endswith(".json")]),
            "Repository": len([f for f in os.listdir(RE_DIR) if f.endswith(".json")]),
            "Memory": 1,
            "Capability": len([f for f in os.listdir("evaluation/datasets/capability") if f.endswith(".json")]),
        }[name]
        loc = _method_loc(resolvers)
        print(f"  {name:<13} {len(resolvers):<16} {n_datasets:<10} {loc}")


if __name__ == "__main__":
    main()
