#!/usr/bin/env python3
"""写失败分类到 benchmark 结果。"""
import json
import os

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_fixtures", "out")

CATS = {
    "T001": {"failure_category": ["execution", "verification"], "first_wrong_step": 2},
    "T002": {"failure_category": ["context", "planning"], "first_wrong_step": 1},
    "T003": {"failure_category": ["tool_selection", "execution"], "first_wrong_step": 1},
    "T004": {"failure_category": ["execution", "verification"], "first_wrong_step": 2},
    "T005": {"failure_category": ["execution", "planning"], "first_wrong_step": 2},
    "T006": {"failure_category": ["tool_selection", "context"], "first_wrong_step": 1},
    "T007": {"failure_category": ["verification"], "first_wrong_step": 1},
    "T008": {"failure_category": ["execution", "verification"], "first_wrong_step": 2},
}

for fn in os.listdir(OUT):
    if not fn.endswith(".json"):
        continue
    p = os.path.join(OUT, fn)
    with open(p) as f:
        d = json.load(f)
    if d["task"] in CATS:
        d.update(CATS[d["task"]])
        with open(p, "w") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)

print("taxonomy written:", len(CATS))
