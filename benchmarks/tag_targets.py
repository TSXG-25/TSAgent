#!/usr/bin/env python3
"""给 benchmark 任务添加 grounding_targets（真实答案）与 grounding_keys（模拟 intent 检索键）。"""
import json
import os

TASKS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tasks")

TARGETS = {
    "T001": ["calculator/core.py"],
    "T002": ["app/helpers.py"],
    "T003": ["src/calc.py"],
    "T004": ["server.py"],
    "T005": ["utils.py"],
    "T006": ["core/engine.py", "data/loader.py"],
    "T007": ["app.py"],
    "T008": ["target.py"],
}

# 模拟 Intent 输出（检索键）——Grounding 与 Planner 两层分别测
KEYS = {
    "T001": ["add", "calculator"],
    "T002": ["helpers", "main"],
    "T003": ["calc"],
    "T004": ["server"],
    "T005": ["utils"],
    "T006": ["engine", "loader"],
    "T007": ["app"],
    "T008": ["target", "compute"],
}

for fn in os.listdir(TASKS):
    if not fn.endswith(".json"):
        continue
    p = os.path.join(TASKS, fn)
    with open(p) as f:
        d = json.load(f)
    if d["id"] in TARGETS:
        d["grounding_targets"] = TARGETS[d["id"]]
        d["grounding_keys"] = KEYS[d["id"]]
        with open(p, "w") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
print("targets+keys added:", len(TARGETS))
