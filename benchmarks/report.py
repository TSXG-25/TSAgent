#!/usr/bin/env python3
"""Benchmark report — 汇总结果 + 失败分类聚类。

用法:
    python benchmarks/report.py
"""
import json
import os

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_fixtures", "out")


def main():
    files = sorted(f for f in os.listdir(OUT_DIR) if f.endswith(".json"))
    results = []
    for fn in files:
        with open(os.path.join(OUT_DIR, fn)) as f:
            results.append(json.load(f))

    total = len(results)
    passed = sum(1 for r in results if r["result"] == "pass")
    print(f"===== TSAgent Benchmark: {passed}/{total} passed =====")

    cats = {}
    for r in results:
        if r["result"] == "fail":
            for c in (r.get("failure_category") or ["unclassified"]):
                cats[c] = cats.get(c, 0) + 1

    print("\n-- failure categories --")
    for c, n in sorted(cats.items(), key=lambda x: -x[1]):
        print(f"  {c}: {n}")

    print("\n-- per-task --")
    for r in results:
        mark = "PASS" if r["result"] == "pass" else "FAIL"
        print(
            f"  [{mark}] {r['task']:6s} {r['name']:20s} "
            f"({r['elapsed_s']}s) cats={r.get('failure_category')}"
        )


if __name__ == "__main__":
    main()
