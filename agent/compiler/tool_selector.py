"""ToolSelector (Compiler) — stateless rule engine for Tasks → ExecutionPlans.

Compiler semantics: mapping a Task (verb+target+policy) to an ExecutionPlan
(steps + executor) is *compilation*, not selection. Rules are compiler passes.

Each Verb has a corresponding Rule. Adding a new tool capability means
adding a new Rule subclass. ToolSelector (Compiler) itself has no __init__
state, so tests can inject FakeWorkspace directly.
"""
from abc import ABC, abstractmethod
from typing import Any, Optional

from agent.task import Task, Verb, ExecutionPlan, ExecutionStep


class Rule(ABC):
    """One rule in the ToolSelector (Compiler) engine.

    Each rule handles one Verb (or a small group of related verbs).
    Rules are stateless — all dependencies injected via build() kwargs.
    """

    @property
    @abstractmethod
    def verb(self) -> Verb:
        """The verb this rule handles."""

    @abstractmethod
    def matches(self, task: Task) -> bool:
        """Check if this rule should handle the given task.

        Default implementation: check verb match.
        Override for rules that handle multiple verbs or conditional matching.
        """

    @abstractmethod
    def build(self, task: Task, **services) -> ExecutionPlan:
        """Build execution plan for the task.

        Args:
            task: The task to build a plan for
            services: Keyword args for external access.
                Common keys:
                - workspace (for resolve/lookup)
                - knowledge (for listing capabilities)
                - repository (for semantic search)

        Returns:
            ExecutionPlan with ordered steps
        """


class ToolSelector:
    """Stateless rule engine (Compiler).

    Usage:
        compiler = ToolSelector()
        compiler.add_rule(ReadRule())
        plan = compiler.compile(task, workspace=ws, knowledge=kw)

    To add a new capability:
        1. Create a Rule subclass
        2. Call compiler.add_rule(MyRule())

    Dispatcher is eliminated: compile() decides the executor.
    - Rules matched → ExecutionPlan(executor="tool", steps=[...])
    - LLM-only task (design/analyze/explain + no target) → ExecutionPlan(executor="llm", steps=[])
    """

    def __init__(self):
        self._rules: list[Rule] = []

    def add_rule(self, rule: Rule) -> None:
        """Register a rule. Rules are checked in registration order."""
        self._rules.append(rule)

    # ── Compiler API（主入口）──

    def compile(self, task: Task, **services) -> ExecutionPlan:
        """Compile a Task into an ExecutionPlan (steps + executor).

        Args:
            task: Planner task (verb + target + kind + policy)
            services: Workspace, Knowledge, Repository instances

        Returns:
            ExecutionPlan. executor="tool" when deterministic steps exist;
            executor="llm" when the task is open-ended reasoning.
        """
        # Task policy may force an executor (Stage 投影时已声明)
        if task.policy.executor == "llm":
            return ExecutionPlan(task=task, steps=[], executor="llm")

        for rule in self._rules:
            if rule.matches(task):
                plan = rule.build(task, **services)
                return plan

        # Fallback: basic resolve + read for unknown tasks
        fallback = self._fallback(task, **services)
        if fallback.steps:
            return fallback

        # 无确定性步骤 → 开放式 LLM 推理
        return ExecutionPlan(task=task, steps=[], executor="llm")

    def select(self, task: Task, **services) -> ExecutionPlan:
        """Backward-compat alias for compile()."""
        return self.compile(task, **services)

    def _fallback(self, task: Task, **services) -> ExecutionPlan:
        """Fallback plan: resolve target, then try to read it."""
        steps = [
            ExecutionStep(
                tool="workspace",
                args={"spec": task.target},
                outputs=["path"],
            ),
        ]
        if task.verb in (Verb.READ, Verb.EXPLAIN, Verb.MODIFY):
            steps.append(
                ExecutionStep(
                    tool="filesystem.read",
                    args={"path": "$path"},  # resolved by previous step
                    outputs=["content"],
                )
            )
        if not task.target:
            # 无 target → 开放式任务（design/analyze/chat）
            return ExecutionPlan(task=task, steps=[], executor="llm")
        return ExecutionPlan(task=task, steps=steps)

    # ── Legacy SPI（保留测试兼容）──

    def _legacy_select(self, task: Task, **services) -> ExecutionPlan:
        """Original select() behavior: raises on no match, fallback always tool."""
        for rule in self._rules:
            if rule.matches(task):
                return rule.build(task, **services)
        return self._fallback(task, **services)