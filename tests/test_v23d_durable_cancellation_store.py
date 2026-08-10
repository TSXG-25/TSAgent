from __future__ import annotations

from datetime import datetime, timezone

import pytest

from agent.interruption import (
    CancellationIntent,
    InterruptionFailurePoint,
    InterruptionPhase,
    InterruptionReason,
)
from agent.runtime_store import DurableStoreError, SqliteRuntimeStore, StoreErrorCode


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _intent(*, request_id: str = "cancel-1", run_id: str = "run-1") -> CancellationIntent:
    return CancellationIntent(
        tenant_id="tenant-a",
        user_id="user-a",
        session_id="session-a",
        run_id=run_id,
        request_id=request_id,
        requested_at=_timestamp(),
        requested_by="user-a",
        reason=InterruptionReason.USER_CANCEL,
        revision=0,
    )


def _store(tmp_path):
    store = SqliteRuntimeStore.open(tmp_path / "runtime.sqlite")
    store.initialize_run(
        "tenant-a",
        "session-a",
        "run-1",
        "start-1",
        run_status="RUNNING",
    )
    store.acquire_fence(
        "tenant-a",
        "session-a",
        "run-1",
        writer_id="worker-a",
    )
    return store


def test_cancel_request_is_atomic_and_idempotent(tmp_path) -> None:
    store = _store(tmp_path)
    try:
        intent = _intent()
        saved = store.request_interruption(intent, request_digest=intent.intent_digest)
        retry = store.request_interruption(intent, request_digest=intent.intent_digest)
        head = store.get_run_head("tenant-a", "run-1", session_id="session-a")
        events = store.read_events("tenant-a", "run-1", session_id="session-a")

        assert saved.intent.phase is InterruptionPhase.REQUESTED
        assert retry.idempotent is True
        assert retry.updated_revision == saved.updated_revision
        assert head is not None and head.run_status == "CANCELLING"
        assert [event.event_type for event in events] == ["run_cancelling"]
        assert head.current_revision == saved.created_revision
    finally:
        store.close()


def test_same_request_id_with_different_digest_conflicts(tmp_path) -> None:
    store = _store(tmp_path)
    try:
        intent = _intent()
        store.request_interruption(intent, request_digest=intent.intent_digest)
        with pytest.raises(DurableStoreError) as caught:
            store.request_interruption(intent, request_digest="different")
        assert caught.value.code is StoreErrorCode.INTERRUPTION_REQUEST_CONFLICT
    finally:
        store.close()


@pytest.mark.parametrize(
    "failure_point",
    [
        InterruptionFailurePoint.AFTER_INTENT_INSERT,
        InterruptionFailurePoint.AFTER_REVISION_INSERT,
        InterruptionFailurePoint.AFTER_EVENT_APPEND,
        InterruptionFailurePoint.AFTER_HEAD_UPDATE,
        InterruptionFailurePoint.BEFORE_COMMIT,
    ],
)
def test_cancel_request_fault_rolls_back_all_facts(tmp_path, failure_point) -> None:
    store = _store(tmp_path)
    try:
        before = store.get_run_head("tenant-a", "run-1", session_id="session-a")
        with pytest.raises(DurableStoreError) as caught:
            intent = _intent()
            store.request_interruption(
                intent,
                request_digest=intent.intent_digest,
                failure_point=failure_point,
            )
        after = store.get_run_head("tenant-a", "run-1", session_id="session-a")
        assert caught.value.code is StoreErrorCode.INTERRUPTION_INJECTED_FAILURE
        assert after == before
        assert store.get_interruption("tenant-a", "run-1", session_id="session-a") is None
        assert store.read_events("tenant-a", "run-1", session_id="session-a") == ()
    finally:
        store.close()


def test_phase_advance_requires_current_fence_and_is_durable(tmp_path) -> None:
    path = tmp_path / "runtime.sqlite"
    store = _store(tmp_path)
    intent = _intent()
    try:
        store.request_interruption(intent, request_digest=intent.intent_digest)
        with pytest.raises(DurableStoreError) as caught:
            store.advance_interruption_phase(
                "tenant-a",
                "session-a",
                "run-1",
                request_id="cancel-1",
                target_phase=InterruptionPhase.OBSERVED,
                writer_id="stale-worker",
                fence_token=1,
            )
        assert caught.value.code is StoreErrorCode.STALE_WRITER

        observed = store.advance_interruption_phase(
            "tenant-a",
            "session-a",
            "run-1",
            request_id="cancel-1",
            target_phase=InterruptionPhase.OBSERVED,
            writer_id="worker-a",
            fence_token=1,
        )
        assert observed.intent.phase is InterruptionPhase.OBSERVED
    finally:
        store.close()

    reopened = SqliteRuntimeStore.open(path)
    try:
        restored = reopened.get_interruption(
            "tenant-a", "run-1", session_id="session-a"
        )
        assert restored is not None
        assert restored.intent.phase is InterruptionPhase.OBSERVED
        assert reopened.get_run_head("tenant-a", "run-1").run_status == "CANCELLING"
    finally:
        reopened.close()


def test_cross_tenant_intent_lookup_is_fail_closed(tmp_path) -> None:
    store = _store(tmp_path)
    try:
        intent = _intent()
        store.request_interruption(intent, request_digest=intent.intent_digest)
        with pytest.raises(DurableStoreError) as caught:
            store.get_interruption("tenant-b", "run-1", session_id="session-a")
        assert caught.value.code is StoreErrorCode.IDENTITY_MISMATCH
    finally:
        store.close()
