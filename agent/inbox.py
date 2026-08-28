"""Small, explicit input queues for result-driven execution."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass
class AgentInbox:
    """Separate current-step observations from new-turn requests."""

    next_step: list[dict[str, Any]] = field(default_factory=list)
    next_turn: list[str] = field(default_factory=list)

    def add_step(self, observation: Mapping[str, Any]) -> None:
        """Queue one structured observation for the next action decision."""
        self.next_step.append(dict(observation))

    def add_turn(self, request: str) -> None:
        """Queue a new user/goal-round request."""
        value = str(request or "").strip()
        if value:
            self.next_turn.append(value)

    def consume_steps(self) -> tuple[dict[str, Any], ...]:
        """Return and clear current-step observations."""
        values = tuple(self.next_step)
        self.next_step.clear()
        return values

    def to_dict(self) -> dict[str, Any]:
        """Return the JSON-safe Runtime projection."""
        return {
            "next_step": [dict(item) for item in self.next_step],
            "next_turn": list(self.next_turn),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any] | None) -> "AgentInbox":
        """Restore an inbox projection from AgentState."""
        data = dict(value or {})
        next_step = data.get("next_step", ())
        next_turn = data.get("next_turn", ())
        return cls(
            next_step=[dict(item) for item in next_step if isinstance(item, Mapping)],
            next_turn=[str(item) for item in next_turn if str(item).strip()],
        )


__all__ = ["AgentInbox"]
