"""One bounded budget for a logical Runtime run."""

from __future__ import annotations

from dataclasses import dataclass
import os
import time


@dataclass
class RunBudget:
    """Mutable per-run counters with one owner for runtime limits."""

    max_seconds: float = 120.0
    max_transitions: int = 24
    max_goal_rounds: int = 10
    max_recoveries: int = 2
    transitions: int = 0
    recoveries: int = 0
    started_at: float | None = None

    @classmethod
    def from_env(cls) -> "RunBudget":
        return cls(
            max_seconds=float(os.getenv("TSAGENT_MAX_RUNTIME_SECONDS", "120")),
            max_transitions=int(os.getenv("TSAGENT_MAX_STATE_TRANSITIONS", "24")),
            max_goal_rounds=int(os.getenv("TSAGENT_MAX_GOAL_ROUNDS", "10")),
            max_recoveries=int(os.getenv("TSAGENT_MAX_RECOVERIES", "2")),
        )

    def start(self, now: float | None = None) -> None:
        self.started_at = time.perf_counter() if now is None else now

    def consume_transition(self, now: float | None = None) -> bool:
        if self.started_at is None:
            self.start(now)
        self.transitions += 1
        current = time.perf_counter() if now is None else now
        return (
            self.transitions <= self.max_transitions
            and current - float(self.started_at) <= self.max_seconds
        )

    def consume_recovery(self) -> bool:
        self.recoveries += 1
        return self.recoveries <= self.max_recoveries

    def exhausted_code(self, now: float | None = None) -> str:
        current = time.perf_counter() if now is None else now
        if self.transitions > self.max_transitions:
            return "RUNTIME_TRANSITION_BUDGET_EXHAUSTED"
        if self.started_at is not None and current - self.started_at > self.max_seconds:
            return "RUNTIME_TIME_BUDGET_EXHAUSTED"
        if self.recoveries > self.max_recoveries:
            return "RUNTIME_RECOVERY_BUDGET_EXHAUSTED"
        return ""


__all__ = ["RunBudget"]
