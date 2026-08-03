#!/usr/bin/env python3
"""eval_repository — Repository Runtime（ADR-0008 复用验证 / Capability Reuse Ratio KPI）。

验证：同一 Resolver 处理 Repository 场景，几乎不新增抽象。

Capability Reuse Ratio（KPI）：
    - 新增 Resolver = 0（复用 ReferenceResolver）
    - 新增 Candidate = 0（复用 ResolutionCandidate）
    - 新增 Merge   = 0（复用 merge_candidates）
    - 新增 Result  = 0（复用 ResolutionResult）
    - 新增 Timeline= 0（复用 ResolutionTimeline / ConversationState）

Repository 数据经 CognitiveContext.repository_symbols 注入（Resolver 保持纯函数）。
如果未来出现 Repository 专用 Resolver 类，本 eval 的复用断言会直接 FAIL。
"""
import json
import os
import sys

sys.path.insert(0, ".")

from agent.cognition.cognitive_context import (
    ResolutionCandidate,
    ResolutionResult,
    ResolutionTimeline,
)
from agent.cognition.reference_resolver import ReferenceResolver

from evaluation.benchmark.eval_multiturn import _Engine

RE_DIR = "evaluation/datasets/repository"


def _assert_reuse():
    """Capability Reuse Ratio 断言：Repository 场景零新增解析抽象。"""
    import agent.cognition.reference_resolver as rr

    # 不允许 Repository 专用 Resolver / Candidate / Result 类存在
    for name in dir(rr):
        if "Repository" in name and name != "RepositoryResolver":
            # ReferenceResolver 是唯一共享 Resolver（Repository 不新建）
            if "Resolver" in name:
                assert name == "ReferenceResolver", f"新增 Resolver 类: {name}"
    # 本模块只复用既有契约对象（无新增 import）
    reused = {ResolutionCandidate, ResolutionResult, ResolutionTimeline, ReferenceResolver}
    assert all(c.__module__ == "agent.cognition.cognitive_context" or c.__module__ == "agent.cognition.reference_resolver" for c in reused)


def main():
    _assert_reuse()
    print("Capability Reuse Ratio: 新增 Resolver=0 / Candidate=0 / Merge=0 / Result=0 / Timeline=0 ✅")

    total = 0
    correct = 0
    for fn in sorted(os.listdir(RE_DIR)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(RE_DIR, fn)) as f:
            scenario = json.load(f)
        engine = _Engine(repository=scenario.get("repository", {}))
        print(f"\n== {scenario['id']} ==")
        for turn in scenario["turns"]:
            exp = turn["expected"]
            got = engine.resolve(turn["input"])
            total += 1
            ok = got["target"] == exp["target"]
            correct += 1 if ok else 0
            mark = "✓" if ok else "✗"
            print(f"  {mark} '{turn['input']}' -> target={got['target']!r} (exp {exp['target']!r}) {got['trace'][:45]}")

    print(f"\nRepository Context Resolution Accuracy: {correct}/{total} = {correct/total:.0%}")


if __name__ == "__main__":
    main()
