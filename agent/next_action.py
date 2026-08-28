"""Provider-neutral next-action contract for the result-driven loop.

The planner may provide a task set, but execution advances one action at a
time.  ``NextAction`` records that choice separately from the result produced
by the action so an observation cannot be mistaken for a completion claim.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ActionKind(str, Enum):
    TOOL = "tool"
    ANSWER = "answer"
    ASK = "ask"


@dataclass(frozen=True)
class NextAction:
    """One bounded action selected from the current goal facts."""

    kind: ActionKind
    tool: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    task_id: str = ""

    @classmethod
    def tool_call(
        cls,
        tool: str,
        *,
        task_id: str = "",
        args: dict[str, Any] | None = None,
        reason: str = "",
    ) -> "NextAction":
        return cls(
            kind=ActionKind.TOOL,
            tool=tool,
            task_id=task_id,
            args=dict(args or {}),
            reason=reason,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "tool": self.tool,
            "args": dict(self.args),
            "reason": self.reason,
            "task_id": self.task_id,
        }


__all__ = ["ActionKind", "NextAction"]
