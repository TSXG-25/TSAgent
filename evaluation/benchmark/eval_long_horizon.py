#!/usr/bin/env python3
"""eval_long_horizon — Long Horizon Benchmark（v2.0-B · Integration Benchmark）。

Long Horizon 不是 Capability（ADR 决定），它是
    Planning + Decision + Reflection + Runtime + Resolver
共同作用的结果，因此作为**集成验证**持续运行（每完成一个 Capability 立即跑）。

指标（metrics_v2）：
    completion_rate    → verify 通过比例
    recovery_count     → 平均恢复次数（replan / runtime recovery）
    context_drift      → 上下文漂移（计划目标 vs 最终完成的偏差比例）
    latency_ms         → 平均耗时

Trend Gate（Capability Progress Curve）：新 Capability 不能使指标下降。

用法:
    python evaluation/benchmark/eval_long_horizon.py [LH001 ...]   # 默认全部
"""
import asyncio
import contextlib
import io
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from agent.bootstrap import (
    init_workspace, load_all_tools, load_all_skills, load_all_workflows,
)
from agent.registry.capability_registry import register_default_capabilities
from evaluation.metrics_v2 import MetricsV2

LH_DIR = os.path.join("evaluation", "datasets", "long_horizon")
PROGRESS = os.path.join("evaluation", "planning_progress.json")


def bootstrap_once():
    init_workspace()
    load_all_tools()
    load_all_skills()
    load_all_workflows()
    register_default_capabilities()
    print("  ✅ long-horizon runtime initialized", flush=True)


def protect_source():
    subprocess.run(
        ["git", "checkout", "HEAD", "--", "agent/compiler/", "agent/executor/executors/"],
        capture_output=True,
    )


def _load_tasks(ids):
    tasks = []
    for name in sorted(os.listdir(LH_DIR)):
        task_file = os.path.join(LH_DIR, name, "task.json")
        if not os.path.exists(task_file):
            continue
        with open(task_file) as f:
            t = json.load(f)
        if ids and t["id"] not in ids:
            continue
        tasks.append(t)
    return tasks


def _run_one(task: dict) -> dict:
    """跑一个 Long Horizon 任务，返回结构化结果。"""
    from agent.runtime import UniversalAgent

    protect_source()
    buf = io.StringIO()
    t0 = time.perf_counter()
    error = ""
    try:
        with contextlib.redirect_stdout(buf):
            agent = UniversalAgent(user_id=f"lh_{task['id']}")
            answer = asyncio.run(agent.run(task["prompt"]))
    except Exception as e:
        answer = ""
        error = f"{type(e).__name__}: {str(e)[:200]}"
    elapsed = round(time.perf_counter() - t0, 1)
    trace = buf.getvalue()

    # verify（确定性，ADR-0009）
    import importlib.util
    spec = importlib.util.spec_from_file_location("lh_verify", task["verify"])
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    ok, reasons = mod.verify()

    # Recovery Count（确定性统计：trace 中的恢复信号）
    recovery_count = (
        trace.count("重规划") + trace.count("Runtime 捕获异常")
        + trace.count("连续失败，兜底") + trace.count("🔄 失败后不结束")
    )

    # Context Drift：计划阶段的目标 vs 最终落地（grounding_targets 是否全部被触碰）
    touched = sum(
        1 for g in task.get("grounding_targets", [])
        if g in trace
    )
    total_grounding = max(len(task.get("grounding_targets", [])), 1)
    context_drift = round(1.0 - touched / total_grounding, 3)

    # 完整 trace 落盘（供 Fail Board / 诊断，Long Horizon 集成分析用）
    os.makedirs(os.path.join("evaluation", "history"), exist_ok=True)
    with open(os.path.join("evaluation", "history", f"lh_{task['id']}.trace.txt"), "w") as tf:
        tf.write(trace)

    return {
        "id": task["id"],
        "completion": ok,
        "verify_reasons": reasons,
        "recovery_count": recovery_count,
        "context_drift": context_drift,
        "latency_ms": elapsed * 1000,
        "error": error,
        "answer_ok": bool(answer),
        "trace_tail": trace[-500:],
    }


def main():
    ids = sys.argv[1:]
    tasks = _load_tasks(ids)
    bootstrap_once()

    results = []
    for t in tasks:
        print(f"\n== {t['id']}（Long Horizon · Integration） ==", flush=True)
        r = _run_one(t)
        results.append(r)
        print(f"  completion={r['completion']} recovery={r['recovery_count']} "
              f"drift={r['context_drift']} latency={r['latency_ms']:.0f}ms "
              f"error={r['error'][:80] or '-'}", flush=True)
        for reason in r["verify_reasons"]:
            print(f"    ✗ {reason}")

    m = MetricsV2()
    n = len(results)
    if n:
        m.completion_rate = sum(1 for r in results if r["completion"]) / n
        m.recovery_count = sum(r["recovery_count"] for r in results) / n
        m.context_drift = sum(r["context_drift"] for r in results) / n
        m.latency_ms = sum(r["latency_ms"] for r in results) / n

    print("\nLong Horizon Metrics (v2.0-B Integration Baseline)")
    for k, v in m.to_dict().items():
        if v > 0.0:
            print(f"  {k:20s} {v:.3f}")

    # 写入 Capability Progress Curve（Trend Gate 基线）+ 历史序列
    curve = {}
    if os.path.exists(PROGRESS):
        with open(PROGRESS) as f:
            curve = json.load(f)
    curve.setdefault("extra", {}).setdefault("lh_history", []).append(m.to_dict())
    curve["lh_metrics"] = m.to_dict()
    curve["results"] = results
    with open(PROGRESS, "w") as f:
        json.dump(curve, f, ensure_ascii=False, indent=2)
    print(f"\nProgress Curve 已写入 {PROGRESS}（long-horizon history 已追加）")

    return 0


if __name__ == "__main__":
    sys.exit(main())
