#!/usr/bin/env python3
"""eval_multiturn — Context Resolution Accuracy（v1.2 核心指标）。

多轮场景：每轮用 ReferenceResolver + IntentEngine 解析，
检查 target/topic 是否匹配 expected（不跑 agent，纯解析层评估）。
"""
import json
import os
import sys

sys.path.insert(0, ".")

from agent.cognition.cognitive_context import CognitiveContext, ConversationState
from agent.cognition.reference_resolver import ReferenceResolver
from agent.cognition.intent_engine import IntentEngine

MT_DIR = "evaluation/datasets/conversation/multiturn"


class _Engine:
    def __init__(self):
        self.resolver = ReferenceResolver()
        self.intent = IntentEngine()
        self.state = ConversationState()

    def resolve(self, user_input: str) -> dict:
        ctx = CognitiveContext(
            query=user_input,
            conversation_state=self.state,
            conversation=[],
        )
        resolved = self.resolver.resolve(user_input, ctx)
        ctx.resolved_query = resolved
        intent = self.intent.analyze(ctx)
        # 更新状态（供下一轮）
        # target：消歧结果优先，否则用 IntentEngine 提取的显式 target
        final_target = resolved.target or getattr(intent, "target", "") or ""
        if final_target:
            self.state.last_target = final_target
        if intent.domain:
            self.state.last_domain = intent.domain
        if intent.action:
            self.state.last_action = intent.action
        return {
            "target": final_target,
            "domain": intent.domain,
            "action": intent.action,
            "trace": resolved.resolution_trace,
        }


def main():
    total = 0
    correct = 0
    for fn in sorted(os.listdir(MT_DIR)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(MT_DIR, fn)) as f:
            scenario = json.load(f)
        engine = _Engine()
        print(f"\n== {scenario['id']} ==")
        for turn in scenario["turns"]:
            exp = turn["expected"]
            got = engine.resolve(turn["input"])
            total += 1
            ok = got["target"] == exp["target"]
            correct += 1 if ok else 0
            mark = "✓" if ok else "✗"
            print(f"  {mark} '{turn['input']}' -> target={got['target']!r} (exp {exp['target']!r}) {got['trace'][:50]}")

    print(f"\nContext Resolution Accuracy: {correct}/{total} = {correct/total:.0%}")


if __name__ == "__main__":
    main()
