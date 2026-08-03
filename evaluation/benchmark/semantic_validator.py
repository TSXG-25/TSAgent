#!/usr/bin/env python3
"""semantic_validator — Semantic Validator（v2.0-A Planning Quality）。

Semantic 层检查"对不对"（与 Structural 层的"有没有"分离）：

    - Goal Coverage     → golden 目标是否被 plan 覆盖（target + 编辑动作）
    - Constraint        → 显式约束是否被遵守：
        no_web         → plan 中无网络相关 hint
        scope_only     → 写操作 target 限定在指定路径
        no_delete      → plan 中无 delete 动作
    - Abstention        → 信息不足场景应 Abstain / Ask User（Uncertainty 横切）

设计纪律（ADR-0009）：本文件全部检查**确定性**，禁止 LLM 参与判断。
设计纪律（ADR-0011）：Semantic Dataset 按领域不同而不同（SQL / Browser / Coding 各自提供），
但本层检查器骨架复用。当前实现面向 Coding / Repository 规划。
"""
from dataclasses import dataclass, field
from typing import Dict, List

from evaluation.benchmark.plan_validator import VALID_VERBS

# 网络相关 hint（no_web 约束判定）：出现在任何 task 字段即违反
WEB_HINTS = ("web", "http", "url", "fetch", "联网", "网络", "search_web", "browser")

# 编辑类 verb（scope_only 约束限定对象 / goal coverage 动作匹配）
WRITE_VERBS = {"write", "modify", "delete", "move"}
EDIT_VERBS = {"write", "modify"}


def _normalize_target(t: str) -> str:
    """路径归一化：去空白、去前导 ./、统一分隔符。强制 str 防御 PosixPath。"""
    return str(t or "").strip().replace("\\", "/").lstrip("./")


def _target_matches(plan_target: str, golden_target: str) -> bool:
    """确定性目标匹配：
    1) basename 相等（parser.py == src/parser.py）
    2) 前缀包含（golden 是 plan 的子串，处理目录形式）
    3) 完全相等
    4) 命令词匹配（execute 类）：golden 是短命令（pytest），plan 是完整命令
       （python -m pytest）——整词匹配，避免 "test" in "latest.py" 误匹配
    """
    import re as _re
    p = _normalize_target(plan_target)
    g = _normalize_target(golden_target)
    if not p or not g:
        return False
    if p == g:
        return True
    if p.endswith("/" + g) or g.endswith("/" + p):
        return True
    if p.rsplit("/", 1)[-1] == g.rsplit("/", 1)[-1]:
        return True
    # 命令词匹配：无扩展名、非路径的短命令 token
    if "/" not in g and "." not in g and _re.search(rf"\b{_re.escape(g)}\b", p):
        return True
    return False


@dataclass
class SemanticReport:
    """Semantic 校验结果。每维度独立 check，支持 Fail Board 归因。"""
    valid: bool
    errors: List[str] = field(default_factory=list)
    checks: Dict[str, bool] = field(default_factory=dict)
    # 细粒度归因（哪个 golden 目标漏覆盖 / 哪条约束被违反）
    missed_targets: List[str] = field(default_factory=list)
    violated_constraints: List[str] = field(default_factory=list)


def _plan_text(tasks: List[dict]) -> str:
    """把 plan 的动作对象拼成文本（verb + target），供 no_web 检查。

    只收**动作对象**（verb/target），不收 goal/description：
    说明文字中出现"网络/联网"（如"本地实现，不依赖网络"）是**遵守**约束，
    不是违反（ADR-0010：以行为为准，不以措辞为准）。
    """
    parts = []
    for t in tasks:
        parts.append((t or {}).get("verb", ""))
        parts.append((t or {}).get("target", ""))
    return " ".join(parts)

def validate_semantic(plan: dict, scenario: dict) -> SemanticReport:
    """校验 plan 是否满足 scenario 的语义要求（golden_tasks + constraints + abstention）。

    Args:
        plan: {"tasks": [...]}（planner 输出）
        scenario: dataset 场景 dict（含 golden_tasks / constraints / expect_abstention）

    Returns:
        SemanticReport：valid + checks + 归因明细。
    """
    report = SemanticReport(valid=True)
    tasks = plan.get("tasks", []) if isinstance(plan, dict) else []

    golden_tasks = scenario.get("golden_tasks", []) or []
    constraints = scenario.get("constraints", []) or []

    # ── Abstention（Uncertainty 横切）：信息不足场景 ──
    if scenario.get("expect_abstention"):
        # 期待 Abstain / Ask User：plan 必须为空或带 ask_user 标记
        abstained = len(tasks) == 0 or any(
            "ask" in ((t or {}).get("goal", "") + (t or {}).get("description", "")).lower()
            for t in tasks
        )
        report.checks["abstention"] = abstained
        if not abstained:
            report.valid = False
            report.errors.append(
                f"信息不足场景应 Abstain / Ask User，但 planner 给出了 {len(tasks)} 个任务（乱猜 → False Confidence）"
            )
        # Abstention 场景不继续做 goal/constraint 检查
        return report

    # ── Goal Coverage：每个 golden 目标必须有 plan task 覆盖（target + 编辑动作） ──
    report.checks["goal_coverage"] = True
    for gt in golden_tasks:
        gt_target = (gt or {}).get("target", "")
        gt_verb = (gt or {}).get("verb", "")
        covered = False
        for t in tasks:
            if not _target_matches((t or {}).get("target", ""), gt_target):
                continue
            if gt_verb in EDIT_VERBS:
                if (t or {}).get("verb") in WRITE_VERBS:
                    covered = True
                    break
            elif (t or {}).get("verb") == gt_verb:
                covered = True
                break
        if not covered:
            report.checks["goal_coverage"] = False
            report.valid = False
            report.missed_targets.append(gt_target)
            report.errors.append(f"Goal 未覆盖: {gt_target}（需 {gt_verb}）")

    # ── Constraint Detection：显式约束逐条检查 ──
    report.checks["constraints"] = True
    text = _plan_text(tasks)
    for c in constraints:
        ctype = (c or {}).get("type", "")
        if ctype == "no_web":
            hits = [h for h in WEB_HINTS if h in text.lower()]
            if hits:
                report.checks["constraints"] = False
                report.valid = False
                report.violated_constraints.append(f"no_web 违反: 出现 {hits}")
                report.errors.append(f"no_web 约束违反: 计划中出现 {hits}")
        elif ctype == "scope_only":
            path = _normalize_target((c or {}).get("path", ""))
            for t in tasks:
                verb = (t or {}).get("verb", "")
                if verb not in WRITE_VERBS:
                    continue
                tp = _normalize_target((t or {}).get("target", ""))
                # 允许精确文件（tests/test_x.py）或目录前缀（test/...）
                if not (tp == path or tp.startswith(path.rstrip("/") + "/")):
                    report.checks["constraints"] = False
                    report.valid = False
                    report.violated_constraints.append(
                        f"scope_only 违反: 写 {tp}（限 {path}/ 内）"
                    )
                    report.errors.append(
                        f"scope_only 约束违反: 写操作 {tp} 超出 {path}/"
                    )
        elif ctype == "no_delete":
            dels = [t for t in tasks if (t or {}).get("verb") == "delete"]
            if dels:
                report.checks["constraints"] = False
                report.valid = False
                report.violated_constraints.append("no_delete 违反: plan 含 delete 动作")
                report.errors.append("no_delete 约束违反: 计划中出现 delete 动作")

    # 无约束场景：约束检查视为通过（无约束可违反）
    if not constraints:
        report.checks["constraints"] = True

    return report

