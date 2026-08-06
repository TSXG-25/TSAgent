"""Deterministic event ordering and replay rules for v2.3C-1."""

from __future__ import annotations

from collections.abc import Sequence

from .contracts import RunEvent
from .errors import AgentServiceError, ServiceErrorCode


class EventOrderingOracle:
    """Pure validator for persisted Run event histories.

    It validates facts only.  It does not subscribe to an EventBus, execute a
    workflow, or make a disconnected client affect Runtime execution.
    """

    @staticmethod
    def validate(
        events: Sequence[RunEvent],
        *,
        tenant_id: str | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        require_start: bool = True,
        require_terminal: bool = False,
    ) -> tuple[RunEvent, ...]:
        ordered = tuple(events)
        if not ordered:
            if require_terminal:
                raise AgentServiceError(
                    ServiceErrorCode.EVENT_SEQUENCE_INVALID,
                    "a terminal event is required",
                )
            return ordered

        if require_start and ordered[0].sequence_number != 1:
            raise AgentServiceError(
                ServiceErrorCode.EVENT_SEQUENCE_INVALID,
                "event history must start at sequence 1",
            )

        expected_sequence = ordered[0].sequence_number
        previous_revision = -1
        seen_ids: set[str] = set()
        terminal_seen = False
        for event in ordered:
            if event.event_id in seen_ids:
                raise AgentServiceError(
                    ServiceErrorCode.EVENT_SEQUENCE_INVALID,
                    "event_id must be unique within a Run",
                    details={"event_id": event.event_id},
                )
            seen_ids.add(event.event_id)
            if event.sequence_number != expected_sequence:
                raise AgentServiceError(
                    ServiceErrorCode.EVENT_SEQUENCE_INVALID,
                    "event sequence must be contiguous and increasing",
                    details={
                        "expected_sequence": expected_sequence,
                        "actual_sequence": event.sequence_number,
                    },
                )
            expected_sequence += 1
            if tenant_id is not None and event.tenant_id != tenant_id:
                raise AgentServiceError(
                    ServiceErrorCode.IDENTITY_MISMATCH,
                    "event tenant does not match the requested scope",
                )
            if session_id is not None and event.session_id != session_id:
                raise AgentServiceError(
                    ServiceErrorCode.IDENTITY_MISMATCH,
                    "event session does not match the requested scope",
                )
            if run_id is not None and event.run_id != run_id:
                raise AgentServiceError(
                    ServiceErrorCode.IDENTITY_MISMATCH,
                    "event Run does not match the requested scope",
                )
            if event.run_revision < previous_revision:
                raise AgentServiceError(
                    ServiceErrorCode.EVENT_SEQUENCE_INVALID,
                    "run_revision must not move backwards",
                )
            previous_revision = event.run_revision
            if terminal_seen:
                raise AgentServiceError(
                    ServiceErrorCode.EVENT_SEQUENCE_INVALID,
                    "no event may follow a terminal Run event",
                )
            if event.is_terminal:
                terminal_seen = True

        if require_terminal and not terminal_seen:
            raise AgentServiceError(
                ServiceErrorCode.EVENT_SEQUENCE_INVALID,
                "event history must end with a terminal Run event",
            )
        return ordered

    @classmethod
    def replay_after(
        cls,
        events: Sequence[RunEvent],
        after_sequence: int,
        *,
        tenant_id: str | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
    ) -> tuple[RunEvent, ...]:
        if isinstance(after_sequence, bool) or after_sequence < 0:
            raise AgentServiceError(
                ServiceErrorCode.EVENT_REPLAY_UNAVAILABLE,
                "after_sequence must be a non-negative integer",
            )
        ordered = cls.validate(
            events,
            tenant_id=tenant_id,
            session_id=session_id,
            run_id=run_id,
            require_start=True,
        )
        return tuple(
            event for event in ordered if event.sequence_number > after_sequence
        )

    @classmethod
    def require_terminal(
        cls,
        events: Sequence[RunEvent],
        *,
        tenant_id: str | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
    ) -> tuple[RunEvent, ...]:
        return cls.validate(
            events,
            tenant_id=tenant_id,
            session_id=session_id,
            run_id=run_id,
            require_terminal=True,
        )


__all__ = ["EventOrderingOracle"]
