"""Desktop-2 JSONL sidecar for the public AgentService boundary."""

__all__ = ["SidecarDispatcher"]


def __getattr__(name: str):
    if name != "SidecarDispatcher":
        raise AttributeError(name)
    from .dispatcher import SidecarDispatcher

    globals()[name] = SidecarDispatcher
    return SidecarDispatcher
