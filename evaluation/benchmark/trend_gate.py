#!/usr/bin/env python3
"""trend_gate — Capability Progress Curve 检查（v2.0 Trend Gate）。

核心原则（Roadmap 决策）：
    CI 不判断"当前是多少"（如 >70%），而是判断"是不是持续变好"。
    新 Capability 使整体能力下降 → Regression FAIL。

数据来源：evaluation/planning_progress.json（由 eval_planning --record / eval_long_horizon 写入）。

检查逻辑（metrics_v2.INTELLIGENCE_BUDGET）：
    - 每个 capability 的相邻记录（history[-1] vs history[-2]）
    - ge 指标不得下降；le 指标不得上升（如 false_confidence / false_diagnosis_rate）

用法:
    python evaluation/benchmark/trend_gate.py
退出码: 0 = PASS（无下降），1 = Regression FAIL（某指标下降）
"""
import json
import os
import sys

sys.path.insert(0, ".")

from evaluation.metrics_v2 import MetricsV2, trend_gate

PROGRESS = os.path.join("evaluation", "planning_progress.json")


def main():
    if not os.path.exists(PROGRESS):
        print(f"❌ 未找到 Progress Curve: {PROGRESS}（先运行 eval_planning --record 建立基线）")
        return 1

    with open(PROGRESS) as f:
        curve = json.load(f)

    capabilities = curve.get("capabilities", [])
    failures = []
    print("Capability Progress Curve（Trend Gate）\n")

    for cap in capabilities:
        history = curve.get(f"{cap}_history", [])
        if len(history) < 2:
            print(f"  {cap:12s} 仅 {len(history)} 条记录（基线，无下降对比 → PASS）")
            continue
        prev = MetricsV2.from_dict(history[-2])
        cur = MetricsV2.from_dict(history[-1])
        ok, cap_fails = trend_gate(cur, prev)
        print(f"  {cap:12s} {'PASS' if ok else 'FAIL'}（{len(history)} 条记录）")
        for fl in cap_fails:
            print(f"      ✗ {fl}")
        failures.extend(cap_fails)

    # Long Horizon 集成指标同样纳入趋势检查
    lh = curve.get("lh_metrics", {})
    if lh and curve.get("extra", {}).get("lh_history", []):
        lh_history = curve["extra"]["lh_history"]
        if len(lh_history) >= 2:
            prev = MetricsV2.from_dict(lh_history[-2])
            cur = MetricsV2.from_dict(lh_history[-1])
            ok, lh_fails = trend_gate(cur, prev)
            print(f"  {'long_horizon':12s} {'PASS' if ok else 'FAIL'}")
            for fl in lh_fails:
                print(f"      ✗ {fl}")
            failures.extend(lh_fails)

    if failures:
        print("\n❌ Regression FAIL：新 Capability 使整体能力下降。")
        return 1
    print("\n✅ Trend Gate PASS（无指标下降）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
