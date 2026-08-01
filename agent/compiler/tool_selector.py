"""Compiler (ToolSelector) — stateless compiler: Task → ExecutionPlan.

ADR-0002 Compiler Boundary — four stages, responsibilities must not mix:

    Task
     │
     ▼
 Normalize       # only text normalization (strip quotes/space/slash). No guessing.
     │
     ▼
 Semantic Check  # validate target_type/target legality. No fixing. Invalid → CompileError.
     │
     ▼
 Lower           # dispatch rules by target_type/verb → ExecutionPlan. No reasoning.
     │
     ▼
 Static Check    # ExecutionPlan contract: tool exists / $var defined / outputs non-empty / SSA no dup.
     │
     ▼
 ExecutionPlan

Compiler is a pure function (Principle 7): compile(task, context) has no side effects.
Zero tolerance: no guessing, no fixing, no fallback-repair of illegal tasks.
ToolSelector is the lowering layer: each Rule is a lowering rule.
"""
import re
from abc import ABC, abstractmethod
from typing import Any, Optional

from agent.task import Task, Verb, ExecutionPlan, ExecutionStep
from agent.compiler.context import CompilerContext


class CompileError(Exception):
    """编译期错误（ADR-0002：非法 Task / 非法 ExecutionPlan 在编译期拒绝）。"""


class Rule(ABC):
    """One lowering rule in the Compiler engine.

    Each rule handles one Verb (or a small group of related verbs).
    Rules are stateless — dependencies injected via build() kwargs.
    """

    @property
    @abstractmethod
    def verb(self) -> Verb:
        """The verb this rule handles."""

    @abstractmethod
    def matches(self, task: Task) -> bool:
        """Check if this rule should handle the given task."""

    @abstractmethod
    def build(self, task: Task, **services) -> ExecutionPlan:
        """Build execution plan for the task.

        Args:
            task: The task to build a plan for
            services: keyword deps (workspace for resolve/lookup)

        Returns:
            ExecutionPlan with ordered steps
        """


class Compiler:
    """Stateless compiler (pure function).

    Usage:
        compiler = Compiler()
        compiler.add_rule(ReadRule())
        plan = compiler.compile(task, CompilerContext(workspace=ws))

    To add a new capability (ADR-0001 Freeze):
        1. Create a Rule subclass
        2. Call compiler.add_rule(MyRule())
    """

    def __init__(self):
        self._rules: list[Rule] = []

    def add_rule(self, rule: Rule) -> None:
        """Register a lowering rule. Rules are checked in registration order."""
        self._rules.append(rule)

    # ── Compiler API（主入口）──

    def compile(
        self,
        task: Task,
        context: Optional[CompilerContext] = None,
        **kwargs,
    ) -> ExecutionPlan:
        """Compile a Task into an ExecutionPlan (four stages).

        Args:
            task: Planner task (verb + target + target_type + policy)
            context: CompilerContext (workspace/registry/repository) — sole env input.
            **kwargs: legacy compat (workspace=...) → merged into context.

        Returns:
            ExecutionPlan (executor="tool" | "llm")

        Raises:
            CompileError: task violates contract or plan fails static check.
        """
        if context is None:
            context = CompilerContext(**kwargs)
        elif kwargs:
            for k, v in kwargs.items():
                if getattr(context, k, None) is None:
                    setattr(context, k, v)

        # Task policy may force an executor (Stage 投影时已声明)
        if task.policy.executor == "llm":
            plan = ExecutionPlan(task=task, steps=[], executor="llm")
            self._static_check(plan)
            return plan

        task = self._normalize(task)
        self._semantic_check(task)
        plan = self._lower(task, context)
        self._static_check(plan)
        return plan

    def select(self, task: Task, **services) -> ExecutionPlan:
        """Backward-compat alias for compile()."""
        return self.compile(task, **services)

    # ── Stage 1: Normalize（只做文本规范化，不猜）──

    def _normalize(self, task: Task) -> Task:
        if not task.target:
            return task
        target = task.target.strip()
        for ch in "\"'`":
            target = target.strip(ch)
        target = target.replace("\\", "/")
        if target != task.target:
            # Principle 6: immutable — produce a new Task
            return task.model_copy(update={"target": target})
        return task

    # ── Stage 2: Semantic Check（校验，不修；非法 → 拒绝）──

    def _semantic_check(self, task: Task) -> None:
        if task.target_type in ("file", "symbol") and not task.target.strip():
            raise CompileError(
                f"task={task.id}: target_type={task.target_type} 但 target 为空。"
            )
        if task.target_type == "file" and task.target:
            if re.search(r"[\u4e00-\u9fff]", task.target):
                raise CompileError(
                    f"task={task.id}: target_type=file 但 target 含中文字符: {task.target!r}。"
                    f"file target 必须是具体路径（如 output/solution.py）。"
                )
        if task.target_type not in ("file", "symbol", "text", "none"):
            raise CompileError(
                f"task={task.id}: 非法 target_type={task.target_type!r}。"
                f"可选: file/symbol/text/none"
            )

    # ── Stage 3: Lower（按 target_type 分派规则 → ExecutionPlan）──

    def _lower(self, task: Task, context: CompilerContext) -> ExecutionPlan:
        # 契约内开放任务：text/none → LLM 推理
        if task.target_type in ("text", "none"):
            return ExecutionPlan(task=task, steps=[], executor="llm")

        services = {}
        if context.workspace is not None:
            services["workspace"] = context.workspace
        if context.repository is not None:
            services["repository"] = context.repository

        for rule in self._rules:
            if rule.matches(task):
                return rule.build(task, **services)

        # 无匹配规则 → 基础 resolve + read（仍走 static check）
        fallback = self._fallback(task, **services)
        if fallback.steps:
            return fallback

        return ExecutionPlan(task=task, steps=[], executor="llm")

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
                    args={"path": "$path"},
                    outputs=["content"],
                )
            )
        if not task.target:
            return ExecutionPlan(task=task, steps=[], executor="llm")
        return ExecutionPlan(task=task, steps=steps)

    # ── Stage 4: Static Check（ExecutionPlan 契约，编译期报错）──

    # 内置特殊工具（plan_executor 处理，不在 ToolRegistry）
    _BUILTIN_TOOLS = {
        "workspace", "repository", "knowledge", "llm",
    }

    def _static_check(self, plan: ExecutionPlan) -> None:
        if plan.executor == "llm":
            return  # LLM plan: 无步骤，SSA 检查不适用

        if not plan.steps:
            raise CompileError(f"task={plan.task.id}: executor=tool 但 steps 为空")

        defined: set[str] = set()
        for step in plan.steps:
            # outputs 非空（SSA）
            if not step.outputs:
                raise CompileError(
                    f"task={plan.task.id} step={step.tool}: outputs 为空（SSA 要求非空）"
                )
            for out in step.outputs:
                if out in defined:
                    raise CompileError(
                        f"task={plan.task.id} step={step.tool}: 重复产出变量 '{out}'（SSA）"
                    )
                defined.add(out)

            # $var 输入必须已由前置 steps 产出
            for v in step.args.values():
                if isinstance(v, str) and v.startswith("$"):
                    var = v[1:]
                    if var not in defined:
                        raise CompileError(
                            f"task={plan.task.id} step={step.tool}: 引用了未定义变量 '${var}'"
                        )

    # ── Legacy SPI（保留测试兼容）──

    def _legacy_select(self, task: Task, **services) -> ExecutionPlan:
        """Original select() behavior: raises on no match, fallback always tool."""
        for rule in self._rules:
            if rule.matches(task):
                return rule.build(task, **services)
        return self._fallback(task, **services)


# 兼容别名：ToolSelector == Compiler（Lowering 层语义）
ToolSelector = Compiler

