"""metrics_v2 — Intelligence Metrics 统一模型（版本化，ADR-0009/0010/0011）。

v2.x 智能层指标集合。设计纪律：
    - 版本化：以后新增指标 → metrics_v3.py，不改 v2（沿用 metrics_v1 的 ADR-0005 模式）。
    - 确定性：全部指标由确定性判定器（validator / verifier / 统计）计算，禁止 LLM 自评（ADR-0009）。
    - 行为验收：指标全部面向外部可验证行为，而非推理过程（ADR-0010）。

Capability 分层：
    Planning   → goal_coverage / constraint_detection / task_completeness
                 / dependency_correctness / execution_order
    Decision   → tool_accuracy / retry_accuracy / finish_accuracy / clarification_accuracy
    Reflection → diagnosis_accuracy / false_diagnosis_rate / correction_success
                 / recovery_improvement
    Uncertainty（横切）→ correct_abstention / clarification_accuracy / false_confidence
    Long Horizon（Benchmark）→ completion_rate / recovery_count / context_drift / latency_ms

v1.x 指标（metrics_v1）不迁移、不修改 —— 两个模型并存，互不干扰。
"""
from dataclasses import dataclass, field
from typing import Dict
from .metrics import MetricCollector, MetricDefinition, MetricReport, TrendGate


_LOWER_IS_BETTER_V2 = {
    "false_diagnosis_rate", "false_confidence", "recovery_count",
    "context_drift", "latency_ms",
}
METRIC_DEFINITIONS_V2 = tuple(
    MetricDefinition(
        name=name,
        direction="le" if name in _LOWER_IS_BETTER_V2 else "ge",
        capability="intelligence",
    )
    for name in (
        "goal_coverage", "constraint_detection", "task_completeness",
        "dependency_correctness", "execution_order", "tool_accuracy",
        "retry_accuracy", "finish_accuracy", "clarification_accuracy",
        "diagnosis_accuracy", "false_diagnosis_rate", "correction_success",
        "recovery_improvement", "correct_abstention", "false_confidence",
        "completion_rate", "recovery_count", "context_drift", "latency_ms",
    )
)


@dataclass
class MetricsV2:
    """v2 智能层指标集合。未填充的维度为 0.0（表示该 Capability 尚未评估）。"""

    # ── Planning（v2.0-A） ──
    goal_coverage: float = 0.0          # golden 目标覆盖比例
    constraint_detection: float = 0.0   # 约束遵守比例（含无约束场景=1.0）
    task_completeness: float = 0.0      # 结构合法 task 占比
    dependency_correctness: float = 0.0 # 依赖引用存在 + DAG 无环的比例
    execution_order: float = 0.0        # 数组顺序满足依赖的比例

    # ── Decision（v2.0-D 落地后启用） ──
    tool_accuracy: float = 0.0          # 工具选择正确率
    retry_accuracy: float = 0.0         # Retry 决策正确率
    finish_accuracy: float = 0.0        # Finish 决策正确率
    clarification_accuracy: float = 0.0 # 澄清决策正确率

    # ── Reflection（v2.0-C 落地后启用） ──
    diagnosis_accuracy: float = 0.0     # 错误定位准确率
    false_diagnosis_rate: float = 0.0   # 误诊率（误诊比不知更糟 —— 会污染系统）
    correction_success: float = 0.0     # 修正成功率
    recovery_improvement: float = 0.0   # 恢复提升（相对基线）

    # ── Uncertainty（横切原则） ──
    correct_abstention: float = 0.0     # 正确拒绝/反问的比例
    false_confidence: float = 0.0       # 虚假置信率（信息不足仍乱猜）

    # ── Long Horizon（Benchmark，非 Capability） ──
    completion_rate: float = 0.0        # 长任务完成率
    recovery_count: float = 0.0         # 平均恢复次数
    context_drift: float = 0.0          # 上下文漂移（目标偏移比例）
    latency_ms: float = 0.0             # 平均耗时

    def to_dict(self) -> dict:
        return {k: round(v, 3) for k, v in self.__dict__.items()}

    def to_report(self) -> MetricReport:
        """统一 MetricDefinition → Collector → Report 入口。"""
        return MetricCollector(METRIC_DEFINITIONS_V2).collect(self.to_dict())

    @staticmethod
    def from_dict(d: dict) -> "MetricsV2":
        base = MetricsV2()
        return MetricsV2(**{k: d.get(k, v) for k, v in base.__dict__.items()})

    def populated(self) -> Dict[str, float]:
        """返回已被填充（>0 或已显式赋值）的指标子集，便于只展示已评估维度。"""
        return {k: v for k, v in self.__dict__.items() if v > 0.0}


# Intelligence 回归预算（ADR-0011）：趋势门（Trend Gate）配置。
# 与 v1.x QUALITY_BUDGET 不同：v2 智能指标不做绝对阈值，只做**不能下降**。
# 具体基线（Capability Progress Curve）由 eval_planning 输出 + evaluation 目录维护。
INTELLIGENCE_BUDGET = {
    # op: ge（不得下降） / le（不得上升）
    "goal_coverage": {"op": "ge", "delta": 0.0},
    "constraint_detection": {"op": "ge", "delta": 0.0},
    "task_completeness": {"op": "ge", "delta": 0.0},
    "dependency_correctness": {"op": "ge", "delta": 0.0},
    "execution_order": {"op": "ge", "delta": 0.0},
    "correct_abstention": {"op": "ge", "delta": 0.0},
    "false_confidence": {"op": "le", "delta": 0.0},
    "false_diagnosis_rate": {"op": "le", "delta": 0.0},
    "completion_rate": {"op": "ge", "delta": 0.0},
}


def trend_gate(current: MetricsV2, previous: MetricsV2) -> tuple:
    """Trend Gate：新 Capability 不能使整体能力下降（Capability Progress Curve）。

    Returns:
        (passes: bool, failures: list[str])：failures 列出下降的指标。
    """
    # 保持原有 INTELLIGENCE_BUDGET 的范围，只把比较委托给统一 TrendGate。
    definitions = tuple(
        definition for definition in METRIC_DEFINITIONS_V2
        if definition.name in INTELLIGENCE_BUDGET
    )
    result = TrendGate.evaluate(
        MetricCollector(definitions).collect(current.to_dict()),
        MetricCollector(definitions).collect(previous.to_dict()),
    )
    return (result.passes, list(result.failures))
