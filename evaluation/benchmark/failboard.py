#!/usr/bin/env python3
"""failboard — 从 E2E 结果生成 Fail Board（Bug Evolution History 资产）。

Fail Board 列: ID | Input | Expected | Actual | Layer | Reason | Status
"""
import json
import sys


def classify(r) -> tuple:
    """推断失败层与原因 + 修复成本。"""
    layer = ""
    reason = ""
    difficulty = "M"
    if r.get("error"):
        err = r["error"]
        if "unexpected keyword" in err or "TypeError" in err:
            layer = "integration"
            difficulty = "XS"
        else:
            layer = "runtime"
            difficulty = "L"
        reason = err[:80]
    elif r.get("answer", "").strip() == "":
        layer = "runtime"
        difficulty = "L"
        reason = "空回答"
    elif r.get("answer") and "{" in r["answer"]:
        layer = "answer"
        difficulty = "S"
        reason = "返回原始 JSON/结构，未转自然语言"
    else:
        failed = [c for c in r.get("checks", []) if not c[1]]
        # 层推断：断言类别
        names = [c[0] for c in failed]
        if any("intent" in n for n in names):
            layer = "intent"
            difficulty = "XS"
        elif any("one_of" in n or "not:" in n for n in names):
            layer = "answer"
            difficulty = "S"
        else:
            layer = "dataset"
            difficulty = "XS"
        reason = "; ".join(names[:3])
    return layer, reason, difficulty


def main(path):
    with open(path) as f:
        data = json.load(f)

    print(f"E2E: {data['passed']}/{data['total']} passed\n")
    print(f"{'ID':12s} {'Input':14s} {'Layer':12s} {'Diff':5s} Reason")
    print("-" * 90)
    dist = {}
    for r in data["results"]:
        if r["ok"]:
            continue
        layer, reason, diff = classify(r)
        dist[layer] = dist.get(layer, 0) + 1
        ans = r["answer"][:40].replace("\n", " ")
        print(f"{r['id']:12s} {r['input']:14s} {layer:12s} {diff:5s} {reason}")

    print("\n== Fail Distribution ==")
    for layer, n in sorted(dist.items(), key=lambda x: -x[1]):
        print(f"  {layer:12s} {n}")

    fails = [r for r in data["results"] if not r["ok"]]
    print(f"\n{len(fails)} failed cases (fix one -> failboard shrinks by one)")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: failboard.py <e2e_history.json>")
        sys.exit(2)
    main(sys.argv[1])
