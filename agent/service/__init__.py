"""v2.3C AgentService contract surface.

Only DTOs, stable errors, and deterministic event validation live here in
v2.3C-1.  The concrete service implementation is intentionally deferred to
v2.3C-2.
"""

from .contracts import (
    AgentService,
    ArtifactSummary,
    ArtifactView,
    EventStreamRequest,
    EventType,
    FailureSummary,
    ResumeAction,
    ResumeDisposition,
    ResumeRunRequest,
    ResumeSummary,
    RunEvent,
    RunHandle,
    RunLookupRequest,
    RunSnapshot,
    RunStatus,
    StartRunRequest,
)
from .errors import AgentServiceError, ServiceErrorCode
from .events import EventOrderingOracle

__all__ = [
    "AgentService",
    "AgentServiceError",
    "ArtifactSummary",
    "ArtifactView",
    "EventOrderingOracle",
    "EventStreamRequest",
    "EventType",
    "FailureSummary",
    "ResumeAction",
    "ResumeDisposition",
    "ResumeRunRequest",
    "ResumeSummary",
    "RunEvent",
    "RunHandle",
    "RunLookupRequest",
    "RunSnapshot",
    "RunStatus",
    "ServiceErrorCode",
    "StartRunRequest",
]
