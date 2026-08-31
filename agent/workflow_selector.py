"""Provider-backed Workflow capability decision boundary.

The Selector owns one decision only::

    Goal + projected context + available Workflow definitions
        -> WorkflowDecision

It does not execute or resume a Workflow, mutate Runtime state, inspect the
Registry, or own Planner and recovery policy.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Literal, Mapping

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent.execution_errors import stable_error_message
from agent.interruption import RunInterruptionRequested, await_interruptibly
from agent.workflow_decision import WorkflowDecision, WorkflowDecisionKind


class WorkflowDefinitionProjection(BaseModel):
    """One available Workflow definition projected by composition code."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    version: str
    description: str
    required_bindings: tuple[str, ...] = ()
    defaults: dict[str, Any] = Field(default_factory=dict)
    required_artifacts: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    output_types: tuple[str, ...] = ()


class ActiveWorkflowProjection(BaseModel):
    """Continuation facts already authorized by Runtime resume policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    workflow_id: str
    status: Literal["active", "blocked", "completed"]
    reuse_allowed: bool


class WorkflowContextProjection(BaseModel):
    """Narrow Runtime facts visible to Workflow selection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifacts: dict[str, Any] = Field(default_factory=dict)
    capabilities: tuple[str, ...] = ()
    facts: dict[str, Any] = Field(default_factory=dict)
    active_workflow: ActiveWorkflowProjection | None = None


WORKFLOW_PROJECTION_VERSION = "v2.4C-workflow-projection-v1"


def workflow_projection_hash() -> str:
    payload = {
        "version": WORKFLOW_PROJECTION_VERSION,
        "workflow": WorkflowDefinitionProjection.model_json_schema(),
        "context": WorkflowContextProjection.model_json_schema(),
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class WorkflowSelectionEvidence:
    provider: str
    provider_path: Literal["SINGLE_PROVIDER"]
    format_path: Literal[
        "STRUCTURED_ONLY", "STRUCTURED_TO_RAW_FALLBACK", "RAW_ONLY",
    ]
    raw_output: dict[str, Any] | str
    structured_error: str = ""


@dataclass(frozen=True)
class WorkflowSelection:
    decision: WorkflowDecision
    evidence: WorkflowSelectionEvidence


class WorkflowSelectionError(RuntimeError):
    """Stable Provider/schema/policy failure for one Workflow decision."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        provider: str,
        format_path: str,
        raw_output: dict[str, Any] | str | None = None,
        candidate: WorkflowDecision | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.provider = provider
        self.format_path = format_path
        self.raw_output = raw_output
        self.candidate = candidate


class _WorkflowDecisionSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["instantiate", "reuse", "decline", "ask"]
    workflow_id: str = ""
    bindings: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""

    @model_validator(mode="after")
    def _validate_kind_fields(self) -> "_WorkflowDecisionSchema":
        if self.kind == "instantiate":
            if not self.workflow_id:
                raise ValueError("instantiate requires workflow_id")
        elif self.kind == "reuse":
            if not self.workflow_id:
                raise ValueError("reuse requires workflow_id")
            if self.bindings:
                raise ValueError("reuse cannot replace durable bindings")
        elif self.workflow_id or self.bindings:
            raise ValueError("decline/ask cannot carry workflow fields")
        return self

    def to_decision(self) -> WorkflowDecision:
        return WorkflowDecision.model_validate(self.model_dump())


WORKFLOW_SELECTION_PROMPT = """You are TSAgent's bounded Workflow Selector.

Choose exactly one decision from the supplied Goal, projected Runtime context,
and available Workflow definitions. Return one JSON object with exactly these
four fields: kind, workflow_id, bindings, reason.

The mutually exclusive envelopes are:

INSTANTIATE:
{"kind":"instantiate","workflow_id":"<available id>","bindings":{},"reason":"<brief reason>"}

REUSE:
{"kind":"reuse","workflow_id":"<active id>","bindings":{},"reason":"<brief reason>"}

DECLINE:
{"kind":"decline","workflow_id":"","bindings":{},"reason":"<brief reason>"}

ASK:
{"kind":"ask","workflow_id":"","bindings":{},"reason":"<brief reason>"}

Rules:
- Instantiate only when the complete Goal matches one available Workflow.
- Bind every required binding from explicit Goal/context facts or catalog
  defaults. Never invent a path, topic, environment, tag, artifact, account,
  capability, or binding name.
- Required artifacts and capabilities must already exist in the projection.
- Reuse only the projected active workflow when status=active and
  reuse_allowed=true. Reuse never replaces durable bindings.
- Decline for a simple task, an incompatible output, an explicitly excluded
  Workflow, or a Goal better handled by ordinary Planner/Task execution.
- Ask when a matching Workflow lacks a required binding or grounded fact.
- Do not plan tasks, choose tools, execute or resume a Workflow, inspect
  Registry/Checkpoint/Workspace, mutate state, or create retry policy.
"""


class WorkflowDecisionSelector:
    """Production entry for one bounded WorkflowDecision."""

    def __init__(
        self,
        *,
        provider: Any | None = None,
        provider_name: str = "",
        supports_structured_output: bool | None = None,
    ) -> None:
        self._provider = provider
        self._provider_name = provider_name
        self._supports_structured_output = supports_structured_output

    async def select(
        self,
        goal: str,
        context: WorkflowContextProjection,
        available_workflows: tuple[WorkflowDefinitionProjection, ...],
    ) -> WorkflowDecision:
        selection = await self.select_with_evidence(
            goal,
            context,
            available_workflows,
        )
        return selection.decision

    async def select_with_evidence(
        self,
        goal: str,
        context: WorkflowContextProjection,
        available_workflows: tuple[WorkflowDefinitionProjection, ...],
    ) -> WorkflowSelection:
        provider, provider_name, supports_structured = self._resolve_provider()
        messages = self._messages(goal, context, available_workflows)
        structured_error = ""

        if supports_structured:
            try:
                structured = provider.with_structured_output(_WorkflowDecisionSchema)
                response = await await_interruptibly(structured.ainvoke(messages))
                schema = self._schema_from_structured(response)
                raw_output: dict[str, Any] | str = schema.model_dump()
                format_path = "STRUCTURED_ONLY"
            except RunInterruptionRequested:
                raise
            except Exception as error:
                structured_error = stable_error_message(
                    error,
                    fallback="structured Workflow selection failed",
                )
                format_path = "STRUCTURED_TO_RAW_FALLBACK"
                schema, raw_output = await self._select_raw(
                    provider,
                    provider_name,
                    messages,
                    format_path,
                )
        else:
            format_path = "RAW_ONLY"
            schema, raw_output = await self._select_raw(
                provider,
                provider_name,
                messages,
                format_path,
            )

        decision = schema.to_decision()
        self._validate_decision(
            decision,
            context,
            available_workflows,
            provider=provider_name,
            format_path=format_path,
            raw_output=raw_output,
        )
        return WorkflowSelection(
            decision=decision,
            evidence=WorkflowSelectionEvidence(
                provider=provider_name,
                provider_path="SINGLE_PROVIDER",
                format_path=format_path,
                raw_output=raw_output,
                structured_error=structured_error,
            ),
        )

    async def _select_raw(
        self,
        provider: Any,
        provider_name: str,
        messages: list[Any],
        format_path: str,
    ) -> tuple[_WorkflowDecisionSchema, str]:
        try:
            response = await await_interruptibly(provider.ainvoke(messages))
        except RunInterruptionRequested:
            raise
        except Exception as error:
            raise WorkflowSelectionError(
                "PROVIDER_ERROR",
                stable_error_message(
                    error,
                    fallback="Workflow selection provider request failed",
                ),
                provider=provider_name,
                format_path=format_path,
            ) from error
        raw_output = str(getattr(response, "content", ""))
        try:
            return _WorkflowDecisionSchema.model_validate_json(raw_output), raw_output
        except Exception as error:
            raise WorkflowSelectionError(
                "SCHEMA_INVALID",
                stable_error_message(
                    error,
                    fallback="Workflow selection response is not canonical JSON",
                ),
                provider=provider_name,
                format_path=format_path,
                raw_output=raw_output,
            ) from error

    def _resolve_provider(self) -> tuple[Any, str, bool]:
        if self._provider is not None:
            provider_name = self._provider_name or type(self._provider).__name__
            supports_structured = (
                self._supports_structured_output
                if self._supports_structured_output is not None
                else callable(getattr(self._provider, "with_structured_output", None))
            )
            return self._provider, provider_name, supports_structured
        from agent.llm import llm

        provider, provider_name = llm._get_active_provider()
        return provider, provider_name, provider_name != "ollama"

    @staticmethod
    def _messages(
        goal: str,
        context: WorkflowContextProjection,
        available_workflows: tuple[WorkflowDefinitionProjection, ...],
    ) -> list[Any]:
        payload = {
            "goal": goal,
            "context": context.model_dump(mode="json"),
            "available_workflows": [
                workflow.model_dump(mode="json")
                for workflow in available_workflows
            ],
        }
        return [
            SystemMessage(content=WORKFLOW_SELECTION_PROMPT),
            HumanMessage(
                content=json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            ),
        ]

    @staticmethod
    def _schema_from_structured(response: Any) -> _WorkflowDecisionSchema:
        if isinstance(response, _WorkflowDecisionSchema):
            return response
        if isinstance(response, Mapping):
            return _WorkflowDecisionSchema.model_validate(response)
        if hasattr(response, "model_dump"):
            return _WorkflowDecisionSchema.model_validate(response.model_dump())
        raise TypeError("structured Provider returned an unsupported decision object")

    @staticmethod
    def _validate_decision(
        decision: WorkflowDecision,
        context: WorkflowContextProjection,
        available_workflows: tuple[WorkflowDefinitionProjection, ...],
        *,
        provider: str,
        format_path: str,
        raw_output: dict[str, Any] | str,
    ) -> None:
        available = {workflow.id: workflow for workflow in available_workflows}
        if decision.kind is WorkflowDecisionKind.INSTANTIATE:
            workflow = available.get(decision.workflow_id)
            if workflow is None:
                raise WorkflowSelectionError(
                    "UNAVAILABLE_WORKFLOW",
                    f"workflow is not available: {decision.workflow_id}",
                    provider=provider,
                    format_path=format_path,
                    raw_output=raw_output,
                    candidate=decision,
                )
            required = set(workflow.required_bindings)
            actual = set(decision.bindings)
            if actual != required or any(
                value is None or value == "" for value in decision.bindings.values()
            ):
                raise WorkflowSelectionError(
                    "BINDINGS_INVALID",
                    f"bindings do not match {decision.workflow_id}",
                    provider=provider,
                    format_path=format_path,
                    raw_output=raw_output,
                    candidate=decision,
                )
            if not set(workflow.required_artifacts).issubset(context.artifacts):
                raise WorkflowSelectionError(
                    "REQUIRED_ARTIFACT_UNAVAILABLE",
                    f"required artifact is unavailable: {decision.workflow_id}",
                    provider=provider,
                    format_path=format_path,
                    raw_output=raw_output,
                    candidate=decision,
                )
            if not set(workflow.required_capabilities).issubset(context.capabilities):
                raise WorkflowSelectionError(
                    "REQUIRED_CAPABILITY_UNAVAILABLE",
                    f"required capability is unavailable: {decision.workflow_id}",
                    provider=provider,
                    format_path=format_path,
                    raw_output=raw_output,
                    candidate=decision,
                )
            return

        if decision.kind is WorkflowDecisionKind.REUSE:
            active = context.active_workflow
            if (
                active is None
                or decision.workflow_id not in available
                or active.workflow_id != decision.workflow_id
                or active.status != "active"
                or not active.reuse_allowed
            ):
                raise WorkflowSelectionError(
                    "UNSAFE_REUSE",
                    "reuse requires the projected active reusable Workflow",
                    provider=provider,
                    format_path=format_path,
                    raw_output=raw_output,
                    candidate=decision,
                )


__all__ = [
    "ActiveWorkflowProjection",
    "WORKFLOW_PROJECTION_VERSION",
    "WorkflowContextProjection",
    "WorkflowDecisionSelector",
    "WorkflowDefinitionProjection",
    "WorkflowSelection",
    "WorkflowSelectionError",
    "WorkflowSelectionEvidence",
    "workflow_projection_hash",
]
