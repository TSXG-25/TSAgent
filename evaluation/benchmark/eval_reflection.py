#!/usr/bin/env python3
"""eval_reflection — Reflection Quality Benchmark（v2.0-C）。

指标（Reflection 自身 KPI，约束 3）：
    diagnosis_accuracy           诊断 root_cause 与 gold 一致率
    false_diagnosis_rate         误诊率（root_cause 错但 correction 恰好对的污染信号）
    correction_proposal_accuracy correction action 与 gold 一致率

Reflection Quality Gate（约束 4，Determinism）：
    同一 FailureEvent → reflect() × N → Diagnosis 完全一致（确定性第一层）

数据来源：evaluation/datasets/reflection/RF0xx_*.json（10 场景，含 evidence + gold）
用法:
    python evaluation/benchmark/eval_reflection.py
"""
import json
import os
import sys

sys.path.insert(0, ".")

from agent.reflection.reflector import reflect
from evaluation.benchmark.failboard_v2 import FailureEvent, Evidence

RF_DIR = "evaluation/datasets/reflection"


def load_scenarios() -> list:
    scenarios = []
    for fn in sorted(os.listdir(RF_DIR)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(RF_DIR, fn)) as f:
            scenarios.append(json.load(f))
    return scenarios


def _to_event(s: dict) -> FailureEvent:
    ev = s["event"]
    return FailureEvent(
        benchmark=ev.get("benchmark", "reflection"),
        scenario=ev.get("scenario", s["id"]),
        layer=ev.get("layer", "reflection"),
        dimension=ev.get("dimension", "completion"),
        failure=ev.get("failure", ""),
        evidence=[Evidence(**e) for e in ev.get("evidence", [])],
        symptom=ev.get("symptom", "unknown"),
        detected_at="2026-08",
    )


def evaluate(scenarios: list = None) -> dict:
    scenarios = scenarios if scenarios is not None else load_scenarios()
    results = []
    for s in scenarios:
        event = _to_event(s)
        gold_rc = s["gold"]["root_cause"]
        gold_corr = s["gold"]["correction"]
        result = reflect(event)
        rc = result.diagnosis.root_cause
        corr = result.correction.action

        # 误诊（False Diagnosis）：root_cause 错，但 correction 恰好对 → 污染信号
        false_dx = (rc != gold_rc) and (corr == gold_corr)

        results.append({
            "id": s["id"],
            "gold_root_cause": gold_rc,
            "predicted_root_cause": rc,
            "confidence": result.diagnosis.confidence,
            "gold_correction": gold_corr,
            "predicted_correction": corr,
            "diagnosis_ok": rc == gold_rc,
            "correction_ok": corr == gold_corr,
            "false_diagnosis": false_dx,
        })

    n = len(results)
    metrics = {
        "diagnosis_accuracy": round(sum(1 for r in results if r["diagnosis_ok"]) / n, 3),
        "correction_proposal_accuracy": round(sum(1 for r in results if r["correction_ok"]) / n, 3),
        "false_diagnosis_rate": round(sum(1 for r in results if r["false_diagnosis"]) / n, 3),
        "scenarios": n,
    }
    return metrics, results


def determinism_gate(scenarios: list = None, n_runs: int = 100) -> tuple:
    """Reflection Quality Gate（约束 4）：同一 event → reflect() × N → Diagnosis 完全一致。"""
    scenarios = scenarios if scenarios is not None else load_scenarios()
    all_pass = True
    checked = 0
    for s in scenarios:
        event = _to_event(s)
        first = reflect(event)
        for _ in range(n_runs - 1):
            cur = reflect(event)
            if (cur.diagnosis.root_cause != first.diagnosis.root_cause
                    or cur.diagnosis.confidence != first.diagnosis.confidence
                    or cur.correction.action != first.correction.action):
                all_pass = False
                break
        checked += 1
    return all_pass, checked


PROGRESS = os.path.join("evaluation", "planning_progress.json")


def record_curve(metrics: dict) -> None:
    """把 Reflection 指标写入 Capability Progress Curve（Trend Gate 基线）。"""
    curve = {}
    if os.path.exists(PROGRESS):
        with open(PROGRESS) as f:
            curve = json.load(f)
    curve.setdefault("capabilities", [])
    if "reflection" not in curve["capabilities"]:
        curve["capabilities"].append("reflection")
    curve["reflection_metrics"] = metrics
    curve.setdefault("reflection_history", []).append(metrics)
    with open(PROGRESS, "w") as f:
        json.dump(curve, f, ensure_ascii=False, indent=2)
    print(f"📈 Capability Progress Curve 已更新: reflection → {PROGRESS}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Reflection Quality Benchmark（v2.0-C）")
    parser.add_argument("--record", action="store_true",
                        help="把评估结果记录到 Capability Progress Curve（Trend Gate 基线）")
    args = parser.parse_args()

    from evaluation.metrics_v2 import MetricsV2

    metrics, results = evaluate()
    m = MetricsV2()
    m.diagnosis_accuracy = metrics["diagnosis_accuracy"]
    m.false_diagnosis_rate = metrics["false_diagnosis_rate"]
    m.correction_success = metrics["correction_proposal_accuracy"]

    print("Reflection Quality Benchmark（v2.0-C）\n")
    print(f"  {'scenario':10s} {'gold_rc':12s} {'pred_rc':12s} {'conf':5s} {'corr_ok'}")
    for r in results:
        print(f"  {r['id']:10s} {r['gold_root_cause']:12s} {r['predicted_root_cause']:12s} "
              f"{r['confidence']:.2f}  {'✓' if r['diagnosis_ok'] else '✗'}"
              f"{' (误诊!' if r['false_diagnosis'] else ''}")
    print("\n  Reflection KPI（自身）")
    print(f"  diagnosis_accuracy        {metrics['diagnosis_accuracy']:.3f}")
    print(f"  correction_proposal_accuracy {metrics['correction_proposal_accuracy']:.3f}")
    print(f"  false_diagnosis_rate      {metrics['false_diagnosis_rate']:.3f}")

    # Determinism Gate（约束 4）
    ok, checked = determinism_gate(n_runs=100)
    print(f"\n  Reflection Determinism Gate: {'PASS' if ok else 'FAIL'}（{checked} 场景 × 100 次）")

    if args.record:
        record_curve({**metrics, "determinism_gate": ok})

    all_pass = metrics["diagnosis_accuracy"] == 1.0 and ok
    print(f"\nReflection: {'PASS' if all_pass else 'FAIL'}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
