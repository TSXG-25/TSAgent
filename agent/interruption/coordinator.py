"""Durable Cancellation Coordinator for v2.3D-2.

This coordinator only converges durable facts.  It does not cancel an asyncio
Task or interrupt execution-layer components; propagation belongs to D3.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable

from .contracts import (
    CancellationIntent,
    CancelRunRequest,
    InterruptionPhase,
    InterruptionReason,
)
from .store import CancellationStore, DurableInterruptionRecord


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class CancellationCoordinator:
    """Persist, observe and terminalize one Run interruption intent."""

    def __init__(
        self,
        store: CancellationStore,
        *,
        clock: Callable[[], str] = _utc_now,
    ) -> None:
        self._store = store
        self._clock = clock

    def request_cancel(self, request: CancelRunRequest) -> DurableInterruptionRecord:
        intent = CancellationIntent(
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            session_id=request.session_id,
            run_id=request.run_id,
            request_id=request.request_id,
            requested_at=self._clock(),
            requested_by=request.requested_by,
            reason=request.reason,
            revision=0,
        )
        return self._store.request_interruption(
            intent,
            request_digest=request.request_digest,
        )

    def request_run_timeout(
        self,
        *,
        tenant_id: str,
        user_id: str,
        session_id: str,
        run_id: str,
        request_id: str,
        requested_by: str,
    ) -> DurableInterruptionRecord:
        intent = CancellationIntent(
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            run_id=run_id,
            request_id=request_id,
            requested_at=self._clock(),
            requested_by=requested_by,
            reason=InterruptionReason.RUN_TIMEOUT,
            revision=0,
        )
        return self._store.request_interruption(
            intent,
            request_digest=intent.intent_digest,
        )

    def observe(
        self,
        *,
        tenant_id: str,
        session_id: str,
        run_id: str,
        request_id: str,
        writer_id: str,
        fence_token: int,
    ) -> DurableInterruptionRecord:
        record = self._store.get_interruption(
            tenant_id,
            run_id,
            session_id=session_id,
        )
        if record is None:
            # Keep the Store's stable error taxonomy for callers.
            return self._store.advance_interruption_phase(
                tenant_id,
                session_id,
                run_id,
                request_id=request_id,
                target_phase=InterruptionPhase.OBSERVED,
                writer_id=writer_id,
                fence_token=fence_token,
            )
        if record.intent.phase is not InterruptionPhase.REQUESTED:
            return record
        return self._store.advance_interruption_phase(
            tenant_id,
            session_id,
            run_id,
            request_id=request_id,
            target_phase=InterruptionPhase.OBSERVED,
            writer_id=writer_id,
            fence_token=fence_token,
        )

    def mark_safe_to_interrupt(
        self,
        *,
        tenant_id: str,
        session_id: str,
        run_id: str,
        request_id: str,
        writer_id: str,
        fence_token: int,
    ) -> DurableInterruptionRecord:
        record = self.observe(
            tenant_id=tenant_id,
            session_id=session_id,
            run_id=run_id,
            request_id=request_id,
            writer_id=writer_id,
            fence_token=fence_token,
        )
        if record.intent.phase is InterruptionPhase.FINALIZED:
            return replace(record, idempotent=True)
        return self._store.finalize_interruption(
            tenant_id,
            session_id,
            run_id,
            request_id=request_id,
            writer_id=writer_id,
            fence_token=fence_token,
        )


__all__ = ["CancellationCoordinator"]
