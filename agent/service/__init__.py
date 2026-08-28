"""v2.3C AgentService contract surface.

The package exports the same public DTO/error names as before, but resolves
each concrete module only when a caller asks for that symbol.  In particular,
importing ``agent.service.local_sidecar`` must not initialize the full Store,
Runtime, and Context graph before the sidecar can answer health.
"""

from importlib import import_module


_EXPORTS = {
    "CancelRunRequest": ("agent.interruption", "CancelRunRequest"),
    "AgentServiceContract": ("agent.service.contracts", "AgentService"),
    "AgentService": ("agent.service.service", "AgentService"),
    "AgentServiceError": ("agent.service.errors", "AgentServiceError"),
    "ServiceErrorCode": ("agent.service.errors", "ServiceErrorCode"),
    "ArtifactSummary": ("agent.service.contracts", "ArtifactSummary"),
    "ArtifactView": ("agent.service.contracts", "ArtifactView"),
    "EventStreamRequest": ("agent.service.contracts", "EventStreamRequest"),
    "EventType": ("agent.service.contracts", "EventType"),
    "FailureSummary": ("agent.service.contracts", "FailureSummary"),
    "ResumeAction": ("agent.service.contracts", "ResumeAction"),
    "ResumeDisposition": ("agent.service.contracts", "ResumeDisposition"),
    "ResumeRunRequest": ("agent.service.contracts", "ResumeRunRequest"),
    "ResumeSummary": ("agent.service.contracts", "ResumeSummary"),
    "RunEvent": ("agent.service.contracts", "RunEvent"),
    "RunHandle": ("agent.service.contracts", "RunHandle"),
    "RunLookupRequest": ("agent.service.contracts", "RunLookupRequest"),
    "RunOutput": ("agent.service.contracts", "RunOutput"),
    "RunSnapshot": ("agent.service.contracts", "RunSnapshot"),
    "RunStatus": ("agent.service.contracts", "RunStatus"),
    "StartRunRequest": ("agent.service.contracts", "StartRunRequest"),
    "ServiceContextFactory": ("agent.service.context_factory", "ServiceContextFactory"),
    "EventRepository": ("agent.service.event_repository", "EventRepository"),
    "EmptyEventRepository": ("agent.service.event_repository", "EmptyEventRepository"),
    "InMemoryEventRepository": ("agent.service.event_repository", "InMemoryEventRepository"),
    "PendingRunEvent": ("agent.service.event_repository", "PendingRunEvent"),
    "SqliteEventRepository": ("agent.service.event_repository", "SqliteEventRepository"),
    "ExecutionLauncher": ("agent.service.execution_launcher", "ExecutionLauncher"),
    "EventOrderingOracle": ("agent.service.events", "EventOrderingOracle"),
    "create_default_agent_service": ("agent.service.factory", "create_default_agent_service"),
}

# Backward-compatible aliases that were present in the old eager package
# surface.  They are aliases, not a second implementation.
_EXPORTS.update({
    "GetRunRequest": ("agent.service.contracts", "RunLookupRequest"),
    "ListArtifactsRequest": ("agent.service.contracts", "RunLookupRequest"),
})

__all__ = sorted(_EXPORTS)


def __getattr__(name: str):
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attribute_name = target
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value
