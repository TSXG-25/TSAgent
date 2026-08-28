"""Memory lifecycle boundary.

``MemoryRuntime`` is the single scoped cleanup API used by session owners and
benchmarks.  It deliberately operates on one user/session namespace; there is
no global "clear everything" operation here.

The layers have different lifetimes:

* conversation=True clears session messages, short-term history, semantic
  summaries, resolution memory, and the calling Session's Conversation Runtime
  snapshot;
* facts=True additionally clears extracted user facts;
* execution artifacts are owned by ``SessionRuntime`` and are not handled here.
"""
from __future__ import annotations

from dataclasses import dataclass


def _validate_namespace(user_id: str) -> str:
    """Reject path-like namespaces before any per-user deletion occurs."""
    value = str(user_id or "").strip()
    if not value or value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError("user_id must be a non-empty path-safe namespace")
    return value


@dataclass(frozen=True)
class MemoryResetReport:
    """Auditable result of a scoped memory reset."""

    user_id: str
    conversation: bool
    facts: bool


class MemoryRuntime:
    """Scoped lifecycle API for the memory layers."""

    @classmethod
    def reset(
        cls,
        user_id: str,
        *,
        conversation: bool = True,
        facts: bool = False,
        conversation_tracker=None,
    ) -> MemoryResetReport:
        """Reset memory for exactly one user/session namespace.

        ``conversation`` is intentionally broader than the in-process message
        buffer: persistent short-term/summary/resolution data can otherwise
        leak into a repeated benchmark case. ``conversation_tracker`` is an
        explicit Session-owned tracker; omitted callers use the legacy global
        tracker for compatibility. Facts are independent and are opt-in
        because they normally represent user-level persistence.
        """
        namespace = _validate_namespace(user_id)

        if conversation:
            from agent.memory.session import clear_session
            from agent.memory.short_term import clear_history
            from agent.memory.long_term import clear_summaries
            from agent.memory.resolution import clear_resolutions
            tracker = conversation_tracker
            if tracker is None:
                # The public legacy reset API predates SessionContext. Keep
                # its observable reset semantics for unscoped callers, while
                # the production SessionRuntime path always passes its owned
                # tracker explicitly (see session_runtime.py).
                from agent.conversation import conversation_tracker
                tracker = conversation_tracker

            clear_session(namespace)
            clear_history(namespace)
            clear_summaries(namespace)
            clear_resolutions(namespace)
            tracker.reset(namespace)

        if facts:
            from agent.memory.long_term import clear_facts

            clear_facts(namespace)

        return MemoryResetReport(
            user_id=namespace,
            conversation=conversation,
            facts=facts,
        )


__all__ = ["MemoryRuntime", "MemoryResetReport"]
