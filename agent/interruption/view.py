"""Read-only Runtime projection of durable interruption intent.

``CancellationView`` deliberately cannot create, advance, or finalize an
intent.  Runtime components use it only to decide whether a safe boundary may
start more work.  Durable lifecycle convergence remains owned by
``CancellationCoordinator`` at the Service launcher boundary.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Awaitable, Iterator, TypeVar

from .contracts import (
    AtomicRegion,
    CancellationSafetyClass,
    SafeCancellationBoundary,
)
from .policy import can_observe_interruption
from .store import CancellationStore, DurableInterruptionRecord


@dataclass(frozen=True)
class InterruptionObservation:
    """One safe-boundary observation of a durable interruption intent."""

    record: DurableInterruptionRecord
    boundary: SafeCancellationBoundary
    safety_class: CancellationSafetyClass


class RunInterruptionRequested(BaseException):
    """Cooperative control signal raised only at a declared safe boundary."""

    def __init__(self, observation: InterruptionObservation) -> None:
        self.observation = observation
        intent = observation.record.intent
        super().__init__(
            f"Run interruption requested: {intent.reason.value} "
            f"at {observation.boundary.value}"
        )
        self.execution_evidence: dict[str, Any] = {}


class CancellationView:
    """Identity-scoped, read-only view over the durable interruption Store."""

    def __init__(
        self,
        store: CancellationStore,
        *,
        tenant_id: str,
        session_id: str,
        run_id: str,
    ) -> None:
        self._store = store
        self.tenant_id = str(tenant_id).strip()
        self.session_id = str(session_id).strip()
        self.run_id = str(run_id).strip()
        if not self.tenant_id or not self.session_id or not self.run_id:
            raise ValueError("CancellationView identity must be complete")

    def current(self) -> DurableInterruptionRecord | None:
        """Return the current durable intent without mutating its lifecycle."""

        return self._store.get_interruption(
            self.tenant_id,
            self.run_id,
            session_id=self.session_id,
        )

    def observe_at(
        self,
        boundary: SafeCancellationBoundary | str,
        safety_class: CancellationSafetyClass | str,
        *,
        atomic_region: AtomicRegion | str | None = None,
    ) -> InterruptionObservation | None:
        """Project an intent only when the declared boundary is interruptible."""

        point = SafeCancellationBoundary(boundary)
        safety = CancellationSafetyClass(safety_class)
        if not can_observe_interruption(
            point,
            safety,
            atomic_region=atomic_region,
        ):
            return None
        record = self.current()
        if record is None:
            return None
        return InterruptionObservation(record, point, safety)

    def raise_if_requested(
        self,
        boundary: SafeCancellationBoundary | str,
        safety_class: CancellationSafetyClass | str,
        *,
        atomic_region: AtomicRegion | str | None = None,
    ) -> None:
        observation = self.observe_at(
            boundary,
            safety_class,
            atomic_region=atomic_region,
        )
        if observation is not None:
            raise RunInterruptionRequested(observation)


_CURRENT_CANCELLATION_VIEW: ContextVar[CancellationView | None] = ContextVar(
    "tsagent_cancellation_view",
    default=None,
)


@contextmanager
def cancellation_scope(view: CancellationView | None) -> Iterator[None]:
    """Bind one Run's view to provider calls in this async context."""

    token = _CURRENT_CANCELLATION_VIEW.set(view)
    try:
        yield
    finally:
        _CURRENT_CANCELLATION_VIEW.reset(token)


def current_cancellation_view() -> CancellationView | None:
    return _CURRENT_CANCELLATION_VIEW.get()


_T = TypeVar("_T")


async def await_interruptibly(
    awaitable: Awaitable[_T],
    *,
    view: CancellationView | None = None,
    timeout: float | None = None,
    poll_interval: float = 0.05,
    abort_timeout: float = 0.25,
) -> _T:
    """Await an INTERRUPTIBLE operation while polling durable intent."""

    active_view = view if view is not None else current_cancellation_view()
    operation_awaitable: Awaitable[_T] = awaitable
    if timeout is not None:
        operation_awaitable = asyncio.wait_for(awaitable, timeout=timeout)
    if active_view is None:
        return await operation_awaitable

    operation = asyncio.ensure_future(operation_awaitable)
    try:
        active_view.raise_if_requested(
            SafeCancellationBoundary.DURING_INTERRUPTIBLE_WAIT,
            CancellationSafetyClass.INTERRUPTIBLE,
        )
        while True:
            done, _ = await asyncio.wait(
                {operation},
                timeout=max(float(poll_interval), 0.001),
            )
            if operation in done:
                return await operation
            observation = active_view.observe_at(
                SafeCancellationBoundary.DURING_INTERRUPTIBLE_WAIT,
                CancellationSafetyClass.INTERRUPTIBLE,
            )
            if observation is None:
                continue
            operation.cancel()
            try:
                await asyncio.wait_for(operation, timeout=max(abort_timeout, 0.001))
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
            raise RunInterruptionRequested(observation)
    except BaseException:
        if not operation.done():
            operation.cancel()
        raise


__all__ = [
    "CancellationView",
    "InterruptionObservation",
    "RunInterruptionRequested",
    "await_interruptibly",
    "cancellation_scope",
    "current_cancellation_view",
]
