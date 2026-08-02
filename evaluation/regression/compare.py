#!/usr/bin/env python3
"""regression/compare — main vs PR Metrics 对比 + 三级 Quality Gate。

用法:
    python evaluation/regression/compare.py <base_metrics.json> <cur_metrics.json>
"""
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from evaluation.metrics_v1 import MetricsV1, gate_status


def load(path: str) -> MetricsV1:
    with open(path) as f:
        return MetricsV1.from_dict(json.load(f))


def main():
    if len(sys.argv) < 3:
        print("usage: compare.py <base.json> <cur.json>")
        sys.exit(2)
    base = load(sys.argv[1])
    cur = load(sys.argv[2])
    status = gate_status(cur, base)

    print(f"Quality Gate: {status}")
    print(f"{'metric':24s} {'base':>10s} {'cur':>10s} {'delta':>10s}")
    for key in base.to_dict():
        b = getattr(base, key)
        c = getattr(cur, key)
        d = c - b
        arrow = "▲" if d > 1e-6 else ("▼" if d < -1e-6 else "=")
        print(f"{key:24s} {b:10.3f} {c:10.3f} {d:+10.3f} {arrow}")

    sys.exit(0 if status != "FAIL" else 1)


if __name__ == "__main__":
    main()
