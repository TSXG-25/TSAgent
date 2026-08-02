#!/usr/bin/env python3
"""快速评估 8 个任务的 Grounding 指标（不跑 agent）。"""
import json
import os
import sys

sys.path.insert(0, ".")

from agent.bootstrap import init_workspace
from agent.grounding import Grounder, GroundingInput

init_workspace()


class _FakeIntent:
    def __init__(self, keys):
        self.target = keys[0] if keys else ""
        self.entities = list(keys)


TASKS = os.path.join("benchmarks", "tasks")
total_recall = 0
count = 0
for fn in sorted(os.listdir(TASKS)):
    if not fn.endswith(".json"):
        continue
    with open(os.path.join(TASKS, fn)) as f:
        task = json.load(f)
    keys = task.get("grounding_keys", [])
    result = Grounder().ground(GroundingInput(
        query=task["prompt"],
        intent=_FakeIntent(keys),
    ))
    targets = task.get("grounding_targets", [])
    cands = [c.name for c in result.context.candidates]
    hits = [t for t in targets if any(t in c for c in cands)]
    recall = len(hits) / len(targets) if targets else 0
    total_recall += recall
    count += 1
    print(f"{task['id']}: space={len(cands):2d} recall={recall:.2f} targets={targets}")
    for t in targets:
        if not any(t in c for c in cands):
            print(f"    MISS: {t} | cands={cands[:4]}")

print(f"\nAVG_RECALL={total_recall/count:.2f}")

