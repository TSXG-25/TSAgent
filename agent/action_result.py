"""Canonical result of one runtime action.

The action value is machine truth. ``content`` is only the model/user
projection and never establishes that an effect occurred.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from agent.failure import (
    ClassificationSource,
    FailureFact,
    failure_fact,
)


@dataclass(frozen=True)
class ActionResult:
    """Provider-neutral result consumed by the next reasoning step."""

    ok: bool
    value: Any = None
    content: str = ""
    error_code: str | None = None
    failure_kind: str | None = None
    classification_source: str | None = None
    retryable: bool | None = None
    additional_context: tuple[str, ...] = ()
    concludes_turn: bool = False
    verified: bool | None = None

    @classmethod
    def success(
        cls,
        *,
        value: Any = None,
        content: str = "",
        verified: bool | None = None,
        additional_context: tuple[str, ...] = (),
        concludes_turn: bool = False,
    ) -> "ActionResult":
        """Create a successful action result with no error code."""
        return cls(
            ok=True,
            value=value,
            content=content,
            verified=verified,
            additional_context=additional_context,
            concludes_turn=concludes_turn,
        )

    @classmethod
    def failure(
        cls,
        *,
        error_code: str,
        content: str = "",
        additional_context: tuple[str, ...] = (),
        verified: bool | None = False,
        failure_kind: str | None = None,
        classification_source: str | None = None,
        retryable: bool | None = None,
        failure: FailureFact | None = None,
    ) -> "ActionResult":
        """Create a failure result without a successful machine value."""
        fact = failure or failure_fact(
            error_code,
            message=content,
            classification_source=(
                ClassificationSource(classification_source)
                if classification_source is not None
                else ClassificationSource.STRUCTURED
            ),
            retryable=retryable,
        )
        return cls(
            ok=False,
            content=content,
            error_code=error_code,
            failure_kind=failure_kind or fact.kind.value,
            classification_source=(
                classification_source or fact.classification_source.value
            ),
            retryable=fact.retryable if retryable is None else retryable,
            additional_context=additional_context,
            verified=verified,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly projection for Runtime evidence."""
        return {
            "ok": self.ok,
            "value": self.value if self.ok else None,
            "content": self.content,
            "error_code": self.error_code,
            "failure_kind": self.failure_kind,
            "classification_source": self.classification_source,
            "retryable": self.retryable,
            "additional_context": list(self.additional_context),
            "concludes_turn": self.concludes_turn,
            "verified": self.verified,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ActionResult":
        """Restore the canonical Runtime observation projection."""

        return cls(
            ok=bool(value.get("ok", False)),
            value=value.get("value"),
            content=str(value.get("content", "")),
            error_code=(
                str(value["error_code"])
                if value.get("error_code") is not None
                else None
            ),
            failure_kind=(
                str(value["failure_kind"])
                if value.get("failure_kind") is not None
                else None
            ),
            classification_source=(
                str(value["classification_source"])
                if value.get("classification_source") is not None
                else None
            ),
            retryable=value.get("retryable"),
            additional_context=tuple(value.get("additional_context") or ()),
            concludes_turn=bool(value.get("concludes_turn", False)),
            verified=value.get("verified"),
        )


__all__ = ["ActionResult"]
