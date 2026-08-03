#!/usr/bin/env python3
"""eval_decision — Decision Quality Benchmark（v2.0-D）。

指标（v2.0-D KPI）：
    recovery_rate             正确决策（gold 匹配）比例 —— 决策层恢复正确率
    wrong_recovery_rate       策略错误比例（gold 对比，如 permission 却 retry = 错误策略）
    intervention_efficiency   ask 决策占比例（asks/task 代理指标，越低越好）
    action_accuracy           按 action 分解（retry/switch/ask/finish）

DecisionTrace：每个决策产生 trace（decision_id/rule/confidence/rejected），
               Wrong Recovery Rate 分析直接消费。

数据来源：evaluation/datasets/decision/DS0xx_*.json（10 场景，覆盖 Decision Boundary）
用法:
    python evaluation/benchmark/eval_decision.py [--record]
"""
import json
import os
import sys

sys.path.insert(0, ".")

from agent.decision.decision import (
    decide, DecisionInput, ExecutionState, ACTIONS,
)

DS_DIR = "evaluation/datasets/decision"


def load_scenarios() -> list:
    scenarios = []
    for fn in sorted(os.listdir(DS_DIR)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(DS_DIR, fn)) as f:
            scenarios.append(json.load(f))
    return scenarios


def evaluate(scenarios: list = None) -> dict:
    scenarios = scenarios if scenarios is not None else load_scenarios()
    results = []
    for s in scenarios:
        inp = s["input"]
        state = ExecutionState(**inp["state"])
        dinput = DecisionInput(
            diagnosis=inp["diagnosis"],
            diagnosis_confidence=inp["diagnosis_confidence"],
            state=state,
            event_id=s["id"],
        )
        decision, trace = decide(dinput)
        gold = s["gold"]["action"]
        results.append({
            "id": s["id"],
            "diagnosis": inp["diagnosis"],
            "gold": gold,
            "chosen": decision.action,
            "confidence": decision.confidence,
            "rule": trace.policy_rule,
            "rejected": trace.rejected_actions,
            "correct": decision.action == gold,
        })

    n = len(results)
    action_correct = {a: [] for a in ACTIONS}
    for r in results:
        if r["gold"] == r["chosen"]:
            action_correct.setdefault(r["gold"], []).append(True)

    metrics = {
        "recovery_rate": round(sum(1 for r in results if r["correct"]) / n, 3),
        "wrong_recovery_rate": round(sum(1 for r in results if not r["correct"]) / n, 3),
        "intervention_efficiency": round(
            sum(1 for r in results if r["chosen"] == "ask") / n, 3),
        "scenarios": n,
    }
    return metrics, results


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Decision Quality Benchmark（v2.0-D）")
    parser.add_argument("--record", action="store_true",
                        help="记录到 Capability Progress Curve（Trend Gate 基线）")
    args = parser.parse_args()

    from evaluation.metrics_v2 import MetricsV2

    metrics, results = evaluate()
    m = MetricsV2()
    m.retry_accuracy = round(sum(1 for r in results if r["gold"] == "retry" and r["correct"]) / max(sum(1 for r in results if r["gold"] == "retry"), 1), 3)
    m.finish_accuracy = round(sum(1 for r in results if r["gold"] == "finish" and r["correct"]) / max(sum(1 for r in results if r["gold"] == "finish"), 1), 3)
    m.clarification_accuracy = round(sum(1 for r in results if r["gold"] == "ask" and r["correct"]) / max(sum(1 for r in results if r["gold"] == "ask"), 1), 3)

    print("Decision Quality Benchmark（v2.0-D）\n")
    print(f"  {'scenario':10s} {'diagnosis':18s} {'gold':7s} {'chosen':8s} {'conf':5s} {'rule':24s}")
    for r in results:
        mark = "✓" if r["correct"] else "✗"
        print(f"  {r['id']:10s} {r['diagnosis']:18s} {r['gold']:7s} {r['chosen']:8s} "
              f"{r['confidence']:.2f}  {r['rule']:24s} {mark}")
    print("\n  Decision KPI")
    print(f"  recovery_rate          {metrics['recovery_rate']:.3f}")
    print(f"  wrong_recovery_rate    {metrics['wrong_recovery_rate']:.3f}")
    print(f"  intervention_efficiency {metrics['intervention_efficiency']:.3f}")

    if args.record:
        from evaluation.benchmark.eval_planning import record_curve
        record_curve("decision", {**metrics,
                                  "retry_accuracy": m.retry_accuracy,
                                  "clarification_accuracy": m.clarification_accuracy,
                                  "finish_accuracy": m.finish_accuracy})

    all_pass = metrics["recovery_rate"] == 1.0
    print(f"\nDecision: {'PASS' if all_pass else 'FAIL'}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
