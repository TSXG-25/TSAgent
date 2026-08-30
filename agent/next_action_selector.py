"""Production next-action selection boundary for v2.4B.

The Selector owns one decision only::

    Task projection + execution-state projection + observation -> NextAction

It does not plan, execute tools, mutate Runtime state, inspect durable stores,
or own recovery policy.  Provider evidence is returned separately from the
canonical action so diagnostics cannot change action truth.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any, Literal, Mapping

from jsonschema import ValidationError as JsonSchemaValidationError
from jsonschema import validate as validate_json_schema
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent.action_result import ActionResult
from agent.execution_errors import stable_error_message
from agent.interruption import RunInterruptionRequested, await_interruptibly
from agent.next_action import ActionKind, NextAction
from agent.tool_action_projection import ToolActionProjection


_EFFECT_TOOLS = frozenset({
    "filesystem.write",
    "filesystem.copy",
    "filesystem.move",
    "filesystem.delete",
    "run_python",
    "run_python_file",
    "shell",
})


class TaskProjection(BaseModel):
    """Read-only task facts visible to the Selector."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    verb: Literal[
        "resolve", "read", "write", "modify", "execute", "search",
        "list", "explain", "delete", "move", "copy",
    ]
    target: str = ""
    target_type: Literal["file", "symbol", "text", "none"] = "none"
    status: Literal["pending", "running", "succeeded", "skipped", "failed"]
    dependencies: tuple[str, ...] = ()


class ExecutionStateProjection(BaseModel):
    """Narrow Runtime projection; never a raw checkpoint or state dump."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    goal: str
    current_task_id: str = ""
    tasks: tuple[TaskProjection, ...] = ()
    required_outcomes: tuple[str, ...] = ()
    completed_outcomes: tuple[str, ...] = ()
    answer_ready: bool = False
    available_actions: tuple[ToolActionProjection, ...] = ()
    completion_evidence: tuple[str, ...] = ()
    history: tuple[dict[str, Any], ...] = ()
    facts: dict[str, Any] = Field(default_factory=dict)


SELECTOR_STATE_PROJECTION_VERSION = "v2.4B-selector-state-v2"


def selector_state_projection_hash() -> str:
    """Hash the production Selector state envelope."""

    payload = {
        "version": SELECTOR_STATE_PROJECTION_VERSION,
        "schema": ExecutionStateProjection.model_json_schema(),
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ActionObservation(BaseModel):
    """Latest canonical action/result pair projected by Runtime."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    last_action: NextAction | None = None
    last_result: ActionResult | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_action": (
                self.last_action.to_dict() if self.last_action is not None else None
            ),
            "last_result": (
                self.last_result.to_dict() if self.last_result is not None else None
            ),
        }


@dataclass(frozen=True)
class NextActionSelectionEvidence:
    """Observable Provider/format path for one bounded selection."""

    provider: str
    provider_path: Literal["SINGLE_PROVIDER"]
    format_path: Literal[
        "STRUCTURED_ONLY", "STRUCTURED_TO_RAW_FALLBACK", "RAW_ONLY",
    ]
    raw_output: dict[str, Any] | str
    structured_error: str = ""


@dataclass(frozen=True)
class NextActionSelection:
    action: NextAction
    evidence: NextActionSelectionEvidence


class NextActionSelectionError(RuntimeError):
    """Stable failure at the Provider/schema/action contract boundary."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        provider: str,
        format_path: str,
        raw_output: dict[str, Any] | str | None = None,
        candidate: NextAction | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.provider = provider
        self.format_path = format_path
        self.raw_output = raw_output
        self.candidate = candidate


class _NextActionSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["tool", "answer", "ask"]
    tool: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    task_id: str = ""

    @model_validator(mode="after")
    def _validate_kind_fields(self) -> "_NextActionSchema":
        if self.kind == "tool":
            if not self.tool or not self.task_id:
                raise ValueError("tool action requires tool and task_id")
        elif self.tool or self.args or self.task_id:
            raise ValueError("answer/ask action cannot carry tool fields")
        return self

    def to_action(self) -> NextAction:
        return NextAction(
            kind=ActionKind(self.kind),
            tool=self.tool,
            args=dict(self.args),
            reason=self.reason,
            task_id=self.task_id,
        )


NEXT_ACTION_PROMPT = """You are TSAgent's bounded NextAction Selector.

Choose exactly one next action from the supplied Task, projected Runtime
state, and latest observation. Return one JSON object with exactly these five
fields: kind, tool, args, reason, task_id. The three valid envelopes are
mutually exclusive:

TOOL:
{"kind":"tool","tool":"<available canonical tool>","args":{},"reason":"<brief reason>","task_id":"<state task id>"}

ANSWER:
{"kind":"answer","tool":"","args":{},"reason":"<brief reason>","task_id":""}

ASK:
{"kind":"ask","tool":"","args":{},"reason":"<brief reason>","task_id":""}

Do not use null. Do not add answer, question, message, content, or any other
field. For answer and ask, tool and task_id must be empty strings and args must
be an empty object. Never put answer or question prose in args.

Rules:
- kind is exactly one of tool, answer, ask.
- Projected Runtime state is authoritative over task wording and model
  inference. When state.answer_ready is true, choose ANSWER; do not start a new
  tool action and do not ask for information already represented as ready.
  When state.answer_ready is false, never choose ANSWER.
- A tool action must use a canonical name from state.available_actions and a
  task id from state.tasks whose dependencies are complete. Bind args against
  that action's args_schema.
- Bind arguments from explicit task/state/observation facts. Never invent or
  rename a path, URL, command, content, capability, argument, or completed
  effect.
- Choose ASK when required information or capability is absent.
- A retry is permitted only when the projected last result says retryable=true.
- Never repeat a verified effect. Verification may use an available read or
  execution primitive when the prior result is not verified.
- Do not plan, execute tools, alter state, create retry budgets, or perform
  recovery policy.
"""


class NextActionSelector:
    """Provider-backed production boundary for one NextAction decision."""

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
        task: TaskProjection | None,
        state: ExecutionStateProjection,
        observation: ActionObservation | None,
    ) -> NextAction:
        selection = await self.select_with_evidence(task, state, observation)
        return selection.action

    async def select_with_evidence(
        self,
        task: TaskProjection | None,
        state: ExecutionStateProjection,
        observation: ActionObservation | None,
    ) -> NextActionSelection:
        provider, provider_name, supports_structured = self._resolve_provider()
        messages = self._messages(task, state, observation)
        structured_error = ""

        if supports_structured:
            try:
                structured = provider.with_structured_output(_NextActionSchema)
                response = await await_interruptibly(structured.ainvoke(messages))
                schema = self._schema_from_structured(response)
                raw_output: dict[str, Any] | str = schema.model_dump()
                format_path = "STRUCTURED_ONLY"
            except RunInterruptionRequested:
                raise
            except Exception as error:
                structured_error = stable_error_message(
                    error,
                    fallback="structured next-action selection failed",
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

        action = schema.to_action()
        self._validate_action(
            action,
            state,
            observation,
            provider=provider_name,
            format_path=format_path,
            raw_output=raw_output,
        )
        return NextActionSelection(
            action=action,
            evidence=NextActionSelectionEvidence(
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
    ) -> tuple[_NextActionSchema, str]:
        try:
            response = await await_interruptibly(provider.ainvoke(messages))
        except RunInterruptionRequested:
            raise
        except Exception as raw_error:
            raise NextActionSelectionError(
                "PROVIDER_ERROR",
                stable_error_message(
                    raw_error,
                    fallback="next-action provider request failed",
                ),
                provider=provider_name,
                format_path=format_path,
            ) from raw_error
        raw_output = str(getattr(response, "content", ""))
        try:
            return _NextActionSchema.model_validate_json(raw_output), raw_output
        except Exception as parse_error:
            raise NextActionSelectionError(
                "SCHEMA_INVALID",
                stable_error_message(
                    parse_error,
                    fallback="next-action response is not canonical JSON",
                ),
                provider=provider_name,
                format_path=format_path,
                raw_output=raw_output,
            ) from parse_error

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
        supports_structured = provider_name != "ollama"
        return provider, provider_name, supports_structured

    @staticmethod
    def _messages(
        task: TaskProjection | None,
        state: ExecutionStateProjection,
        observation: ActionObservation | None,
    ) -> list[Any]:
        payload = {
            "task": task.model_dump(mode="json") if task is not None else None,
            "state": state.model_dump(mode="json"),
            "observation": (observation or ActionObservation()).to_dict(),
        }
        return [
            SystemMessage(content=NEXT_ACTION_PROMPT),
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
    def _schema_from_structured(response: Any) -> _NextActionSchema:
        if isinstance(response, _NextActionSchema):
            return response
        if isinstance(response, Mapping):
            return _NextActionSchema.model_validate(response)
        if hasattr(response, "model_dump"):
            return _NextActionSchema.model_validate(response.model_dump())
        raise TypeError("structured Provider returned an unsupported action object")

    @staticmethod
    def _validate_action(
        action: NextAction,
        state: ExecutionStateProjection,
        observation: ActionObservation | None,
        *,
        provider: str,
        format_path: str,
        raw_output: dict[str, Any] | str,
    ) -> None:
        if action.kind is ActionKind.ANSWER:
            if not state.answer_ready:
                raise NextActionSelectionError(
                    "PREMATURE_ANSWER",
                    "answer requires state.answer_ready=true",
                    provider=provider,
                    format_path=format_path,
                    raw_output=raw_output,
                    candidate=action,
                )
            return
        if action.kind is ActionKind.ASK:
            return

        available_actions = {item.tool: item for item in state.available_actions}
        available_action = available_actions.get(action.tool)
        if available_action is None:
            raise NextActionSelectionError(
                "UNAVAILABLE_TOOL",
                f"tool is not available: {action.tool}",
                provider=provider,
                format_path=format_path,
                raw_output=raw_output,
                candidate=action,
            )
        try:
            validate_json_schema(action.args, available_action.args_schema)
        except JsonSchemaValidationError as error:
            raise NextActionSelectionError(
                "ARGUMENT_SCHEMA_INVALID",
                f"arguments do not match {action.tool}: {error.message}",
                provider=provider,
                format_path=format_path,
                raw_output=raw_output,
                candidate=action,
            ) from error
        tasks = {item.id: item for item in state.tasks}
        target_task = tasks.get(action.task_id)
        if target_task is None:
            raise NextActionSelectionError(
                "WRONG_TASK",
                f"unknown task id: {action.task_id}",
                provider=provider,
                format_path=format_path,
                raw_output=raw_output,
                candidate=action,
            )
        statuses = {item.id: item.status for item in state.tasks}
        if any(
            statuses.get(dependency) not in {"succeeded", "skipped"}
            for dependency in target_task.dependencies
        ):
            raise NextActionSelectionError(
                "DEPENDENCY_VIOLATION",
                f"task dependencies are not complete: {action.task_id}",
                provider=provider,
                format_path=format_path,
                raw_output=raw_output,
                candidate=action,
            )

        latest = observation or ActionObservation()
        if (
            latest.last_action is not None
            and latest.last_result is not None
            and latest.last_result.verified is True
            and action.tool in _EFFECT_TOOLS
            and latest.last_action.tool == action.tool
            and latest.last_action.task_id == action.task_id
            and latest.last_action.args == action.args
        ):
            raise NextActionSelectionError(
                "DUPLICATE_EFFECT",
                "verified effect cannot be selected again",
                provider=provider,
                format_path=format_path,
                raw_output=raw_output,
                candidate=action,
            )
        if (
            latest.last_action is not None
            and latest.last_result is not None
            and latest.last_result.ok is False
            and latest.last_result.retryable is not True
            and latest.last_action.tool == action.tool
            and latest.last_action.task_id == action.task_id
            and latest.last_action.args == action.args
        ):
            raise NextActionSelectionError(
                "UNSAFE_RETRY",
                "non-retryable action cannot be selected again",
                provider=provider,
                format_path=format_path,
                raw_output=raw_output,
                candidate=action,
            )


__all__ = [
    "ActionObservation",
    "ExecutionStateProjection",
    "NextActionSelection",
    "NextActionSelectionError",
    "NextActionSelectionEvidence",
    "NextActionSelector",
    "SELECTOR_STATE_PROJECTION_VERSION",
    "TaskProjection",
    "selector_state_projection_hash",
]
