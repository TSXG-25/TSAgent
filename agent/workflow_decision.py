"""Canonical decision record for Workflow capability selection."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class WorkflowDecisionKind(str, Enum):
    INSTANTIATE = "instantiate"
    REUSE = "reuse"
    DECLINE = "decline"
    ASK = "ask"


class WorkflowDecision(BaseModel):
    """One bounded Workflow decision; it never executes or resumes a Workflow."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: WorkflowDecisionKind
    workflow_id: str = ""
    bindings: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""

    @model_validator(mode="after")
    def _validate_envelope(self) -> "WorkflowDecision":
        if self.kind is WorkflowDecisionKind.INSTANTIATE:
            if not self.workflow_id:
                raise ValueError("instantiate requires workflow_id")
        elif self.kind is WorkflowDecisionKind.REUSE:
            if not self.workflow_id:
                raise ValueError("reuse requires workflow_id")
            if self.bindings:
                raise ValueError("reuse cannot replace durable bindings")
        elif self.workflow_id or self.bindings:
            raise ValueError("decline/ask cannot carry workflow fields")
        return self

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


__all__ = ["WorkflowDecision", "WorkflowDecisionKind"]
