"""Compiler — stateless compiler: Task → ExecutionPlan.

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
Compiler is the lowering layer: each Rule is a lowering rule.
"""
import re
from abc import ABC, abstractmethod
from typing import Any, Optional

from agent.task import Task, Verb, ExecutionPlan, ExecutionStep
from agent.compiler.context import CompilerContext
from agent.registry.tool_registry import registry as _application_registry
from agent.tool_identity import CANONICAL_TOOL_ALIASES, registry_tool_name


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
    ) -> ExecutionPlan:
        """Compile a Task into an ExecutionPlan (four stages).

        Args:
            task: Planner task (verb + target + target_type + policy)
            context: CompilerContext (workspace/registry/repository) — sole env input.

        Returns:
            ExecutionPlan (executor="tool" | "llm")

        Raises:
            CompileError: task violates contract or plan fails static check.
        """
        if context is None:
            context = CompilerContext(registry=_application_registry)
        elif context.registry is None:
            # There is one application registry.  A missing registry is not a
            # reason to skip the static check; compilation must still fail
            # fast for an unknown tool.
            context = CompilerContext(
                workspace=context.workspace,
                registry=_application_registry,
                repository=context.repository,
                extra=dict(context.extra),
            )

        # Task policy may force an executor (Stage 投影时已声明)
        # Planner policy cannot downgrade an explicit execution verb to an
        # LLM-only plan.  The requested outcome contract owns this boundary.
        if task.policy.executor == "llm" and task.verb != Verb.EXECUTE:
            plan = ExecutionPlan(task=task, steps=[], executor="llm")
            self._static_check(plan, context)
            return plan

        task = self._normalize(task)
        self._semantic_check(task)
        plan = self._lower(task, context)
        self._static_check(plan, context)
        return plan

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
        # Workflow stages arrive here with resolved Task.inputs and an
        # explicit tool policy.  Lower those bindings into the same
        # ExecutionPlan consumed by PlanExecutor; do not create a second
        # workflow execution path.
        bound_plan = self._lower_bound_tool(task)
        if bound_plan is not None:
            return bound_plan

        services = {}
        if context.workspace is not None:
            services["workspace"] = context.workspace
        if context.repository is not None:
            services["repository"] = context.repository

        if task.verb == Verb.EXECUTE:
            for rule in self._rules:
                if rule.matches(task):
                    return rule.build(task, **services)

        # Only tasks without a deterministic lowering rule become open-ended
        # reasoning.  In particular, verb=EXECUTE must reach ExecuteRule even
        # when its natural-language target has target_type=text/none.
        if task.target_type in ("text", "none"):
            return ExecutionPlan(task=task, steps=[], executor="llm")

        for rule in self._rules:
            if rule.matches(task):
                return rule.build(task, **services)

        raise CompileError(
            f"task={task.id}: 没有匹配的 lowering rule（verb={task.verb.value}, "
            f"target_type={task.target_type}）"
        )

    @staticmethod
    def _lower_bound_tool(task: Task) -> Optional[ExecutionPlan]:
        policy = task.policy.tool_policy or {}
        allowed = list(policy.get("allow", []) or [])
        inputs = task.inputs or {}
        if task.policy.executor != "tool" or not allowed or not inputs:
            return None

        def value(name: str):
            return inputs.get(name)

        if "read_file" in allowed and value("path") is not None:
            return ExecutionPlan(
                task=task,
                steps=[ExecutionStep(
                    tool="filesystem.read",
                    args={"path": str(value("path"))},
                    outputs=["content"],
                )],
            )
        if "run_python" in allowed and value("code") is not None:
            return ExecutionPlan(
                task=task,
                steps=[ExecutionStep(
                    tool="run_python",
                    args={"code": str(value("code"))},
                    outputs=["result"],
                )],
            )
        if "web_search" in allowed and (
            value("query") is not None or value("path") is not None
        ):
            query = value("query") if value("query") is not None else value("path")
            args = {"query": str(query)}
            if value("timeliness") is not None:
                args["timeliness"] = str(value("timeliness"))
            return ExecutionPlan(
                task=task,
                steps=[ExecutionStep(
                    tool="web_search",
                    args=args,
                    outputs=["results"],
                )],
            )
        if "write_file" in allowed and value("path") is not None and value("content") is not None:
            return ExecutionPlan(
                task=task,
                steps=[
                    ExecutionStep(
                        tool="workspace",
                        args={"spec": str(value("path"))},
                        outputs=["path"],
                    ),
                    ExecutionStep(
                        tool="filesystem.write",
                        args={
                            "path": "$path",
                            "content": str(value("content")),
                            "mode": str(value("mode") or "overwrite"),
                        },
                        outputs=["result"],
                    ),
                ],
            )
        if "copy_file" in allowed and value("source") is not None and value("destination") is not None:
            return ExecutionPlan(
                task=task,
                steps=[
                    ExecutionStep(
                        tool="filesystem.copy",
                        args={
                            "source": str(value("source")),
                            "destination": str(value("destination")),
                            "exact": True,
                        },
                        outputs=["result"],
                    ),
                ],
            )
        if "move_file" in allowed and value("source") is not None and value("destination") is not None:
            return ExecutionPlan(
                task=task,
                steps=[
                    ExecutionStep(
                        tool="filesystem.move",
                        args={
                            "source": str(value("source")),
                            "destination": str(value("destination")),
                            "exact": True,
                        },
                        outputs=["result"],
                    ),
                ],
            )
        if "delete_file" in allowed and value("path") is not None:
            return ExecutionPlan(
                task=task,
                steps=[
                    ExecutionStep(
                        tool="filesystem.delete",
                        args={"path": str(value("path")), "exact": True},
                        outputs=["result"],
                    ),
                ],
            )
        return None

    # ── Stage 4: Static Check（ExecutionPlan 契约，编译期报错）──

    # 内置特殊工具（plan_executor 处理，不在 ToolRegistry）
    _BUILTIN_TOOLS = {
        "workspace",
        "repository",
        "knowledge",
        "llm",
        # Standard execution tools are loaded by the application bootstrap,
        # but Workflow/Coordinator callers are allowed to compile plans
        # before that optional registry import has run.  Runtime execution
        # still resolves the concrete implementation from ToolRegistry.
        "shell",
        "run_python",
        "run_python_file",
        # Deterministic transformations performed inside PlanExecutor. They
        # are execution primitives, not ToolRegistry providers.
        "text.merge_unique",
        "text.materialize_research",
        # Scoped filesystem primitives are implemented by PlanExecutor and
        # therefore do not depend on the process-global ToolRegistry.
        *CANONICAL_TOOL_ALIASES,
        # Raw names remain accepted only for hand-built contract tests and
        # the registry adapter; production lowering emits filesystem.*.
        *CANONICAL_TOOL_ALIASES.values(),
    }

    def _static_check(self, plan: ExecutionPlan, context: Optional[CompilerContext] = None) -> None:
        if plan.executor == "llm":
            return  # LLM plan: 无步骤，SSA 检查不适用

        if not plan.steps:
            raise CompileError(f"task={plan.task.id}: executor=tool 但 steps 为空")

        registry = (
            context.registry
            if context is not None and context.registry is not None
            else _application_registry
        )
        defined: set[str] = set()
        for step in plan.steps:
            # tool 必须存在（内置特殊工具或 ToolRegistry）
            if not self._tool_exists(step.tool, registry):
                raise CompileError(
                    f"task={plan.task.id} step={step.tool}: 工具不存在于 ToolRegistry（编译期错误）"
                )

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

    @classmethod
    def _tool_exists(cls, tool: str, registry) -> bool:
        """工具存在性：内置特殊工具 / filesystem.* 映射 / ToolRegistry 注册。"""
        if tool in cls._BUILTIN_TOOLS:
            return True
        actual = registry_tool_name(tool)
        try:
            return registry.get(actual) is not None
        except Exception as exc:
            raise CompileError(
                f"ToolRegistry unavailable while checking {tool!r}: {exc}"
            ) from exc
