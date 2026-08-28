"""Production failure contracts shared by Runtime and evaluation adapters."""

from .contracts import (
    Evidence,
    FailureEvent,
    SYMPTOM_MAP,
    VALID_SYMPTOMS,
)
from .taxonomy import (
    ClassificationSource,
    FailureCode,
    FailureFact,
    FailureKind,
    failure_fact,
)

__all__ = [
    "Evidence",
    "FailureEvent",
    "SYMPTOM_MAP",
    "VALID_SYMPTOMS",
    "ClassificationSource",
    "FailureCode",
    "FailureFact",
    "FailureKind",
    "failure_fact",
    "FailurePolicy",
    "RecoveryDirective",
]


def __getattr__(name: str):
    """Keep Reflection/Decision out of the ordinary action import path."""
    if name in {"FailurePolicy", "RecoveryDirective"}:
        from .policy import FailurePolicy, RecoveryDirective

        return {"FailurePolicy": FailurePolicy, "RecoveryDirective": RecoveryDirective}[name]
    raise AttributeError(name)
