"""Deterministic authorization for requested local effects.

The workspace boundary answers *where* a task may operate.  This module
answers *whether the user requested that kind of operation at all*.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
import re
from typing import Any, Mapping, Sequence

from .execution_need import (
    EffectScope,
    RequestedOutcome,
)
from .resource_binding import normalize_resource_path


_MUTATION_VERBS = frozenset({"write", "modify", "delete", "move", "copy"})
_EXECUTION_VERBS = frozenset({"execute"})

# Deterministic repair for a demonstrated intent-classifier gap: a real run
# classified "rename X and confirm with python -m pytest -q" as FILE_MUTATION
# only, so the completion gate had no execution outcome to enforce and an
# unverified claim passed.  The classifier stays the primary declarer; this
# repair only fires on unambiguous run/verify instructions in the request
# text, and only on runs that already request real work (never pure chat).
_RUN_WORDS = r"运行|执行|跑一下|跑一遍|跑|验证|确认|确保|检查|run|execute|verify|check"
_TARGET_WORDS = (
    r"测试|命令|脚本|程序|构建|编译|lint|"
    r"tests?|build|commands?|scripts?|pytest|unittest|vitest|jest|mocha|rspec|phpunit"
)
_VERIFICATION_DEMAND = re.compile(
    # Unambiguous tool invocations: `python -m pytest -q`, `npm test`,
    # `cargo test`, `pytest -q tests/`, `./build.sh`, `python foo.py` …
    r"python3?\s+-m\s+[\w.]+"
    r"|python3?\s+[\w./-]+\.py"
    r"|(?:pytest|py\.test|unittest|vitest|jest|mocha|rspec|phpunit|ctest|tox|nose2?)"
    r"(?:\s+[-a-zA-Z0-9_./:=\[\]]+)"
    r"|(?:npm|pnpm|yarn)\s+(?:test|build|run\s+[\w:-]+|ci)\b"
    r"|cargo\s+(?:test|build|check|run)\b"
    r"|go\s+(?:test|build|run|vet)\b"
    r"|make\s+[\w.-]+\b"
    r"|mvn\s+[\w:-]+\b|gradlew?\s+[\w:-]+\b"
    r"|dotnet\s+(?:test|build)\b|flutter\s+test\b"
    r"|(?:bash|sh)\s+[\w./-]+|\./[\w./-]+\.sh"
    # Run/verify verb aimed at a test/build target: 跑一下测试, run the tests
    rf"|(?:{_RUN_WORDS})[^\n。；;]{{0,16}}(?:{_TARGET_WORDS})"
    # Test/build must pass: 测试通过, tests pass, build passes
    rf"|(?:{_TARGET_WORDS})[^\n。；;]{{0,8}}(?:通过|成功|pass(?:es|ed)?)"
    ,
    re.IGNORECASE,
)
_NEGATION = re.compile(
    r"不(?:要|用|需|能|可|必)|别|禁|勿|never|don'?t|do\s+not",
    re.IGNORECASE,
)
_CLAUSE_BREAKS = "，。；;,.、：:\n"

# Cross-file mutation: "更新全部引用/所有调用点/across the codebase" grants
# writes beyond the file the classifier happened to name.  Real-eval R3:
# write_targets=["util.py"] made a rename's call-site edits scope violations.
_WORKSPACE_WIDE_MUTATION = re.compile(
    r"全部引用|所有引用|全部调用|所有调用|全部使用|所有使用"
    r"|所有相关文件|全部相关文件|整个项目|整个仓库|全仓"
    r"|all\s+references?|every\s+reference|all\s+usages?|every\s+usage"
    r"|all\s+callers?|all\s+occurrences|all\s+imports?"
    r"|across\s+the\s+(?:code\s?base|project|repo(?:sitory)?)"
    r"|project[-\s]?wide|codebase[-\s]?wide|everywhere",
    re.IGNORECASE,
)


def _request_demands_verification(user_input: str) -> bool:
    """A demand match is real only when its own clause isn't negated —
    "也不要运行程序" must not produce an execution requirement."""
    for match in _VERIFICATION_DEMAND.finditer(user_input):
        prefix = user_input[max(0, match.start() - 32) : match.start()]
        clause = prefix[max(
            prefix.rfind(breaker) for breaker in _CLAUSE_BREAKS
        ) + 1 :]
        if not _NEGATION.search(clause):
            return True
    return False
_EXECUTION_TOOLS = frozenset({
    "shell",
    "run_python",
    "run_python_file",
    "process.execute",
})
_MUTATION_TOOLS = frozenset({
    "filesystem.write",
    "filesystem.edit",
    "filesystem.copy",
    "filesystem.move",
    "filesystem.delete",
})

_PLAN_REQUIREMENTS = {
    RequestedOutcome.FILE_READ: (
        frozenset({"filesystem.read", "filesystem.list"}),
        frozenset({"read", "list", "explain"}),
    ),
    RequestedOutcome.FILE_MUTATION: (
        frozenset({
            "filesystem.write", "filesystem.copy", "filesystem.move",
            "filesystem.delete",
        }),
        frozenset({"write", "modify", "copy", "move", "delete"}),
    ),
    RequestedOutcome.CODE_EXECUTION: (
        frozenset({"run_python", "run_python_file"}),
        frozenset({"execute"}),
    ),
    RequestedOutcome.COMMAND_EXECUTION: (
        frozenset({"shell", "process.execute"}),
        frozenset({"execute"}),
    ),
}


def _normalize_scope(value: str) -> str:
    directory_scope = str(value or "").strip().replace("\\", "/").endswith("/")
    normalized = normalize_resource_path(value)
    if normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized.startswith("/"):
        return normalized + "/" if directory_scope and normalized != "/" else normalized
    normalized = PurePosixPath(normalized).as_posix()
    return normalized + "/" if directory_scope and normalized != "." else normalized


@dataclass(frozen=True)
class EffectAuthorization:
    """Immutable authorization derived from the Intent's declared facts."""

    requested_outcomes: tuple[RequestedOutcome, ...]
    write_scopes: tuple[str, ...]
    prohibited_outcomes: tuple[RequestedOutcome, ...] = ()

    @classmethod
    def from_state(cls, state: Mapping[str, Any], user_input: str) -> "EffectAuthorization":
        """Outcomes and write scopes come from the Intent, never from regex."""
        outcomes = tuple(dict.fromkeys(
            RequestedOutcome(item)
            for item in state.get("requested_outcomes", ())
        ))
        goal_state = state.get("goal_state") or {}
        prohibited = {
            RequestedOutcome(item)
            for item in goal_state.get("prohibited_effects", ())
        }
        outcomes = tuple(item for item in outcomes if item not in prohibited)
        if (
            state.get("artifact_required")
            and RequestedOutcome.FILE_MUTATION not in prohibited
            and RequestedOutcome.FILE_MUTATION not in outcomes
        ):
            outcomes = (*outcomes, RequestedOutcome.FILE_MUTATION)
        if (
            any(outcome is not RequestedOutcome.USER_VISIBLE_OUTPUT for outcome in outcomes)
            and RequestedOutcome.COMMAND_EXECUTION not in prohibited
            and RequestedOutcome.COMMAND_EXECUTION not in outcomes
            and RequestedOutcome.CODE_EXECUTION not in outcomes
            and _request_demands_verification(user_input or "")
        ):
            outcomes = (*outcomes, RequestedOutcome.COMMAND_EXECUTION)
        write_targets = tuple(
            _normalize_scope(target)
            for target in state.get("intent_write_targets", ())
            if str(target or "").strip()
        )
        if (
            RequestedOutcome.FILE_MUTATION in outcomes
            and _WORKSPACE_WIDE_MUTATION.search(user_input or "")
        ):
            write_targets = (".",)
        if not write_targets and RequestedOutcome.FILE_MUTATION in outcomes:
            write_targets = (".",)
        return cls(
            requested_outcomes=outcomes,
            write_scopes=write_targets,
            prohibited_outcomes=tuple(prohibited),
        )

    @property
    def allows_file_mutation(self) -> bool:
        return RequestedOutcome.FILE_MUTATION in self.requested_outcomes

    @property
    def requires_execution(self) -> bool:
        return self.code_execution_allowed or self.command_execution_allowed

    @property
    def command_execution_allowed(self) -> bool:
        """Any file-mutation grant carries command execution — a run that
        writes files may verify what it wrote — unless execution was
        explicitly prohibited. Declared outcomes stay untouched so this
        rides as a *permission*, never a plan requirement."""
        if RequestedOutcome.COMMAND_EXECUTION in self.prohibited_outcomes:
            return False
        return (
            RequestedOutcome.COMMAND_EXECUTION in self.requested_outcomes
            or RequestedOutcome.FILE_MUTATION in self.requested_outcomes
        )

    @property
    def code_execution_allowed(self) -> bool:
        return RequestedOutcome.CODE_EXECUTION in self.requested_outcomes

    @property
    def internal_effect_policy(self) -> str:
        return "RUN_SCOPED_TEMP_ONLY"

    @staticmethod
    def _task_effect_scope(task: Mapping[str, Any]) -> str:
        policy = task.get("policy") or {}
        if isinstance(policy, Mapping):
            return str(
                policy.get("effect_scope", EffectScope.USER_EFFECT.value)
            )
        return EffectScope.USER_EFFECT.value

    @staticmethod
    def _task_targets(task: Mapping[str, Any]) -> tuple[str, ...]:
        inputs = task.get("inputs") or {}
        targets = [str(task.get("target", ""))]
        if isinstance(inputs, Mapping):
            if str(task.get("verb", "")).lower() in {"copy", "move"}:
                targets.extend([
                    str(inputs.get("source", "")),
                    str(inputs.get("destination", "")),
                ])
        return tuple(value for value in targets if value.strip())

    @staticmethod
    def allows_internal_path(path: str) -> bool:
        normalized = _normalize_scope(path).lstrip("/")
        return normalized == ".tsagent/tmp" or normalized.startswith(
            ".tsagent/tmp/"
        )

    def allows_path(self, path: str) -> bool:
        target = _normalize_scope(path)
        for scope in self.write_scopes:
            if scope == "." and not target.startswith("/"):
                return True  # whole-workspace grant
            if scope.endswith("/"):
                if target == scope.rstrip("/") or target.startswith(scope):
                    return True
            elif target == scope:
                return True
        return False

    def validate_task(self, task: Mapping[str, Any]) -> str | None:
        """Reject a task whose effect is outside the user's request."""

        verb = str(task.get("verb", "")).lower()
        for path in task.get('output_paths', []):
            if not self.allows_file_mutation or not self.allows_path(path):
                return f'EFFECT_SCOPE_VIOLATION: 产物声明不在授权范围内: {path}'
        if verb in _MUTATION_VERBS:
            effect_scope = self._task_effect_scope(task)
            if effect_scope == EffectScope.INTERNAL_EXECUTION_EFFECT.value:
                if not all(self.allows_internal_path(target) for target in self._task_targets(task)):
                    return (
                        "INTERNAL_EFFECT_SCOPE_VIOLATION: 临时执行效果只能写入 Run-scoped temp namespace"
                    )
                return None
            if effect_scope != EffectScope.USER_EFFECT.value:
                return f"EFFECT_SCOPE_VIOLATION: 非法 effect scope: {effect_scope}"
            if not self.allows_file_mutation:
                return (
                    "EFFECT_SCOPE_VIOLATION: 用户未授权文件写入、修改、删除、移动或复制"
                )
            targets = self._task_targets(task)
            for target in targets:
                if target.strip() and not self.allows_path(target):
                    return (
                        "EFFECT_SCOPE_VIOLATION: 目标不在用户授权的写入范围内: "
                        f"{target}"
                    )
            if not self.write_scopes:
                return (
                    "EFFECT_SCOPE_VIOLATION: 文件变更请求没有明确的授权目标路径"
                )

        if verb in _EXECUTION_VERBS and not self.requires_execution:
            return (
                "EFFECT_SCOPE_VIOLATION: 用户未要求执行代码或命令，禁止执行副作用"
            )
        return None

    def validate_plan(
        self,
        plan: Any,
        remaining_tasks: Sequence[Mapping[str, Any]] = (),
    ) -> str | None:
        """Ensure explicit execution cannot be silently routed to LLM only."""

        steps = getattr(plan, "steps", ()) or ()
        executor = str(getattr(plan, "executor", "") or "")
        tools = {str(getattr(step, "tool", "")) for step in steps}
        future_verbs = {
            str(task.get("verb", "")).lower() for task in remaining_tasks
        }
        for outcome in self.requested_outcomes:
            if outcome is RequestedOutcome.USER_VISIBLE_OUTPUT:
                continue
            accepted_tools, accepted_verbs = _PLAN_REQUIREMENTS.get(
                outcome, (frozenset(), frozenset())
            )
            if tools & accepted_tools or future_verbs & accepted_verbs:
                continue
            return (
                "EXECUTION_REQUIRED: 用户明确要求"
                f" {outcome.value}，当前计划没有对应的可执行步骤"
                f"（executor={executor or 'unknown'}）"
            )
        return None

    def validate_tool_action(self, tool: str, args: Mapping[str, Any]) -> str | None:
        """Authorize the actual dynamic action, not only its Task wording."""

        if tool in _MUTATION_TOOLS:
            if not self.allows_file_mutation:
                return (
                    "EFFECT_SCOPE_VIOLATION: 用户未授权文件写入、修改、删除、移动或复制"
                )
            if tool in {"filesystem.copy", "filesystem.move"}:
                targets = (str(args.get("destination", "")),)
            else:
                targets = (str(args.get("path", "")),)
            if not self.write_scopes:
                return "EFFECT_SCOPE_VIOLATION: 文件变更请求没有明确的授权目标路径"
            for target in targets:
                if not target or not self.allows_path(target):
                    return (
                        "EFFECT_SCOPE_VIOLATION: 目标不在用户授权的写入范围内: "
                        f"{target}"
                    )
        if tool in _EXECUTION_TOOLS and not self.requires_execution:
            return "EFFECT_SCOPE_VIOLATION: 用户未要求执行代码或命令，禁止执行副作用"
        return None

    def validate_plan_set(
        self,
        plans: Sequence[Any],
        available_dynamic_tools: Sequence[str] = (),
    ) -> str | None:
        """Validate requested capabilities against the complete execution set.

        A user request may intentionally span several tasks, such as a write
        followed by execution.  Authorization is therefore checked before the
        first side effect against the union of all compiled plans, rather than
        requiring every individual task to satisfy every requested outcome.
        """

        tools: set[str] = set(available_dynamic_tools)
        for plan in plans:
            if str(getattr(plan, "executor", "") or "") == "llm":
                continue
            steps = getattr(plan, "steps", ()) or ()
            tools.update(str(getattr(step, "tool", "")) for step in steps)

        for outcome in self.requested_outcomes:
            if outcome is RequestedOutcome.USER_VISIBLE_OUTPUT:
                continue
            accepted_tools, _ = _PLAN_REQUIREMENTS.get(
                outcome, (frozenset(), frozenset())
            )
            if tools & accepted_tools:
                continue
            if outcome is RequestedOutcome.FILE_MUTATION:
                # A missing write task is a delivery gap owned by the goal
                # gate and delivery-repair replan, not a capability violation;
                # write_scopes already authorize any future mutation.
                continue
            return (
                "EXECUTION_REQUIRED: 用户明确要求"
                f" {outcome.value}，完整计划没有对应的可执行步骤"
            )
        return None


__all__ = ["EffectAuthorization"]
