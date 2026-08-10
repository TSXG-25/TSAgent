"""v2.3C AgentService contract surface.

Only DTOs, stable errors, and deterministic event validation live here in
v2.3C-1.  The concrete service implementation is intentionally deferred to
v2.3C-2.
"""

from agent.interruption import CancelRunRequest

from .contracts import (
    AgentService as AgentServiceProtocol,
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
    RunOutput,
    RunSnapshot,
    RunStatus,
    StartRunRequest,
)
from .errors import AgentServiceError, ServiceErrorCode
from .context_factory import ServiceContextFactory
from .event_repository import (
    EmptyEventRepository,
    EventRepository,
    InMemoryEventRepository,
    PendingRunEvent,
    SqliteEventRepository,
)
from .execution_launcher import ExecutionLauncher
from .events import EventOrderingOracle
from .factory import create_default_agent_service
from .service import AgentService as AgentServiceCore

# Keep the Protocol available under an explicit name while exposing the
# concrete v2.3C-2 implementation as the package-level AgentService.
AgentServiceContract = AgentServiceProtocol
AgentService = AgentServiceCore
GetRunRequest = RunLookupRequest
ListArtifactsRequest = RunLookupRequest

__all__ = [
    "AgentService",
    "AgentServiceContract",
    "AgentServiceError",
    "CancelRunRequest",
    "ArtifactSummary",
    "ArtifactView",
    "EventOrderingOracle",
    "EventRepository",
    "EmptyEventRepository",
    "PendingRunEvent",
    "SqliteEventRepository",
    "EventStreamRequest",
    "EventType",
    "FailureSummary",
    "ExecutionLauncher",
    "create_default_agent_service",
    "GetRunRequest",
    "ListArtifactsRequest",
    "ResumeAction",
    "ResumeDisposition",
    "ResumeRunRequest",
    "ResumeSummary",
    "RunEvent",
    "RunHandle",
    "RunOutput",
    "RunLookupRequest",
    "RunSnapshot",
    "RunStatus",
    "ServiceErrorCode",
    "ServiceContextFactory",
    "InMemoryEventRepository",
    "StartRunRequest",
]
