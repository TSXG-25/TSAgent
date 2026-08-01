#!/usr/bin/env python3
"""Benchmark runner — 执行任务、捕获 step trace、运行外部验证。

用法:
    python benchmarks/runner.py            # 跑全部 8 个任务
    python benchmarks/runner.py T001 T003  # 跑指定任务

输出（benchmarks/_fixtures/out/）:
    <task>.json        完整结果（含 trace、answer、verify 输出）
    <task>.answer.txt  最终答案（供答案型 verify 使用）

验证是外部诚实判据：verify 脚本独立于 agent 执行链，
pytest / curl / 代码检查，与 agent 自己的判断无关。
"""
import asyncio
import contextlib
import io
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

BASE = os.path.dirname(os.path.abspath(__file__))
TASKS_DIR = os.path.join(BASE, "tasks")
OUT_DIR = os.path.join(BASE, "_fixtures", "out")


def load_tasks():
    tasks = []
    for fn in sorted(os.listdir(TASKS_DIR)):
        if fn.endswith(".json"):
            with open(os.path.join(TASKS_DIR, fn)) as f:
                tasks.append(json.load(f))
    return tasks


def bootstrap_once():
    """初始化运行时（workspace + tools + skills + workflows + capabilities）。

    必须在执行任何任务前调用一次——agent 依赖 WorkspaceService 与 ToolRegistry。
    返回后打印启动耗时概要。
    """
    from agent.bootstrap import (
        init_workspace, load_all_tools, load_all_skills, load_all_workflows,
    )

    init_workspace()
    load_all_tools()
    load_all_skills()
    load_all_workflows()
    from agent.registry.capability_registry import register_default_capabilities
    register_default_capabilities()
    print("  ✅ benchmarks runtime initialized", flush=True)


def run_single(task):
    from agent.runtime import UniversalAgent

    task_id = task["id"]
    print(f"\n===== {task_id} {task['name']} ({task.get('category','')}) =====", flush=True)

    # 捕获 agent 运行 stdout 作为 step trace
    buf = io.StringIO()
    t0 = time.time()
    error = ""
    try:
        with contextlib.redirect_stdout(buf):
            agent = UniversalAgent(user_id=f"bench_{task_id}")
            answer = asyncio.run(agent.run(task["prompt"]))
    except Exception as e:
        answer = ""
        error = f"{type(e).__name__}: {e}"
    elapsed = round(time.time() - t0, 1)
    trace = buf.getvalue()

    # 写答案供答案型 verify 使用
    answer_path = os.path.join(OUT_DIR, f"{task_id}.answer.txt")
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(answer_path, "w") as f:
        f.write(answer or "")

    # 运行外部验证（诚实判据）
    verify_ok = False
    verify_out = ""
    try:
        r = subprocess.run(
            [sys.executable, os.path.join(ROOT, task["verify"]), answer_path],
            capture_output=True, text=True, timeout=120,
        )
        verify_ok = r.returncode == 0
        verify_out = ((r.stdout or "") + (r.stderr or ""))[-900:]
    except Exception as e:
        verify_out = f"verify error: {e}"

    result = {
        "task": task_id,
        "name": task["name"],
        "category": task.get("category", ""),
        "result": "pass" if verify_ok else "fail",
        "answer": (answer or "")[:500],
        "verify_output": verify_out,
        "elapsed_s": elapsed,
        "error": error,
        "trace": trace[-8000:],
        "failure_category": [],
        "first_wrong_step": None,
    }

    out_path = os.path.join(OUT_DIR, f"{task_id}.json")
    with open(out_path, "w") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"  → {result['result']} ({elapsed}s) verify_ok={verify_ok}")
    return result


def main():
    args = sys.argv[1:]
    tasks = load_tasks()
    if args:
        ids = set(args)
        tasks = [t for t in tasks if t["id"] in ids]

    os.makedirs(OUT_DIR, exist_ok=True)
    bootstrap_once()
    results = [run_single(t) for t in tasks]

    passed = sum(1 for r in results if r["result"] == "pass")
    print(f"\n===== SUMMARY: {passed}/{len(results)} passed =====")
    for r in results:
        print(f"  {r['task']}: {r['result']} ({r['elapsed_s']}s)")


if __name__ == "__main__":
    main()
