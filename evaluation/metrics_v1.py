"""metrics_v1 — Evaluation Metrics 统一模型（版本化，ADR-0005）。

所有 Benchmark 输出同一套 Metrics。以后新增指标 → metrics_v2.py，不改 v1。
"""
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class MetricsV1:
    """v1 指标集合。"""
    # 正确性
    planning_success: float = 0.0      # Planner 产出合法 plan 的比例
    grounding_recall: float = 0.0      # 真实目标文件在候选中的比例
    grounding_top1: float = 0.0        # 真实目标文件在 Top-1 的比例
    compile_reject_rate: float = 0.0   # 编译期拒绝率
    execution_success: float = 0.0     # 执行层成功率
    verification_success: float = 0.0  # 外部 verify 通过率
    # 资源
    latency_ms: float = 0.0            # 平均任务耗时
    cost_usd: float = 0.0              # 平均成本
    # 恢复能力
    recovery_rate: float = 0.0         # Workflow/执行异常被 Runtime 捕获并继续 Session 的比例

    def to_dict(self) -> dict:
        return {
            "planning_success": round(self.planning_success, 3),
            "grounding_recall": round(self.grounding_recall, 3),
            "grounding_top1": round(self.grounding_top1, 3),
            "compile_reject_rate": round(self.compile_reject_rate, 3),
            "execution_success": round(self.execution_success, 3),
            "verification_success": round(self.verification_success, 3),
            "latency_ms": round(self.latency_ms, 1),
            "cost_usd": round(self.cost_usd, 4),
            "recovery_rate": round(self.recovery_rate, 3),
        }

    @staticmethod
    def from_dict(d: dict) -> "MetricsV1":
        return MetricsV1(**{k: d.get(k, v) for k, v in MetricsV1().to_dict().items()})


# Quality Budget（ADR-0005）：回归比较时的允许差
QUALITY_BUDGET = {
    "planning_success": {"op": "ge", "delta": 0.0},       # 不得下降
    "grounding_recall": {"op": "ge", "delta": 0.0},       # 不得下降
    "compile_reject_rate": {"op": "le", "delta": 0.0},    # 不得上升
    "latency_ms": {"op": "le", "delta": 0.10},            # ≤ +10%
    "cost_usd": {"op": "le", "delta": 0.05},              # ≤ +5%
}


def gate_status(cur: MetricsV1, base: Optional[MetricsV1]) -> str:
    """三级 Quality Gate（PASS / WARNING / FAIL）。"""
    if base is None:
        return "PASS"  # 无基线 → 首跑
    violations = []
    for key, rule in QUALITY_BUDGET.items():
        c = getattr(cur, key)
        b = getattr(base, key)
        if rule["op"] == "ge" and c < b - 1e-6:
            violations.append((key, c, b, "下降"))
        elif rule["op"] == "le" and c > b * (1 + rule["delta"]) + 1e-6 and c > b:
            violations.append((key, c, b, "超限"))
    if not violations:
        return "PASS"
    # WARNING: 允许 1 个非核心指标轻微超限（latency/cost）
    non_core = [v for v in violations if v[0] not in ("planning_success", "grounding_recall", "compile_reject_rate")]
    core = [v for v in violations if v[0] not in ("latency_ms", "cost_usd")]
    if core:
        return "FAIL"
    if len(non_core) <= 2:
        return "WARNING"
    return "FAIL"
