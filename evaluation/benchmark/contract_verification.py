#!/usr/bin/env python3
"""contract_verification — Resolver Contract Verification（v1.2C）。

验证 Resolver Contract 冻结（ADR-0008）可执行化，四部分：

    ✓ public fields          （ResolutionCandidate / ResolutionResult 字段名 + 类型）
    ✓ public methods         （ResolutionTimeline / ResolutionResult 方法签名）
    ✓ function signature     （merge_candidates / resolve_candidates 签名）
    ✓ serialization schema   （ResolutionResult.to_json() 的 Schema Hash）

任何变化（新增/删除字段、改类型、改签名、改序列化 schema）→ FAIL。
这是冻结的硬约束：比 ADR 文本更强。

用法：
    python evaluation/benchmark/contract_verification.py            # 校验（对比基线）
    python evaluation/benchmark/contract_verification.py --refresh  # 更新基线（仅契约变更时）
"""
import dataclasses
import hashlib
import inspect
import json
import os
import sys

sys.path.insert(0, ".")

from agent.cognition.cognitive_context import (
    ResolutionCandidate,
    ResolutionResult,
    ResolutionTimeline,
)
from agent.cognition.reference_resolver import ReferenceResolver

BASELINE_PATH = "evaluation/contract_baseline.json"


def _contract_snapshot() -> dict:
    """契约当前快照（四部分）。"""
    snap = {}

    # 1. public fields（dataclass 字段名 + 类型）
    for cls in (ResolutionCandidate, ResolutionResult):
        snap[f"{cls.__name__}.fields"] = {
            f.name: str(f.type).replace("typing.", "") for f in dataclasses.fields(cls)
        }

    # 2. public methods（Timeline / Result 方法签名）
    for name in ("push", "latest", "history", "iter_reverse", "__len__", "__iter__"):
        snap[f"ResolutionTimeline.{name}.signature"] = str(inspect.signature(getattr(ResolutionTimeline, name)))
    for name in ("to_json", "to_resolved_query", "resolution_trace", "entities"):
        target = getattr(ResolutionResult, name, None)
        if target is not None:
            sig = inspect.signature(target) if callable(target) else "property"
            snap[f"ResolutionResult.{name}.signature"] = str(sig)

    # 3. function signature（Pipeline 纯函数）
    snap["merge_candidates.signature"] = str(inspect.signature(ReferenceResolver.merge_candidates))
    snap["resolve_candidates.signature"] = str(inspect.signature(ReferenceResolver.resolve_candidates))
    snap["resolve.signature"] = str(inspect.signature(ReferenceResolver.resolve))

    # 4. serialization schema（to_json 的固定 key 集 + 类型）
    r = ResolutionResult(kind="symbol", target="X", symbol="X", confidence=0.5, trace="t", raw="r")
    snap["ResolutionResult.to_json.keys"] = sorted(r.to_json().keys())
    snap["ResolutionResult.to_json.schema"] = {
        k: type(v).__name__ for k, v in r.to_json().items()
    }

    return snap


def _digest(snapshot: dict) -> str:
    return hashlib.sha256(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2).encode()
    ).hexdigest()


def verify() -> bool:
    """对比基线。True = 契约冻结保持；False = 契约变化。"""
    snapshot = _contract_snapshot()
    digest = _digest(snapshot)
    if not os.path.exists(BASELINE_PATH):
        _write_baseline(snapshot)
        print(f"Contract Verification: 基线不存在，已创建（{BASELINE_PATH}）")
        return True

    with open(BASELINE_PATH) as f:
        baseline = json.load(f)

    if baseline.get("digest") == digest:
        print("Contract Verification: PASS（fields + methods + signature + schema 全冻结）")
        return True

    print("Contract Verification: FAIL —— Resolver Contract 发生变化！")
    print("以下契约对象被修改：")
    for key in sorted(set(baseline.get("snapshot", {})) | set(snapshot)):
        old = baseline.get("snapshot", {}).get(key)
        new = snapshot.get(key)
        if old != new:
            print(f"  ✗ {key}:")
            print(f"      before: {old}")
            print(f"      after : {new}")
    print("提示：若这是有意的契约变更，运行 --refresh 更新基线（需 ADR-0008 评审）。")
    return False


def _write_baseline(snapshot: dict) -> None:
    data = {"digest": _digest(snapshot), "snapshot": snapshot}
    with open(BASELINE_PATH, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)


def main():
    if "--refresh" in sys.argv:
        _write_baseline(_contract_snapshot())
        print(f"Contract Verification: 基线已刷新（{BASELINE_PATH}）")
        return 0
    return 0 if verify() else 1


if __name__ == "__main__":
    sys.exit(main())
