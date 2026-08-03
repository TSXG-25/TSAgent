"""Reflection — 诊断器（v2.0-C）。

Reflection Contract（v2.0-C 约束 1）：
    reflect(event: FailureEvent) -> ReflectionResult    # 唯一入口
    - 输入只允许 FailureEvent（Fail Board 的 Evidence）
    - 禁止 reflect(runtime / context / history / messages)
    - Reflection 是 Fail Board 的消费者，不是第二个 Runtime

Reflection 只提出修正方案（v2.0-C 约束 2），不执行：
    Correction(action, reason, confidence)  ← Proposal
    Executor 决定是否执行（执行 / Ask User / Finish）

设计纪律：
    - 第一层诊断确定性（ADR-0009）：symptom → root_cause 由规则 + evidence 特征决定，无 LLM
    - 未来若允许 LLM Diagnosis，Diagnosis Verifier 仍保持确定
    - KPI：Diagnosis Accuracy / False Diagnosis Rate / Correction Proposal Accuracy
      （Recovery Improvement 属于集成 KPI，不属于 Reflection 自身）
"""
from dataclasses import dataclass, field
from typing import List

from evaluation.benchmark.failboard_v2 import FailureEvent, Evidence, SYMPTOM_MAP


@dataclass(frozen=True)
class Diagnosis:
    root_cause: str
    confidence: float
    evidence_matches: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class Correction:
    """修正方案（Proposal）——Executor 决定是否执行。"""
    action: str
    reason: str
    confidence: float


@dataclass(frozen=True)
class ReflectionResult:
    diagnosis: Diagnosis
    correction: Correction


# ── Correction 策略（root_cause → correction action，确定性） ──
_CORRECTION_MAP = {
    "tool":      "switch_tool",
    "grounding": "re_ground",
    "planning":  "replanning",
    "decision":  "re_decide",
    "prompt":    "enrich_prompt",
    "runtime":   "recover",
    "external":  "notify",
    "unknown":   "ask_user",
}


def _root_cause_from_evidence(ev: Evidence) -> str:
    """从单条 Evidence 推断 root_cause（确定性特征规则，specificity 优先）。"""
    src = (ev.source or "").lower()
    loc = (ev.location or "").lower()
    exp = (ev.expected or "").lower()
    act = (ev.actual or "").lower()

    # external：连接失败 / web_fetch / 外部服务
    if ("connectionerror" in act or "无法连接" in act or loc == "web_fetch"
            or "外部" in act or "外部" in exp):
        return "external"
    # runtime：Runtime 恢复层捕获
    if src == "runtime" and "recover" in loc:
        return "runtime"
    if src == "runtime":
        return "runtime"
    # unknown：信息不足 / 模糊 / 乱猜
    if ("乱猜" in act or "模糊" in exp or "模糊" in act
            or "信息不足" in act or "abstain" in act):
        return "unknown"
    # grounding：无候选 / 未命中 / 不存在
    if (src in ("grounder", "grounding") or "候选为空" in act or "无匹配" in act
            or "不存在" in act or "非候选" in act):
        return "grounding"
    # planning：约束 / 语义验证 / 目标漂移
    if (src in ("semantic_validator", "constraint_extractor")
            or "约束" in exp or "漏 [" in act or "context_drift" in act):
        return "planning"
    # prompt：结构不完整（缺 verb/target_type）
    if src == "plan_validator" or "verb" in act or "target_type" in act:
        return "prompt"
    # decision：选错工具 / 决策
    if src in ("executor", "capability") or "选错" in act:
        return "decision"
    # tool：超时（兜底）
    if "timeout" in act:
        return "tool"
    return ""


def diagnose(event: FailureEvent) -> Diagnosis:
    """确定性第一层诊断：evidence 特征投票 + symptom 默认（SYMPTOM_MAP tie-break）。

    Returns:
        Diagnosis(root_cause, confidence, evidence_matches)
    """
    votes: dict = {}
    matches: List[str] = []
    for ev in event.evidence:
        rc = _root_cause_from_evidence(ev)
        if rc:
            votes[rc] = votes.get(rc, 0) + 1
            matches.append(f"{ev.source}:{ev.location} → {rc}")

    # symptom 提供默认候选（tie-break，权重 0.5）
    symptom_default = SYMPTOM_MAP.get(event.symptom, {}).get("root_cause", "")
    if symptom_default:
        votes[symptom_default] = votes.get(symptom_default, 0) + 0.5

    if not votes:
        root_cause = "unknown"
        confidence = 0.2
    else:
        root_cause = max(votes, key=votes.get)
        total = max(len(event.evidence), 1)
        confidence = round(min(votes[root_cause] / total, 1.0), 2)

    return Diagnosis(root_cause=root_cause, confidence=confidence, evidence_matches=matches)


def correction_strategy(diagnosis: Diagnosis) -> Correction:
    """root_cause → correction proposal（确定性映射）。"""
    action = _CORRECTION_MAP.get(diagnosis.root_cause, "ask_user")
    return Correction(
        action=action,
        reason=f"诊断 root_cause={diagnosis.root_cause}（confidence={diagnosis.confidence}）",
        confidence=diagnosis.confidence,
    )


def reflect(event: FailureEvent) -> ReflectionResult:
    """Reflection Contract 唯一入口。

    Args:
        event: FailureEvent（Fail Board 提供，含 evidence + symptom）

    Returns:
        ReflectionResult(diagnosis, correction) —— correction 是 Proposal，Executor 决定执行。
    """
    diagnosis = diagnose(event)
    correction = correction_strategy(diagnosis)
    return ReflectionResult(diagnosis=diagnosis, correction=correction)
