"""Approval protocol: durable records, atomic decisions, idempotent replay."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from agent.runtime_store.sqlite import SqliteRuntimeStore
from agent.runtime_store.errors import DurableStoreError
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
                service._approval_waiters["ap-9"] = waiter

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
