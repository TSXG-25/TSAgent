#!/usr/bin/env python3
"""Memory Fuzz report — Memory Accuracy Matrix（v2.1B-0）。"""
import json
import os
import sys
from collections import Counter, defaultdict

RESULTS = os.environ.get("MEMORY_RESULTS", "/private/tmp/memory_results.json")


def main() -> None:
    data = json.load(open(RESULTS, encoding="utf-8"))
    results = data.get("results", [])
    total = len(results)
    passed = sum(1 for r in results if r["passed"])

    print("Memory Benchmark v0.1")
    print("=" * 52)
    by_group = defaultdict(lambda: [0, 0])
    by_sub = defaultdict(lambda: [0, 0])
    for r in results:
        by_group[r["group"]][1] += 1
        by_sub[(r["group"], r["sub"])][1] += 1
        if r["passed"]:
            by_group[r["group"]][0] += 1
            by_sub[(r["group"], r["sub"])][0] += 1

    print(f"{'Group':<14}{'Recall':>10}")
    for g in ["fact", "conversation", "temporal", "interference"]:
        if g in by_group:
            p, t = by_group[g]
            print(f"{g:<14}{p}/{t} = {100*p/t:.0f}%")
    print("-" * 52)
    print(f"Overall: {passed}/{total} = {100*passed/total:.1f}%")

    print("\nDetail by sub-category:")
    for (g, s), (p, t) in sorted(by_sub.items()):
        print(f"  {g:<13}/{s:<22} {p}/{t} = {100*p/t:.0f}%")


if __name__ == "__main__":
    main()
