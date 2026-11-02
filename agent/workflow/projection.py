"""Workflow definition projection — the static facts a caller needs to
decide bindings before instantiating a Workflow."""
from __future__ import annotations

from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, model_validator


class WorkflowDefinitionProjection(BaseModel):
    """One available Workflow definition projected by composition code."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    version: str
    description: str
    required_bindings: tuple[str, ...] = ()
    defaults: dict[str, Any] = Field(default_factory=dict)
    # Required bindings whose bound value must name an input file that
    # already exists in the Run workspace.
    required_input_paths: tuple[str, ...] = ()
    required_artifacts: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    output_types: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _input_paths_are_required_bindings(
        self,
    ) -> "WorkflowDefinitionProjection":
        undeclared = set(self.required_input_paths) - set(self.required_bindings)
        if undeclared:
            raise ValueError(
                "required_input_paths must name required bindings: "
                f"{sorted(undeclared)}"
            )
        return self

    @classmethod
    def from_workflow(cls, workflow: Any) -> "WorkflowDefinitionProjection":
        metadata = workflow.metadata or {}
        capability = metadata.get("capability")
        if not isinstance(capability, Mapping):
            raise ValueError(
                f"WORKFLOW_CAPABILITY_METADATA_MISSING: {workflow.id}"
            )
        return cls(
            id=workflow.id,
            version=workflow.version,
            description=workflow.description,
            **capability,
        )
