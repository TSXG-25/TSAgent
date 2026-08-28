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
    analyze_requested_outcomes,
)
from .resource_binding import (
    BoundTarget,
    extract_bound_targets,
    normalize_resource_path,
)


_MUTATION_VERBS = frozenset({"write", "modify", "delete", "move", "copy"})
_EXECUTION_VERBS = frozenset({"execute"})
_EXECUTION_TOOLS = frozenset({
    "shell",
    "run_python",
    "run_python_file",
    "process.execute",
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
    normalized = normalize_resource_path(value)
    directory_scope = normalized.endswith("/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized.startswith("/"):
        return normalized
    normalized = PurePosixPath(normalized).as_posix()
    return normalized + "/" if directory_scope and normalized != "." else normalized


def _extract_write_scopes(text: str) -> tuple[str, ...]:
    return tuple(target.path for target in extract_bound_targets(text))


@dataclass(frozen=True)
class EffectAuthorization:
    """Immutable authorization derived from one original user request."""

    requested_outcomes: tuple[RequestedOutcome, ...]
    write_scopes: tuple[str, ...]
    bound_targets: tuple[BoundTarget, ...] = ()

    @classmethod
    def from_request(cls, user_input: str) -> "EffectAuthorization":
        return cls(
            requested_outcomes=analyze_requested_outcomes(user_input),
            write_scopes=_extract_write_scopes(user_input),
            bound_targets=extract_bound_targets(user_input),
        )

    @property
    def allows_file_mutation(self) -> bool:
        return RequestedOutcome.FILE_MUTATION in self.requested_outcomes

    @property
    def requires_execution(self) -> bool:
        return any(
            outcome in self.requested_outcomes
            for outcome in (
                RequestedOutcome.CODE_EXECUTION,
                RequestedOutcome.COMMAND_EXECUTION,
            )
        )

    @property
    def command_execution_allowed(self) -> bool:
        return RequestedOutcome.COMMAND_EXECUTION in self.requested_outcomes

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
            if scope.endswith("/"):
                if target == scope.rstrip("/") or target.startswith(scope):
                    return True
            elif target == scope:
                return True
        return False

    def validate_task(self, task: Mapping[str, Any]) -> str | None:
        """Reject a task whose effect is outside the user's request."""

        verb = str(task.get("verb", "")).lower()
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

    def validate_plan_set(
        self,
        plans: Sequence[Any],
    ) -> str | None:
        """Validate requested capabilities against the complete execution set.

        A user request may intentionally span several tasks, such as a write
        followed by execution.  Authorization is therefore checked before the
        first side effect against the union of all compiled plans, rather than
        requiring every individual task to satisfy every requested outcome.
        """

        tools: set[str] = set()
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
            return (
                "EXECUTION_REQUIRED: 用户明确要求"
                f" {outcome.value}，完整计划没有对应的可执行步骤"
            )
        return None


__all__ = ["EffectAuthorization"]
