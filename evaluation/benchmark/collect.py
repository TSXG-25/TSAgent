#!/usr/bin/env python3
"""benchmark/collect — 聚合 benchmark 结果 → MetricsV1 → history 快照。

用法:
    python evaluation/benchmark/collect.py            # 聚合 out/ → history/<today>.json
    python evaluation/benchmark/collect.py --print    # 打印 Metrics
"""
import glob
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from evaluation.metrics_v1 import MetricsV1

OUT = os.path.join("benchmarks", "_fixtures", "out")
HISTORY = os.path.join("evaluation", "history")


def collect() -> MetricsV1:
    files = sorted(glob.glob(os.path.join(OUT, "T*.json")) +
                   glob.glob(os.path.join(OUT, "NAV*.json")))
    if not files:
        return MetricsV1()

    n = len(files)
    m = MetricsV1()
    for fn in files:
        with open(fn) as f:
            r = json.load(f)
        m.verification_success += 1 if r["result"] == "pass" else 0
        g = r.get("grounding") or {}
        m.grounding_recall += g.get("recall", 0)
        # Top1: 目标文件 == candidates[0]
        cands = g.get("candidates", [])
        targets = g.get("targets", [])
        if cands and targets and any(t in cands[0] for t in targets):
            m.grounding_top1 += 1
        m.latency_ms += r.get("elapsed_s", 0) * 1000
        m.planning_success += 1 if r.get("planning_ok", True) else 0
        m.execution_success += 1 if r.get("failure_category") in ([], None, ["verification"]) else 0

    m.verification_success /= n
    m.grounding_recall /= n
    m.grounding_top1 /= n
    m.latency_ms /= n
    m.planning_success /= n
    m.execution_success /= n
    return m


def main():
    m = collect()
    d = m.to_dict()
    if "--print" in sys.argv:
        print(json.dumps(d, indent=2))
        return
    os.makedirs(HISTORY, exist_ok=True)
    path = os.path.join(HISTORY, f"{date.today().isoformat()}.json")
    with open(path, "w") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    print(f"saved: {path}\n{json.dumps(d, indent=2)}")


if __name__ == "__main__":
    main()
