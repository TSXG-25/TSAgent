#!/usr/bin/env python3
"""eval_capability — Capability Hint（v1.2C C3）。

resolve_capability()：意图 → capability（calculation / web_search / translation / code /
file_ops / knowledge / scheduling）。

关键设计：
- Resolver 返回 Capability Hint，**不绑定具体工具**（Tool 是 Planner 职责）。
- 复用同一 ResolutionCandidate 模型（零新增抽象）。
"""
import json
import os
import sys

sys.path.insert(0, ".")

from agent.cognition.cognitive_context import CognitiveContext, ConversationState
from agent.cognition.reference_resolver import ReferenceResolver

CAP_DIR = "evaluation/datasets/capability"


def main():
    total = 0
    correct = 0
    resolver = ReferenceResolver()
    state = ConversationState()
    for fn in sorted(os.listdir(CAP_DIR)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(CAP_DIR, fn)) as f:
            scenario = json.load(f)
        print(f"\n== {scenario['id']} ==")
        for turn in scenario["turns"]:
            exp = turn["expected"]
            ctx = CognitiveContext(query=turn["input"], conversation_state=state, conversation=[])
            c = resolver.resolve_capability(turn["input"], ctx)
            total += 1
            ok = c.target == exp.get("capability", "")
            correct += 1 if ok else 0
            mark = "✓" if ok else "✗"
            print(f"  {mark} '{turn['input']}' -> capability={c.target!r} (exp {exp.get('capability')!r})")

    print(f"\nCapability Hint Accuracy: {correct}/{total} = {correct/total:.0%}")


if __name__ == "__main__":
    main()
