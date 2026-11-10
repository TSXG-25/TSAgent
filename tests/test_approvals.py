"""Approval protocol: durable records, atomic decisions, idempotent replay."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from agent.runtime_store.sqlite import SqliteRuntimeStore
from agent.runtime_store.errors import DurableStoreError, StoreErrorCode
from agent.service.contracts import DecideApprovalRequest, ListApprovalsRequest


def _store(tmp: str) -> SqliteRuntimeStore:
    store = SqliteRuntimeStore.open(Path(tmp) / "rt.db")
    store.initialize_run("t", "s", "run-1", request_id="r1")
    return store


def test_approval_lifecycle_and_idempotent_decide() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        try:
            record = store.create_approval(
                "t", "s", "run-1", "ap-1",
                action="edit_file", scope="src/a.py", risk_level="R1",
            )
            assert record.status == "PENDING"

            decided = store.decide_approval(
                "t", "run-1", "ap-1", decision="APPROVED", decided_by="user-1",
            )
            assert decided.status == "APPROVED"
            assert decided.decided_by == "user-1"

            # replay same decision → same record (idempotent)
            replay = store.decide_approval(
                "t", "run-1", "ap-1", decision="APPROVED", decided_by="user-1",
            )
            assert replay.status == "APPROVED"

            # conflicting decision → store conflict
            with pytest.raises(DurableStoreError):
                store.decide_approval(
                    "t", "run-1", "ap-1", decision="DENIED", decided_by="user-2",
                )

            pending = store.list_approvals("t", "s", status="PENDING")
            assert pending == []
            all_ = store.list_approvals("t", "s", run_id="run-1")
            assert [a.approval_id for a in all_] == ["ap-1"]
        finally:
            store.close()


def test_create_approval_rejects_same_key_for_a_different_action() -> None:
    """Same-key replay must re-bind only to an identical action+params —
    a different action under one approval_id is an idempotency conflict."""
    with tempfile.TemporaryDirectory() as tmp:
        store = _store(tmp)
        try:
            store.create_approval(
                "t", "s", "run-1", "ap-1",
                action="shell", params_json='{"command":"ls"}',
                risk_level="R2",
            )
            replay = store.create_approval(
                "t", "s", "run-1", "ap-1",
                action="shell", params_json='{"command": "ls"}',
                risk_level="R2",
            )
            assert replay.status == "PENDING"
            for action, params_json in (
                ("shell", '{"command":"echo other"}'),
                ("write_file", '{"command":"ls"}'),
            ):
                with pytest.raises(DurableStoreError) as conflict:
                    store.create_approval(
                        "t", "s", "run-1", "ap-1",
                        action=action, params_json=params_json,
                    )
                assert conflict.value.code is StoreErrorCode.IDEMPOTENCY_CONFLICT
        finally:
            store.close()


def test_decide_releases_only_the_matching_scope() -> None:
    """Same approval_id under two tenants must not cross-wake waiters."""
    import tests.test_ha_r1_harness as harness

    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service, store = harness.service_at(Path(tmp))
            try:
                store.initialize_run(
                    "t1", "s1", "run-1", request_id="r1", run_status="RUNNING"
                )
                store.initialize_run(
                    "t2", "s2", "run-2", request_id="r2", run_status="RUNNING"
                )
                store.create_approval("t1", "s1", "run-1", "ap-1", action="shell")
                store.create_approval("t2", "s2", "run-2", "ap-1", action="shell")
                first, second = asyncio.Event(), asyncio.Event()
                service._approval_waiters[("t1", "run-1", "ap-1")] = first
                service._approval_waiters[("t2", "run-2", "ap-1")] = second
                summary = await service.decide_approval(DecideApprovalRequest(
                    tenant_id="t1", user_id="u1", session_id="s1",
                    run_id="run-1", approval_id="ap-1",
                    decision="approve", request_id="dec-1",
                ))
                assert summary.status == "APPROVED"
                assert first.is_set()
                assert not second.is_set()
            finally:
                await service.close()

    asyncio.run(scenario())


def test_peer_service_decision_wakes_parked_waiter() -> None:
    """A decision committed by another Service sharing the SQLite file
    releases the parked gate — waiters observe the store, not the process."""
    from types import SimpleNamespace

    import tests.test_ha_r1_harness as harness
    from agent.runtime_store import DurableRuntimeStoreView

    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service_a, store_a = harness.service_at(Path(tmp))
            service_b, _store_b = harness.service_at(Path(tmp))
            try:
                store_a.initialize_run(
                    "t", "s", "run-1", request_id="r1", run_status="RUNNING"
                )
                view = DurableRuntimeStoreView(
                    store_a, tenant_id="t", session_id="s", run_id="run-1",
                    request_id="r1", writer_id="w1", ensure_run=False,
                )
                pending = asyncio.create_task(
                    service_a.request_approval(
                        SimpleNamespace(durable_store_view=view),
                        approval_id="ap-1",
                        action="shell",
                        risk_level="R2",
                    )
                )
                for _ in range(100):
                    if store_a.get_approval("t", "run-1", "ap-1") is not None:
                        break
                    await asyncio.sleep(0.05)
                summary = await service_b.decide_approval(DecideApprovalRequest(
                    tenant_id="t", user_id="u", session_id="s",
                    run_id="run-1", approval_id="ap-1",
                    decision="approve", request_id="dec-1",
                ))
                assert summary.status == "APPROVED"
                record = await asyncio.wait_for(pending, timeout=10)
                assert record.status == "APPROVED"
                view.close()
            finally:
                await service_a.close()
                await service_b.close()

    asyncio.run(scenario())


def test_service_decide_releases_waiter() -> None:
    import tests.test_ha_r1_harness as harness

    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service, _store = harness.service_at(Path(tmp))
            try:
                from agent.service.contracts import StartRunRequest
                run = await service.start_run(StartRunRequest(
                    tenant_id="ha-tenant", user_id="ha-user",
                    session_id="sess", request_id="req-1",
                    request_text="ping",
                ))

                # simulate a runtime gate: approval record + waiter registered
                store_ref = service._store
                record = store_ref.create_approval(
                    "ha-tenant", "sess", run.run_id, "ap-9",
                    action="shell_exec", risk_level="R2",
                )
                waiter = asyncio.Event()
                service._approval_waiters[
                    ("ha-tenant", run.run_id, "ap-9")
                ] = waiter

                summary = await service.decide_approval(DecideApprovalRequest(
                    tenant_id="ha-tenant", user_id="ha-user",
                    session_id="sess", run_id=run.run_id,
                    approval_id="ap-9", decision="deny",
                    request_id="req-dec-1",
                ))
                assert summary.status == "DENIED"
                assert summary.decided_by == "ha-user"
                assert waiter.is_set()

                listed = await service.list_approvals(ListApprovalsRequest(
                    tenant_id="ha-tenant", user_id="ha-user",
                    session_id="sess", request_id="req-list-1",
                ))
                assert [a.approval_id for a in listed] == ["ap-9"]
            finally:
                await service.close()

    asyncio.run(scenario())
