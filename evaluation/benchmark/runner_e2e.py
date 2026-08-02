#!/usr/bin/env python3
"""runner_e2e — 跑 E2E Conversation Dataset（真实 Agent）。

对每条 conversation：
- 跑 UniversalAgent.run(input)（真实 LLM）
- 断言 answer_contains / answer_not_contains / 不崩溃
- 记录 trace 供 Fail Board 归因

用法:
    python evaluation/benchmark/runner_e2e.py [001 002 ...]   # 指定或全部
输出: evaluation/history/e2e_<date>.json
"""
import asyncio
import contextlib
import glob
import io
import json
import os
import sys
import time
from datetime import date

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

from agent.bootstrap import (
    init_workspace, load_all_tools, load_all_skills, load_all_workflows,
)
from agent.registry.capability_registry import register_default_capabilities

CHAT_DIR = os.path.join("evaluation", "datasets", "conversation", "chat")
HISTORY = os.path.join("evaluation", "history")


def bootstrap_once():
    init_workspace()
    load_all_tools()
    load_all_skills()
    load_all_workflows()
    register_default_capabilities()
    print("  ✅ e2e runtime initialized", flush=True)


def run_one(c):
    from agent.runtime import UniversalAgent

    buf = io.StringIO()
    t0 = time.perf_counter()
    error = ""
    try:
        with contextlib.redirect_stdout(buf):
            agent = UniversalAgent(user_id=f"e2e_{c['id']}")
            answer = asyncio.run(agent.run(c["input"]))
    except Exception as e:
        answer = ""
        error = f"{type(e).__name__}: {str(e)[:200]}"
    elapsed = round(time.perf_counter() - t0, 1)
    trace = buf.getvalue()

    exp = c.get("expected", {})
    checks = []
    ok = True
    for kw in exp.get("answer_contains", []):
        hit = kw in (answer or "")
        ok = ok and hit
        checks.append((f"contains:{kw}", hit))
    for kw in exp.get("answer_not_contains", []):
        hit = (kw or "") not in (answer or "")
        ok = ok and hit
        checks.append((f"not:{kw}", hit))
    if error:
        ok = False
        checks.append(("no_crash", False))

    return {
        "id": c["id"],
        "input": c["input"],
        "ok": ok,
        "answer": (answer or "")[:250],
        "error": error,
        "elapsed_s": elapsed,
        "checks": checks,
        "trace": trace[-2500:],
    }


def main():
    args = sys.argv[1:]
    bootstrap_once()

    files = sorted(glob.glob(os.path.join(CHAT_DIR, "*.json")))
    results = []
    for fn in files:
        with open(fn) as f:
            c = json.load(f)
        if args and not any(a in c["id"] for a in args):
            continue
        print(f"\n===== {c['id']}: {c['input']} =====", flush=True)
        r = run_one(c)
        results.append(r)
        print(f"  → {'PASS' if r['ok'] else 'FAIL'} ({r['elapsed_s']}s) {r['answer'][:120]}")

    passed = sum(1 for r in results if r["ok"])
    print(f"\n===== E2E: {passed}/{len(results)} passed =====")

    os.makedirs(HISTORY, exist_ok=True)
    path = os.path.join(HISTORY, f"e2e_{date.today().isoformat()}.json")
    with open(path, "w") as f:
        json.dump({"passed": passed, "total": len(results), "results": results},
                  f, ensure_ascii=False, indent=2)
    print(f"saved: {path}")


if __name__ == "__main__":
    main()
