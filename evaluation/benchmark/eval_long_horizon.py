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


def events_from_long_horizon(results: list, tasks: list) -> list:
    """把 Long Horizon 评估结果转成 FailureEvent（Diagnostic Backbone）。

    symptom 映射：
        verify 未通过           → wrong_answer（行为未达标）
        context_drift > 0       → context_drift（计划目标漂移）
        空 answer               → wrong_answer
    """
    from evaluation.benchmark.failboard_v2 import FailureEvent, Evidence
    events = []
    task_map = {t["id"]: t for t in tasks}
    for r in results:
        tid = r["id"]
        groundings = task_map.get(tid, {}).get("grounding_targets", [])
        if not r["completion"]:
            for reason in r.get("verify_reasons", []) or []:
                events.append(FailureEvent(
                    benchmark="long_horizon", scenario=tid, layer="long_horizon",
                    dimension="completion",
                    failure=f"verify 未通过: {reason[:90]}",
                    evidence=[Evidence(source="verify", location=tid,
                                       expected="全部验证项通过", actual=reason[:90])],
                    symptom="wrong_answer",
                ))
        if r.get("context_drift", 0) > 0:
            missed = [g for g in groundings if g not in (r.get("trace_tail", "") or "")]
            events.append(FailureEvent(
                benchmark="long_horizon", scenario=tid, layer="long_horizon",
                dimension="drift",
                failure=f"Context Drift {r['context_drift']:.2f}（目标未全部触碰）",
                evidence=[Evidence(source="trace", location=tid,
                                   expected=str(groundings), actual=f"漏: {missed or '无法判定'}")],
                symptom="context_drift",
            ))
        if not r.get("answer_ok"):
            events.append(FailureEvent(
                benchmark="long_horizon", scenario=tid, layer="long_horizon",
                dimension="completion",
                failure="无最终回答",
                evidence=[Evidence(source="runtime", location=tid,
                                   expected="产出最终答案", actual="空回答")],
                symptom="wrong_answer",
            ))
    return events


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

    # Diagnostic Backbone：Long Horizon 失败事件 → Fail Board v2
    from evaluation.benchmark.failboard_v2 import load, save
    board = load()
    lh_events = events_from_long_horizon(results, tasks)
    added = board.collect(lh_events)
    save(board)
    print(f"📋 Fail Board v2 新增 {added} 条 FailureEvent（Long Horizon 层）")

    return 0


if __name__ == "__main__":
    sys.exit(main())
