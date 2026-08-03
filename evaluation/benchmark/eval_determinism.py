#!/usr/bin/env python3
"""eval_determinism — Resolver Determinism（v1.2B 正式指标）。

定义：
    相同输入 + 相同上下文，连续跑 N 次（默认 100），ResolutionResult 完全一致。

双 Hash（CI 可区分"输出一样" vs "推理路径一样"）：
    Result Hash：sha256(kind / target / symbol / confidence)
    Trace Hash ：sha256(trace，推理路径)

目标：100%。Resolver 本质是确定性的（无 LLM 随机性）。
任一 hash 漂移 → Determinism FAIL（回归一眼可读）。

方法：
    1. 每场景独立 engine，先正常跑一遍累积 ConversationState（记录每轮 state 快照）。
    2. 对每个 turn，用 deepcopy 的 state 快照重建相同上下文，连续跑 RUNS 次。
    3. 比较每次的 Result Hash / Trace Hash。
"""
import copy
import hashlib
import json
import os
import sys

sys.path.insert(0, ".")

from agent.cognition.cognitive_context import CognitiveContext, ConversationState
from agent.cognition.reference_resolver import ReferenceResolver

MT_DIR = "evaluation/datasets/conversation/multiturn"
RUNS = int(os.environ.get("DETERMINISM_RUNS", "100"))


def _sha256(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def _fingerprint(result) -> dict:
    """拆成 Result Hash 与 Trace Hash 的输入。"""
    j = result.to_json()
    return {
        "result": {k: v for k, v in j.items() if k != "trace"},
        "trace": j["trace"],
    }


def main():
    total_scenarios = 0
    ok_scenarios = 0
    for fn in sorted(os.listdir(MT_DIR)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(MT_DIR, fn)) as f:
            scenario = json.load(f)

        # ── 第一遍：正常跑，记录每轮 state 快照（确定性重放基线）──
        resolver = ReferenceResolver()
        state = ConversationState()
        snapshots = []
        for turn in scenario["turns"]:
            inp = turn["input"]
            ctx = CognitiveContext(query=inp, conversation_state=state, conversation=[])
            resolved = resolver.resolve(inp, ctx)
            snapshots.append((inp, copy.deepcopy(state)))
            state.record(resolved)

        # ── 第二遍：每个 turn 用相同上下文连续跑 RUNS 次 ──
        scenario_ok = True
        print(f"\n== {scenario['id']} == (runs={RUNS})")
        for inp, snapshot in snapshots:
            result_hashes = set()
            trace_hashes = set()
            for _ in range(RUNS):
                st = copy.deepcopy(snapshot)
                ctx = CognitiveContext(query=inp, conversation_state=st, conversation=[])
                r = resolver.resolve(inp, ctx)
                fp = _fingerprint(r)
                result_hashes.add(_sha256(fp["result"]))
                trace_hashes.add(_sha256(fp["trace"]))
            r_ok = len(result_hashes) == 1
            t_ok = len(trace_hashes) == 1
            scenario_ok = scenario_ok and r_ok and t_ok
            mark = "✓" if (r_ok and t_ok) else "✗"
            print(f"  {mark} '{inp}' Result={_sha256(fp['result'])[:8]} Trace={_sha256(fp['trace'])[:8]}")

        total_scenarios += 1
        ok_scenarios += 1 if scenario_ok else 0

    print(f"\nResolver Determinism: {ok_scenarios}/{total_scenarios} scenarios × {RUNS} runs")
    if ok_scenarios == total_scenarios:
        print("Determinism: PASS（Result Hash + Trace Hash 全一致）")
    else:
        print("Determinism: FAIL")


if __name__ == "__main__":
    main()
