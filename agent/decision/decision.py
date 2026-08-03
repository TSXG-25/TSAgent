"""Decision — 故障处置策略系统（v2.0-D）。

守则（v2.0-D 最终冻结）：
    Decision 是 Policy + Matrix + Confidence Gate 的确定性层，不是 LLM agentic loop。
    先把 retry / switch / ask / finish 四个动作做到可靠，不扩展动作类型。

Input（三件套，不消费 World Knowledge）：
    DecisionInput(diagnosis, diagnosis_confidence, state, event_id)
    只消费：失败诊断 + 策略约束 + 执行状态。不知道用户原话/全部历史/memory/context。

输出：
    Decision(action, confidence, reason) + DecisionTrace（可解释性 / Wrong Recovery Rate 分析）

PolicyRegistry：可被 v2.1 Failure Learning 动态更新（Policy Suggestion → Human Review → update）。

设计纪律（ADR-0009）：全部确定性，无 LLM。
"""
from dataclasses import dataclass, field
from typing import List, Tuple

# ── Action 集合（v2.0-D 固定四个动作） ──
RETRY, SWITCH, ASK, FINISH = "retry", "switch", "ask", "finish"
ACTIONS = {RETRY, SWITCH, ASK, FINISH}

# Confidence Gate：组合置信低于阈值 → 降级 Ask User（不许 Agent 乱改）
CONFIDENCE_GATE = 0.5

# 破坏性/终态动作的置信惩罚
TERMINAL_ACTION_PENALTY = 0.5
RETRY_EXHAUSTED_PENALTY = 0.3


@dataclass(frozen=True)
class ExecutionState:
    """Decision 的执行状态输入。"""
    retry_count: int = 0
    same_tool: bool = False
    user_blocked: bool = False
    evidence_completeness: float = 1.0


@dataclass(frozen=True)
class Policy:
    """单诊断的决策策略（Decision Matrix 的一行）。"""
    allowed: Tuple[str, ...]
    default: str
    max_retry: int = 3


# ── Decision Matrix（确定性策略表；v2.1 可动态更新） ──
POLICY_TABLE = {
    "tool_timeout":       Policy(allowed=(RETRY, SWITCH), default=RETRY, max_retry=3),
    "tool_failure":       Policy(allowed=(RETRY, SWITCH), default=SWITCH, max_retry=2),
    "permission_denied":  Policy(allowed=(ASK,),          default=ASK),
    "grounding_miss":     Policy(allowed=(SWITCH,),       default=SWITCH),
    "hallucination":      Policy(allowed=(SWITCH, ASK),   default=SWITCH),
    "constraint_violation": Policy(allowed=(ASK,),        default=ASK),
    "context_drift":      Policy(allowed=(RETRY,),        default=RETRY),
    "planning_failure":   Policy(allowed=(RETRY,),        default=RETRY),
    "decision_failure":   Policy(allowed=(SWITCH,),       default=SWITCH),
    "prompt_failure":     Policy(allowed=(RETRY,),        default=RETRY),
    "runtime_failure":    Policy(allowed=(RETRY,),        default=RETRY),
    "external_failure":   Policy(allowed=(RETRY, ASK, FINISH), default=RETRY, max_retry=3),
    "unknown":            Policy(allowed=(ASK,),          default=ASK),
}


@dataclass(frozen=True)
class DecisionInput:
    """Decision 输入（三件套，严格边界）。"""
    diagnosis: str
    diagnosis_confidence: float
    state: ExecutionState = field(default_factory=ExecutionState)
    event_id: str = ""


@dataclass(frozen=True)
class Decision:
    action: str
    confidence: float
    reason: str


@dataclass(frozen=True)
class DecisionTrace:
    """决策可解释性记录（Wrong Recovery Rate 分析）。"""
    decision_id: str
    input_event_id: str
    diagnosis: str
    chosen_action: str
    rejected_actions: List[str]
    confidence: float
    policy_rule: str


def _decision_confidence(diag_conf: float, action: str, state: ExecutionState,
                         policy: Policy) -> float:
    """组合置信：诊断置信 + 重试耗尽 + 动作风险 + 证据完整度。"""
    c = diag_conf
    if state.retry_count >= policy.max_retry:
        c -= RETRY_EXHAUSTED_PENALTY
    if action == FINISH:
        c -= TERMINAL_ACTION_PENALTY
    c *= state.evidence_completeness
    return round(max(0.0, min(1.0, c)), 2)


def _rule_name(diagnosis: str, action: str, state: ExecutionState, policy: Policy,
               diag_conf: float) -> str:
    if action == ASK and diag_conf < CONFIDENCE_GATE:
        return "confidence_gate"
    if state.retry_count >= policy.max_retry and action != RETRY:
        return f"{diagnosis}_exhausted"
    if action == ASK:
        return f"{diagnosis}_ask"
    return f"{diagnosis}_default"


def decide(inp: DecisionInput) -> Tuple[Decision, DecisionTrace]:
    """确定性决策：Policy Matrix → 组合置信 → Confidence Gate。

    Returns:
        (Decision, DecisionTrace)
    """
    policy = POLICY_TABLE.get(inp.diagnosis, POLICY_TABLE["unknown"])
    st = inp.state

    # 重试耗尽 → 从 allowed 中剔除 retry
    if st.retry_count >= policy.max_retry:
        allowed = [a for a in policy.allowed if a != RETRY] or list(policy.allowed)
    else:
        allowed = list(policy.allowed)

    action = policy.default if policy.default in allowed else allowed[0]
    conf = _decision_confidence(inp.diagnosis_confidence, action, st, policy)

    # Confidence Gate：组合置信 < 阈值且动作非 ask/finish → 降级 Ask User
    if conf < CONFIDENCE_GATE and action not in (ASK, FINISH):
        action = ASK
        conf = _decision_confidence(inp.diagnosis_confidence, ASK, st, policy)

    rule = _rule_name(inp.diagnosis, action, st, policy, inp.diagnosis_confidence)
    rejected = [a for a in policy.allowed if a != action]

    decision = Decision(action=action, confidence=conf,
                        reason=f"policy={rule}; diagnosis={inp.diagnosis}(conf={inp.diagnosis_confidence})")
    trace = DecisionTrace(
        decision_id=f"dec:{inp.event_id or '?'}:{inp.diagnosis}:{rule}",
        input_event_id=inp.event_id,
        diagnosis=inp.diagnosis,
        chosen_action=action,
        rejected_actions=rejected,
        confidence=conf,
        policy_rule=rule,
    )
    return decision, trace
