_SERVICES = {
    "MemoryService": ".memory_service",
    "RepositoryService": ".repository_service",
    "ArtifactService": ".artifact_service",
    "ToolService": ".tool_service",
}


def __getattr__(name):
    if name not in _SERVICES:
        raise AttributeError(name)

    from importlib import import_module

    module = import_module(_SERVICES[name], __name__)
    service = getattr(module, name)
    globals()[name] = service
    return service


__all__ = list(_SERVICES)
