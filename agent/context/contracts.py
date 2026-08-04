"""Typed context views for each runtime phase.

``CognitiveContext`` remains the planner's domain model and
``ExecutionContext`` remains the mutable execution container.  These views
make the boundary explicit without introducing a new mutable God object:

    RuntimeContext  -> shared request identity
    PlannerContext  -> planning-only inputs
    ExecutorContext -> execution-only cache and target
    ReflectionContext -> failure evidence for deterministic reflection

RuntimeContext, ExecutorContext and ReflectionContext are frozen snapshots and
copy mappings on construction. PlannerContext intentionally preserves the
existing mutable cognitive timeline/resolved-query cache for contract
compatibility; it is phase-owned and must not escape into Runtime state.
"""
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping, Optional

from agent.task import Task


def _freeze_mapping(value: Optional[Mapping[str, Any]]) -> Mapping[str, Any]:
    """Return a shallow immutable snapshot of a mapping."""
    return MappingProxyType(dict(value or {}))


@dataclass(frozen=True)
class RuntimeContext:
    """Shared request identity available to all phase views."""

    request_id: str = ""
    user_id: str = ""
    query: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))


@dataclass(frozen=True)
class ExecutorContext:
    """Read-only snapshot exposed to an executor."""

    runtime: RuntimeContext
    task: Task
    artifacts: Mapping[str, Any] = field(default_factory=dict)
    facts: Mapping[str, Any] = field(default_factory=dict)
    action_history: tuple = ()
    failure_history: tuple = ()
    variables: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "artifacts", _freeze_mapping(self.artifacts))
        object.__setattr__(self, "facts", _freeze_mapping(self.facts))
        object.__setattr__(self, "variables", _freeze_mapping(self.variables))
        object.__setattr__(self, "action_history", tuple(self.action_history or ()))
        object.__setattr__(self, "failure_history", tuple(self.failure_history or ()))


@dataclass(frozen=True)
class ReflectionContext:
    """Minimal evidence boundary for deterministic Reflection."""

    runtime: RuntimeContext
    task_id: str
    failure: str
    evidence: tuple = ()
    symptom: str = ""
    retry_count: int = 0
    last_action: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence", tuple(self.evidence or ()))


from agent.cognition.cognitive_context import PlannerContext
