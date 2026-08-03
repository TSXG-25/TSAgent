#!/usr/bin/env python3
"""plan_validator — Structural Validator（v2.0-A Planning Quality）。

Structural 层检查"有没有"（与 Semantic 层的"对不对"分离）：

    - Goal          → tasks 非空、每个 task 有 goal
    - Task          → id 唯一、verb/target/target_type 合法、success_condition 存在
    - Dependency    → dependencies 引用存在、DAG 无环
    - Order         → 数组顺序满足依赖（被依赖者在前）

Structural Validator 不依赖任何 LLM / Dataset 语义，纯结构检查。
后续 SQL / Browser / Coding Planner 全部复用本层（ADR 原则：Structural 可跨领域复用，
只有 Semantic Dataset 不同）。

注意：本文件**只读不改**。所有检查确定性、可回归。
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# 与 agent/planner/schemas.py 的 Task.verb 枚举保持一致（ADR-0001 核心 Task 契约）
VALID_VERBS = {
    "read", "write", "modify", "execute", "search", "list",
    "explain", "delete", "move", "resolve",
}
VALID_TARGET_TYPES = {"file", "symbol", "text", "none"}


@dataclass
class StructuralReport:
    """Structural 校验结果。valid=False 时 errors 说明违反的具体结构约束。"""
    valid: bool
    errors: List[str] = field(default_factory=list)
    checks: Dict[str, bool] = field(default_factory=dict)
    task_count: int = 0
    has_cycle: bool = False


def validate_structural(plan: dict) -> StructuralReport:
    """校验 planner 输出的 plan（TaskList 结构）。plan 为 dict 形式（含 tasks 列表）。

    Args:
        plan: {"tasks": [{"id", "verb", "target", "target_type", "goal",
                          "description", "success_condition", "dependencies"}], ...}

    Returns:
        StructuralReport：valid + 每项 check 布尔 + 错误明细。
    """
    report = StructuralReport(valid=True, task_count=0)

    if not isinstance(plan, dict) or not isinstance(plan.get("tasks"), list):
        report.valid = False
        report.errors.append("plan.tasks 缺失或不是列表")
        return report

    tasks = plan["tasks"]
    report.task_count = len(tasks)

    # ── Goal / Task 基础结构 ──
    report.checks["tasks_nonempty"] = len(tasks) > 0
    if not report.checks["tasks_nonempty"]:
        report.valid = False
        report.errors.append("task 列表为空（Goal 缺失 → FAIL）")

    ids = []
    for i, t in enumerate(tasks):
        tid = (t or {}).get("id", "")
        # id 唯一性
        if tid in ids:
            report.valid = False
            report.errors.append(f"task[{i}] id 重复: {tid}")
        ids.append(tid)

        if not tid:
            report.valid = False
            report.errors.append(f"task[{i}] id 为空")

        # verb 合法枚举
        verb = (t or {}).get("verb", "")
        if verb not in VALID_VERBS:
            report.valid = False
            report.errors.append(f"task[{i}] 非法 verb: {verb!r}（合法: {sorted(VALID_VERBS)}）")

        # target_type 合法
        tt = (t or {}).get("target_type", "")
        if tt not in VALID_TARGET_TYPES:
            report.valid = False
            report.errors.append(f"task[{i}] 非法 target_type: {tt!r}")

        # file/symbol 类型必须携带具体 target
        if tt in ("file", "symbol") and not (t or {}).get("target", ""):
            report.valid = False
            report.errors.append(f"task[{i}] target_type={tt} 但 target 为空")

        # goal / success_condition 必须存在
        if not (t or {}).get("goal", ""):
            report.valid = False
            report.errors.append(f"task[{i}] goal 为空")
        if not (t or {}).get("success_condition", ""):
            report.valid = False
            report.errors.append(f"task[{i}] success_condition 为空")

    # ── Dependency：引用存在 + DAG 无环 + 顺序满足 ──
    report.checks["deps_exist"] = True
    report.checks["deps_acyclic"] = True
    report.checks["deps_ordered"] = True

    id_set = set(ids)
    for i, t in enumerate(tasks):
        for dep in (t or {}).get("dependencies", []) or []:
            if dep not in id_set:
                report.checks["deps_exist"] = False
                report.valid = False
                report.errors.append(f"task[{i}] 依赖不存在: {dep}")
            # 顺序约束：被依赖的 task 必须排在被依赖者之前
            if dep in id_set and dep not in ids[:i]:
                report.checks["deps_ordered"] = False
                report.valid = False
                report.errors.append(f"task[{i}] 依赖 {dep} 未排在前面（顺序违反）")

    # DAG 环检测（DFS 三色标记）
    graph = {tid: list((t or {}).get("dependencies", []) or []) for tid, t in zip(ids, tasks)}
    color = {tid: 0 for tid in ids}  # 0=白 1=灰 2=黑

    def _has_cycle(node: str) -> bool:
        color[node] = 1
        for nxt in graph.get(node, []):
            if color.get(nxt, 0) == 1:
                return True
            if color.get(nxt, 0) == 0 and _has_cycle(nxt):
                return True
        color[node] = 2
        return False

    for tid in ids:
        if color[tid] == 0 and _has_cycle(tid):
            report.checks["deps_acyclic"] = False
            report.has_cycle = True
            report.valid = False
            report.errors.append(f"依赖存在环: {tid}")
            break

    return report
