"""Compatibility access to the legacy process-global EventBus."""
from __future__ import annotations

import warnings

from agent.event_bus import EventBus, event_bus

_access_count = 0


def get_legacy_event_bus() -> EventBus:
    global _access_count
    _access_count += 1
    warnings.warn(
        "legacy global EventBus access; pass a scoped Run EventBus",
        DeprecationWarning,
        stacklevel=2,
    )
    return event_bus


def legacy_event_bus_access_count() -> int:
    return _access_count


__all__ = ["get_legacy_event_bus", "legacy_event_bus_access_count"]
