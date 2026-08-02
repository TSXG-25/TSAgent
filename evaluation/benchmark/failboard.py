#!/usr/bin/env python3
"""failboard — 从 E2E 结果生成 Fail Board（Bug Evolution History 资产）。

Fail Board 列: ID | Input | Expected | Actual | Layer | Reason | Status
"""
import json
import sys


def classify(r) -> tuple:
    """推断失败层与原因。"""
    layer = ""
    reason = ""
    if r.get("error"):
        layer = "runtime"
        reason = r["error"][:80]
    elif r.get("answer") and "{" in r["answer"]:
        layer = "answer"
        reason = "返回原始 JSON/结构，未转自然语言"
    elif r.get("answer", "").strip() == "":
        layer = "runtime"
        reason = "空回答"
    else:
        # 断言失败
        failed_checks = [c for c in r.get("checks", []) if not c[1]]
        layer = "assert"
        reason = "; ".join(f"{c[0]}" for c in failed_checks[:3])
    return layer, reason


def main(path):
    with open(path) as f:
        data = json.load(f)

    print(f"E2E: {data['passed']}/{data['total']} passed\n")
    print(f"{'ID':12s} {'Input':14s} {'Expected':18s} {'Layer':8s} {'Reason'}")
    print("-" * 90)
    for r in data["results"]:
        if r["ok"]:
            continue
        exp = ""
        ans = r["answer"][:50].replace("\n", " ")
        layer, reason = classify(r)
        print(f"{r['id']:12s} {r['input']:14s} {ans:18s} {layer:8s} {reason}")

    fails = [r for r in data["results"] if not r["ok"]]
    print(f"\n{len(fails)} failed cases (fix one -> failboard shrinks by one)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: failboard.py <e2e_history.json>")
        sys.exit(2)
    main(sys.argv[1])
