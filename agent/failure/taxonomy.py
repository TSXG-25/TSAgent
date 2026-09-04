"""Structured failure taxonomy for the single Runtime spine."""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class FailureKind(str, Enum):
    """Whether a failure is an ordinary action observation or structural."""

    ACTION = "ACTION"
    STRUCTURAL = "STRUCTURAL"


class ClassificationSource(str, Enum):
    """How the Runtime obtained the failure classification."""

    STRUCTURED = "STRUCTURED"
    LEGACY_FALLBACK = "LEGACY_FALLBACK"


class FailureCode(str, Enum):
    # Action-level observations.
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    NO_SEARCH_MATCH = "NO_SEARCH_MATCH"
    TEST_FAILED = "TEST_FAILED"
    COMMAND_NONZERO_EXIT = "COMMAND_NONZERO_EXIT"
    HTTP_404 = "HTTP_404"
    BINARY_FILE = "BINARY_FILE"
    INVALID_TOOL_ARGUMENT = "INVALID_TOOL_ARGUMENT"
    TOOL_TIMEOUT = "TOOL_TIMEOUT"
    PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    PROVIDER_NETWORK = "PROVIDER_NETWORK"
    PROVIDER_REQUEST_INVALID = "PROVIDER_REQUEST_INVALID"
    EXECUTION_ENVIRONMENT_UNAVAILABLE = "EXECUTION_ENVIRONMENT_UNAVAILABLE"
    UNKNOWN_TOOL = "UNKNOWN_TOOL"
    TOOL_EXECUTION_FAILED = "TOOL_EXECUTION_FAILED"
    ACTION_EXECUTION_FAILED = "ACTION_EXECUTION_FAILED"
    ACTION_VERIFICATION_FAILED = "ACTION_VERIFICATION_FAILED"
    FILE_OPERATION_FAILED = "FILE_OPERATION_FAILED"
    FILE_WRITE_UNVERIFIED = "FILE_WRITE_UNVERIFIED"
    FILE_OPERATION_UNVERIFIED = "FILE_OPERATION_UNVERIFIED"

    # Structural failures that require the FailurePolicy seam.
    TOOL_REGISTRY_UNAVAILABLE = "TOOL_REGISTRY_UNAVAILABLE"
    CONTRACT_VIOLATION = "CONTRACT_VIOLATION"
    RUNTIME_INVARIANT_BROKEN = "RUNTIME_INVARIANT_BROKEN"
    PROVIDER_EXHAUSTED = "PROVIDER_EXHAUSTED"
    STATE_CORRUPTION = "STATE_CORRUPTION"
    REPEATED_NO_PROGRESS = "REPEATED_NO_PROGRESS"
    PERMISSION_BOUNDARY = "PERMISSION_BOUNDARY"
    EFFECT_SCOPE_VIOLATION = "EFFECT_SCOPE_VIOLATION"
    UNSUPPORTED_CAPABILITY = "UNSUPPORTED_CAPABILITY"
    UNKNOWN = "UNKNOWN"


_STRUCTURAL_CODES = frozenset({
    FailureCode.UNKNOWN_TOOL.value,
    FailureCode.TOOL_REGISTRY_UNAVAILABLE.value,
    FailureCode.CONTRACT_VIOLATION.value,
    FailureCode.RUNTIME_INVARIANT_BROKEN.value,
    FailureCode.PROVIDER_EXHAUSTED.value,
    FailureCode.STATE_CORRUPTION.value,
    FailureCode.REPEATED_NO_PROGRESS.value,
    FailureCode.PERMISSION_BOUNDARY.value,
    FailureCode.EFFECT_SCOPE_VIOLATION.value,
    FailureCode.UNSUPPORTED_CAPABILITY.value,
})


@dataclass(frozen=True)
class FailureFact:
    """Machine-readable failure evidence attached to one action."""

    code: str
    kind: FailureKind
    classification_source: ClassificationSource
    retryable: bool
    message: str = ""
    component: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_code": self.code,
            "failure_kind": self.kind.value,
            "classification_source": self.classification_source.value,
            "retryable": self.retryable,
            "message": self.message,
            "component": self.component,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "FailureFact":
        """Restore the canonical failure fact from a Runtime projection."""
        return cls(
            code=str(value.get("error_code", value.get("code", FailureCode.UNKNOWN.value))),
            kind=FailureKind(str(value.get("failure_kind", FailureKind.ACTION.value))),
            classification_source=ClassificationSource(
                str(value.get("classification_source", ClassificationSource.STRUCTURED.value))
            ),
            retryable=bool(value.get("retryable", False)),
            message=str(value.get("message", "")),
            component=str(value.get("component", "")),
        )


def failure_fact(
    code: str,
    *,
    message: str = "",
    component: str = "",
    classification_source: ClassificationSource = ClassificationSource.STRUCTURED,
    retryable: bool | None = None,
) -> FailureFact:
    """Create a fact from an explicit code without parsing prose.

    Unknown codes are action observations by default. Callers that only have
    an exception string must explicitly mark the result as
    ``LEGACY_FALLBACK``; this keeps heuristic usage measurable.
    """

    normalized = str(code or FailureCode.UNKNOWN.value).upper()
    kind = (
        FailureKind.STRUCTURAL
        if normalized in _STRUCTURAL_CODES
        else FailureKind.ACTION
    )
    if retryable is None:
        retryable = kind is FailureKind.ACTION and normalized not in {
            FailureCode.EXECUTION_ENVIRONMENT_UNAVAILABLE.value,
            FailureCode.UNKNOWN_TOOL.value,
            FailureCode.BINARY_FILE.value,
            FailureCode.FILE_NOT_FOUND.value,
            FailureCode.ACTION_VERIFICATION_FAILED.value,
            FailureCode.FILE_OPERATION_UNVERIFIED.value,
        }
    return FailureFact(
        code=normalized,
        kind=kind,
        classification_source=classification_source,
        retryable=bool(retryable),
        message=str(message or ""),
        component=str(component or ""),
    )


__all__ = [
    "ClassificationSource",
    "FailureCode",
    "FailureFact",
    "FailureKind",
    "failure_fact",
]
