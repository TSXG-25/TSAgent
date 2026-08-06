"""Runtime diagnostics for cross-module contract violations.

The runtime may degrade in production, but a missing boundary method or field
must remain observable as a structured ``FailureEvent``. Tests can opt into
strict mode with ``TSAGENT_STRICT_CONTRACTS=1`` and receive a direct
``ContractIntegrationError``.
"""
from __future__ import annotations

import logging
import os
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque, List, Optional

from agent.event_bus import EventBus
from agent.compat.event_bus import get_legacy_event_bus

logger = logging.getLogger(__name__)

STRICT_CONTRACTS_ENV = "TSAGENT_STRICT_CONTRACTS"
_EVENTS: Deque[object] = deque(maxlen=100)


class ContractIntegrationError(RuntimeError):
    """A required cross-module contract could not be wired or projected."""


class RunDiagnosticsSink(list[Any]):
    """Run-owned diagnostic sink with explicit identity and lifecycle.

    It intentionally keeps the list-compatible surface used by existing
    contract handlers while preventing a closed Run from accepting new
    diagnostics. The sink never falls back to the process-global ``_EVENTS``.
    """

    def __init__(self, *, scope_id: str) -> None:
        super().__init__()
        self.scope_id = scope_id
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def append(self, event: Any) -> None:
        if self._closed:
            raise RuntimeError(f"diagnostics sink is closed: {self.scope_id}")
        super().append(event)

    def close(self) -> None:
        self._closed = True


def strict_contracts_enabled() -> bool:
    value = os.getenv(STRICT_CONTRACTS_ENV, "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def record_contract_violation(
    *,
    boundary: str,
    operation: str,
    expected: str,
    error: Exception,
    event_bus_instance: Optional[EventBus] = None,
    diagnostics: Optional[list[Any]] = None,
):
    """Create and publish a structured FailureEvent for a contract failure."""
    # Import lazily so the normal runtime path does not import evaluation
    # modules until a diagnostic is actually needed.
    from evaluation.benchmark.failboard_v2 import Evidence, FailureEvent

    actual = f"{type(error).__name__}: {error}"
    event = FailureEvent(
        benchmark="runtime",
        scenario="cross_module_contract",
        layer=boundary,
        dimension=operation,
        failure=actual[:500],
        evidence=[Evidence(
            source=boundary,
            location=operation,
            expected=expected,
            actual=actual[:500],
        )],
        symptom="contract_violation",
        detected_at=datetime.now(timezone.utc).isoformat(),
    )
    if diagnostics is not None:
        diagnostics.append(event)
    else:
        _EVENTS.append(event)
    target_bus = event_bus_instance or get_legacy_event_bus()
    try:
        target_bus.emit("failure_event", event)
    except Exception:  # diagnostics must not hide the original contract issue
        logger.exception("FailureEvent subscriber failed for %s", event.id)
    logger.error(
        "Contract violation at %s.%s: %s",
        boundary,
        operation,
        actual[:500],
    )
    return event


def handle_contract_violation(
    *,
    boundary: str,
    operation: str,
    expected: str,
    error: Exception,
    event_bus_instance: Optional[EventBus] = None,
    diagnostics: Optional[list[Any]] = None,
):
    """Record a violation, then fail visibly in strict mode."""
    event = record_contract_violation(
        boundary=boundary,
        operation=operation,
        expected=expected,
        error=error,
        event_bus_instance=event_bus_instance,
        diagnostics=diagnostics,
    )
    if strict_contracts_enabled():
        raise ContractIntegrationError(
            f"{boundary}.{operation} contract violation: {error}"
        ) from error
    return event


def get_contract_violations() -> List[object]:
    return list(_EVENTS)


def clear_contract_violations() -> None:
    _EVENTS.clear()


__all__ = [
    "ContractIntegrationError",
    "RunDiagnosticsSink",
    "strict_contracts_enabled",
    "record_contract_violation",
    "handle_contract_violation",
    "get_contract_violations",
    "clear_contract_violations",
]
