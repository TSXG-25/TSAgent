"""v2.2C Run-level Workflow resume boundary."""

from .contracts import (
    ArtifactRequirement,
    RunArtifactFact,
    RunResumeDisposition,
    RunResumeIndex,
    RunResumeReasonCode,
    RunResumeRequest,
    RunWorkflowStatus,
    WorkflowDependency,
    WorkflowSummary,
)
from .resolver import RunResumeDecision, RunResumeResolver
from .codec import (
    RunResumeCodecError,
    deserialize_run_index,
    run_index_digest,
    serialize_run_index,
)
from .store import (
    InMemoryRunResumeStore,
    JsonRunResumeStore,
    RunResumeActivationError,
    RunResumeStore,
    RunResumeStoreError,
)

__all__ = [
    "ArtifactRequirement",
    "InMemoryRunResumeStore",
    "JsonRunResumeStore",
    "RunResumeCodecError",
    "RunResumeCoordinator",
    "RunResumeActivationError",
    "RunArtifactFact",
    "RunResumeDecision",
    "RunResumeDisposition",
    "RunResumeIndex",
    "RunResumeReasonCode",
    "RunResumeRequest",
    "RunResumeExecution",
    "RunResumeResolver",
    "RunResumeStore",
    "RunResumeStoreError",
    "RunWorkflowStatus",
    "WorkflowDependency",
    "WorkflowSummary",
    "deserialize_run_index",
    "run_index_digest",
    "serialize_run_index",
]


def __getattr__(name: str):
    """Load the coordinator lazily to keep codec/store imports acyclic.

    The durable SQLite infrastructure imports the RunResume codec during
    bootstrap.  Eagerly importing the coordinator from this package would
    make that low-level path import the SQLite view back while the connection
    class is still being defined.
    """

    if name in {"RunResumeCoordinator", "RunResumeExecution"}:
        from .coordinator import RunResumeCoordinator, RunResumeExecution

        return {
            "RunResumeCoordinator": RunResumeCoordinator,
            "RunResumeExecution": RunResumeExecution,
        }[name]
    raise AttributeError(name)
