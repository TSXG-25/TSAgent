#!/usr/bin/env python3
"""Memory Fuzz runner — 真实 LLM 运行记忆用例并记录结果（v2.1B-0）。

用法:
    python -B benchmarks/memory/runner.py [--n 10] [--fill 4]
    python -B benchmarks/memory/runner.py --benchmark continuation
环境变量:
    MEMORY_RESULTS  结果 JSON 输出路径（默认 /private/tmp/memory_results.json）
"""
import argparse
import asyncio
import json
import os
import re
import shutil
import sys
import tempfile
import time
import unicodedata
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

os.environ["TRANSFORMERS_NO_TF"] = "1"
os.environ["TF_CPP_MIN_LOG_LEVEL"] = "3"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

from agent.bootstrap import load_all
from agent.session_runtime import SessionRuntime
from benchmarks.memory.cases import build_cases, build_continuation_cases, MemoryCase
from benchmarks.memory.metadata import benchmark_metadata

RESULTS_PATH = os.environ.get("MEMORY_RESULTS", "/private/tmp/memory_results.json")
TURN_TIMEOUT = float(os.environ.get("MEMORY_TURN_TIMEOUT", "120"))


# ── Output hygiene（v2.1B）：每 case 备份/恢复 output/，避免执行产物跨 case 累积 ──

def _backup_output():
    """备份当前 output/ 到临时目录；无 output/ 时返回 None。"""
    src = Path("output")
    if not src.exists():
        return None
    tmp = tempfile.mkdtemp(prefix="mem-out-")
    shutil.copytree(src, Path(tmp) / "output")
    return tmp


def _restore_output(backup_tmp):
    """把 output/ 恢复为备份状态（删除 case 新建文件、还原被覆盖的原文件）。"""
    if not backup_tmp:
        return
    src = Path("output")
    backup = Path(backup_tmp) / "output"
    if src.exists():
        shutil.rmtree(src)
    shutil.copytree(backup, src)
    shutil.rmtree(backup_tmp)


def _expected_set(case: MemoryCase) -> list:
    """Return a flat compatibility view of expected answer terms."""
    return [term for group in _expected_groups(case) for term in group]


def _expected_groups(case: MemoryCase) -> list[list[str]]:
    """Normalize legacy ``expected`` and new ``expected_any_of`` schemas.

    Each inner list contains synonyms for one acceptable answer; any group may
    match. The current legacy list is one synonym group.
    """
    configured = getattr(case, "expected_any_of", None)
    if configured is not None:
        groups = configured
    else:
        exp = case.expected
        groups = [[exp]] if isinstance(exp, str) else [list(exp or [])]
    normalized = []
    for group in groups:
        if isinstance(group, str):
            group = [group]
        terms = [str(term) for term in group if str(term).strip()]
        if terms:
            normalized.append(terms)
    return normalized


def _forbidden_groups(case: MemoryCase) -> list[list[str]]:
    configured = getattr(case, "forbidden_any_of", None) or []
    groups = []
    for group in configured:
        if isinstance(group, str):
            group = [group]
        terms = [str(term) for term in group if str(term).strip()]
        if terms:
            groups.append(terms)
    return groups


def canonicalize(text: str) -> str:
    """Normalize Unicode width, case and whitespace before matching."""
    normalized = unicodedata.normalize("NFKC", str(text or "")).casefold()
    return re.sub(r"\s+", " ", normalized).strip()


def _matches_groups(answer: str, groups: list[list[str]]) -> str | None:
    for group in groups:
        for term in group:
            canonical_term = canonicalize(term)
            if canonical_term and canonical_term in answer:
                return term
    return None


def _validate_runtime_contract(case: MemoryCase, evidence: dict | None) -> tuple:
    """Validate structured Runtime evidence for a continuation case."""
    if not evidence:
        return False, "missing_runtime_evidence"
    expected = case.contract_expectations or {}
    intent = expected.get("intent", "")
    if intent and evidence.get("conversation_intent") != intent:
        return False, (
            f"intent={intent!r} actual={evidence.get('conversation_intent')!r}"
        )
    if "requires_execution" in expected:
        actual_exec = bool(evidence.get("requires_execution"))
        if actual_exec != bool(expected["requires_execution"]):
            return False, f"requires_execution={actual_exec!r}"
    if expected.get("progress_required") and not evidence.get("execution_progress"):
        return False, "execution_progress=0"
    if expected.get("verification_required") and not evidence.get("verified_success"):
        return False, "execution_verifier=not_successful"
    if expected.get("last_answer_required") and not evidence.get("previous_answer"):
        return False, "last_answer=missing"
    anchor = expected.get("answer_anchor", "")
    if anchor and canonicalize(anchor) not in canonicalize(evidence.get("answer", "")):
        return False, f"answer_anchor={anchor!r} missing"
    target = expected.get("reference_target", "")
    if target and canonicalize(target) not in canonicalize(evidence.get("resolved_target", "")):
        return False, (
            f"reference_target={target!r} "
            f"actual={evidence.get('resolved_target', '')!r}"
        )
    return True, "runtime_contract_ok"


def summarize_results(results: list[dict]) -> dict:
    """Aggregate results without mixing recall and continuation semantics.

    Continuation is reported by ``plan_resume``/``chat_resume``/
    ``reference_resume``. Text-memory cases retain their ``group/sub`` label.
    Exception counts are kept separately so a network failure cannot look like
    a capability regression.
    """
    buckets: dict[tuple[str, str], dict[str, int]] = {}
    for result in results:
        scope = str(result.get("metric_scope", "memory_recall"))
        label = (
            str(result.get("sub", "unknown"))
            if scope == "continuation"
            else f"{result.get('group', 'unknown')}/{result.get('sub', 'unknown')}"
        )
        bucket = buckets.setdefault(
            (scope, label),
            {"total": 0, "passed": 0, "exceptions": 0},
        )
        bucket["total"] += 1
        bucket["passed"] += int(bool(result.get("passed")))
        bucket["exceptions"] += int(bool(result.get("exc")))

    metrics = []
    for (scope, label), bucket in sorted(buckets.items()):
        total = bucket["total"]
        non_exception = total - bucket["exceptions"]
        metrics.append({
            "metric_scope": scope,
            "metric": label,
            **bucket,
            "non_exception": non_exception,
            "pass_rate": round(bucket["passed"] / total * 100, 1) if total else 0.0,
            "non_exception_pass_rate": round(
                bucket["passed"] / non_exception * 100, 1
            ) if non_exception else None,
        })
    return {"metrics": metrics}


def validate(case: MemoryCase, answer: str, evidence: dict | None = None) -> tuple:
    """Return ``(passed, detail)`` under the case-local ADR-0014 contract."""
    if case.validation_mode == "runtime_contract":
        return _validate_runtime_contract(case, evidence)
    ans = canonicalize(answer)
    if not ans.strip():
        return False, "EMPTY"
    forbidden = _matches_groups(ans, _forbidden_groups(case))
    if forbidden is not None:
        return False, f"forbidden={forbidden!r} out={ans[:120]!r}"
    expected = _matches_groups(ans, _expected_groups(case))
    if expected is not None:
        return True, "ok"
    return False, f"missing={_expected_set(case)!r} out={ans[:120]!r}"


async def run_case(
    case: MemoryCase,
    user_id: str,
    *,
    session: SessionRuntime | None = None,
    session_mode: str = "isolated",
    keep_output: bool = False,
) -> dict:
    """Run one case inside an explicit session lifecycle.

    The default creates and destroys one isolated SessionRuntime per case.
    ``session`` is only supplied by the opt-in persistent benchmark mode.
    """
    owned_session = session is None
    backup_tmp = _backup_output() if (keep_output is False and owned_session) else None
    active_session = session or SessionRuntime.create(
        session_id=user_id,
        user_id=user_id,
        persistent=False,
    )
    out = ""
    dur = 0.0
    exc = None
    evidence = {}
    try:
        for text in case.turns:
            t0 = time.perf_counter()
            try:
                out = await asyncio.wait_for(active_session.run(text), timeout=TURN_TIMEOUT)
                evidence = dict(getattr(active_session.agent, "last_run_evidence", {}) or {})
            except Exception as e:
                exc = f"{type(e).__name__}: {e}"
                break
            dur += time.perf_counter() - t0
    finally:
        if owned_session:
            active_session.destroy()
        if backup_tmp:
            _restore_output(backup_tmp)
    passed, detail = (
        (False, f"EXC {exc}")
        if exc
        else validate(case, out, evidence=evidence)
    )
    return {
        "id": case.id, "group": case.group, "sub": case.sub,
        "turns": len(case.turns), "expected": case.expected,
        "session_id": active_session.session_id,
        "user_id": active_session.user_id,
        "continuation_contract": case.continuation_contract,
        "validation_mode": case.validation_mode,
        "contract_expectations": case.contract_expectations,
        "metric_scope": case.metric_scope,
        "session_mode": session_mode,
        "passed": passed, "detail": detail,
        "dur": round(dur, 1), "out": str(out or "")[:200],
        "evidence": evidence,
        "exc": exc,
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=10, help="每个子类用例数")
    parser.add_argument("--fill", type=int, default=4, help="干扰填充轮数")
    parser.add_argument("--limit", type=int, default=0, help="仅跑前 N 个（调试用）")
    parser.add_argument(
        "--benchmark",
        choices=("memory", "continuation"),
        default="memory",
        help="memory=完整记忆集；continuation=独立 plan/chat/reference 集",
    )
    parser.add_argument("--groups", default="", help="仅跑指定组（逗号分隔）")
    parser.add_argument("--keep-output", action="store_true",
                        help="保留 output/ 执行产物（默认 isolated 模式每 case 后恢复）")
    parser.add_argument(
        "--session-mode",
        choices=("isolated", "persistent"),
        default="isolated",
        help="isolated=每个 case 独立会话（默认）；persistent=所有 case 共用会话（需专门数据集）",
    )
    args = parser.parse_args()

    if args.benchmark == "continuation":
        cases = build_continuation_cases(n_per_sub=args.n)
        benchmark_name = "conversation-continuation"
    else:
        cases = build_cases(n_per_sub=args.n, fill_turns=args.fill)
        benchmark_name = "memory-fuzz"
    if args.groups:
        _keep = {g.strip() for g in args.groups.split(",") if g.strip()}
        cases = [c for c in cases if c.group in _keep]
    if args.limit:
        cases = cases[: args.limit]
    metadata = benchmark_metadata(cases, benchmark_name=benchmark_name)
    print(
        f"Memory Fuzz {metadata['benchmark_version']}: {len(cases)} cases "
        f"dataset={metadata['dataset_hash'][:12]}",
        flush=True,
    )

    load_all()
    print("boot done", flush=True)

    results = []
    t_start = time.perf_counter()
    shared_session = None
    if args.session_mode == "persistent":
        shared_session = SessionRuntime.create(
            session_id="memory-benchmark-persistent",
            user_id="memory-benchmark-persistent",
            persistent=True,
        )
    try:
        for idx, case in enumerate(cases):
            user_id = f"mem_{case.id}"
            rec = await run_case(
                case,
                user_id,
                session=shared_session,
                session_mode=args.session_mode,
                keep_output=args.keep_output,
            )
            results.append(rec)
            if (idx + 1) % 10 == 0:
                with open(RESULTS_PATH, "w", encoding="utf-8") as f:
                    json.dump({
                        **metadata,
                        **summarize_results(results),
                        "partial": True,
                        "results": results,
                    }, f, ensure_ascii=False, indent=1)
            mark = "PASS" if rec["passed"] else "FAIL"
            print(f"[{idx:03d}] {case.id} [{case.group}/{case.sub}] {mark} ({rec['dur']}s) {rec['detail'][:80]}", flush=True)
    finally:
        if shared_session is not None:
            shared_session.destroy(purge_facts=False)

    elapsed = time.perf_counter() - t_start
    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump({
            **metadata,
            **summarize_results(results),
            "elapsed_min": round(elapsed / 60, 1),
            "results": results,
        }, f, ensure_ascii=False, indent=1)
    for metric in summarize_results(results)["metrics"]:
        print(
            "METRIC "
            f"[{metric['metric_scope']}/{metric['metric']}] "
            f"{metric['passed']}/{metric['total']} passed; "
            f"exceptions={metric['exceptions']}; "
            f"non_exception_pass_rate={metric['non_exception_pass_rate']}%",
            flush=True,
        )
    print(f"DONE {len(results)} cases in {elapsed/60:.1f} min", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
