"""BudgetManager — 资源预算管控。

每个 Node 绑定 BudgetSpec，Executor 每步检查是否超限。
避免 ReAct 无限循环、减少 Token 浪费。
"""
from dataclasses import dataclass, field
from typing import Optional
import time


@dataclass
class BudgetSpec:
    """预算规格。
    
    Attributes:
        max_steps: 最大步骤数（如 ReAct 循环的最大 Think 次数）
        max_retries: 最大重试次数
        max_tokens: 最大 Token 消耗
        max_cost: 最大费用（USD）
        deadline: 截止时间戳（time.time() + timeout）
        timeout: 超时秒数
    """
    max_steps: int = 8
    max_retries: int = 0
    max_tokens: Optional[int] = None
    max_cost: Optional[float] = None
    deadline: Optional[float] = None
    timeout: Optional[int] = None

    def to_dict(self) -> dict:
        return {
            "max_steps": self.max_steps,
            "max_retries": self.max_retries,
            "max_tokens": self.max_tokens,
            "max_cost": self.max_cost,
            "timeout": self.timeout,
        }


@dataclass
class BudgetState:
    """运行时预算状态。"""
    steps_used: int = 0
    retries_used: int = 0
    tokens_used: int = 0
    cost_used: float = 0.0
    start_time: float = field(default_factory=time.time)

    @property
    def elapsed(self) -> float:
        return time.time() - self.start_time

    def reset(self):
        self.steps_used = 0
        self.retries_used = 0
        self.tokens_used = 0
        self.cost_used = 0.0
        self.start_time = time.time()


class BudgetManager:
    """预算管理器。
    
    用法:
        budget = BudgetSpec(max_steps=5, timeout=30)
        mgr = BudgetManager(budget)
        
        while mgr.within_budget():
            mgr.count_step()
            # ... do work ...
            if not mgr.within_budget():
                break
    """

    def __init__(self, spec: Optional[BudgetSpec] = None):
        self.spec = spec or BudgetSpec()
        self.state = BudgetState()

    def within_budget(self) -> bool:
        """检查是否仍然在预算内。"""
        spec = self.spec

        # 检查步骤数
        if self.state.steps_used >= spec.max_steps:
            return False

        # 检查重试次数
        if self.state.retries_used > spec.max_retries:
            return False

        # 检查 Token
        if spec.max_tokens is not None and self.state.tokens_used >= spec.max_tokens:
            return False

        # 检查费用
        if spec.max_cost is not None and self.state.cost_used >= spec.max_cost:
            return False

        # 检查超时
        if spec.deadline is not None and time.time() >= spec.deadline:
            return False
        if spec.timeout is not None and self.state.elapsed >= spec.timeout:
            return False

        return True

    def count_step(self):
        """计数一个步骤。"""
        self.state.steps_used += 1

    def count_retry(self):
        """计数一次重试。"""
        self.state.retries_used += 1

    def count_tokens(self, n: int):
        """计数 Token 消耗。"""
        self.state.tokens_used += n

    def count_cost(self, amount: float):
        """计数费用消耗。"""
        self.state.cost_used += amount

    def steps_remaining(self) -> int:
        """剩余步骤数。"""
        return max(0, self.spec.max_steps - self.state.steps_used)

    @property
    def exceeded(self) -> Optional[str]:
        """返回超限原因，None 表示未超限。"""
        if self.state.steps_used >= self.spec.max_steps:
            return f"steps_exceeded ({self.state.steps_used}/{self.spec.max_steps})"
        if self.state.retries_used > self.spec.max_retries:
            return f"retries_exceeded ({self.state.retries_used}/{self.spec.max_retries})"
        if self.spec.max_tokens is not None and self.state.tokens_used >= self.spec.max_tokens:
            return f"tokens_exceeded ({self.state.tokens_used}/{self.spec.max_tokens})"
        if self.spec.deadline is not None and time.time() >= self.spec.deadline:
            return "deadline_exceeded"
        if self.spec.timeout is not None and self.state.elapsed >= self.spec.timeout:
            return f"timeout_exceeded ({self.state.elapsed:.1f}/{self.spec.timeout}s)"
        return None

    def to_prompt(self) -> str:
        """生成 LLM 提示中的预算信息。"""
        parts = [f"步骤 {self.state.steps_used + 1}/{self.spec.max_steps}"]
        remaining = self.steps_remaining()
        if remaining > 0:
            parts.append(f"剩余 {remaining} 步")
        if self.spec.timeout:
            remaining_time = max(0, self.spec.timeout - self.state.elapsed)
            parts.append(f"剩余 {remaining_time:.0f}s")
        return " | ".join(parts)