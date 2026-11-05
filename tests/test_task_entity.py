"""Task entity: durable task_heads, run linkage, service projection."""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from agent.runtime_store.sqlite import SqliteRuntimeStore
from agent.service.contracts import (
    CreateTaskRequest,
    ListTasksRequest,
    TaskLookupRequest,
)
from agent.service.errors import AgentServiceError


def test_task_store_crud_and_run_linkage() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        store = SqliteRuntimeStore.open(Path(tmp) / "rt.db")
        try:
            parent = store.create_task_head(
                "t", "s", "task-parent", origin_planet="sun"
            )
            child = store.create_task_head(
                "t", "s", "task-child", parent_task_id="task-parent",
                owner_profile_id="mercury",
            )
            assert child.parent_task_id == "task-parent"
            # idempotent re-create returns existing head
            again = store.create_task_head("t", "s", "task-parent")
            assert again.task_id == parent.task_id

            head = store.initialize_run(
                "t", "s", "run-1", request_id="r1", task_id="task-child"
            )
            assert head.task_id == "task-child"
            runs = store.list_run_heads("t", "s", task_id="task-child")
            assert [r.run_id for r in runs] == ["run-1"]

            tasks = store.list_task_heads("t", "s")
            assert [x.task_id for x in tasks] == ["task-parent", "task-child"]
        finally:
            store.close()


def test_service_task_projection() -> None:
    import tests.test_ha_r1_harness as harness

    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service, store = harness.service_at(Path(tmp))
            try:
                handle = await service.create_task(CreateTaskRequest(
                    tenant_id="ha-tenant", user_id="ha-user",
                    session_id="sess", request_id="req-task-1",
                    origin_planet="sun",
                ))
                assert handle.task_id.startswith("task-")
                assert handle.origin_planet == "sun"

                run = await service.start_run(harness.StartRunRequest(
                    tenant_id="ha-tenant", user_id="ha-user",
                    session_id="sess",
                    request_id="req-run-1",
                    request_text="ping",
                    task_id=handle.task_id,
                ))
                assert run.task_id == handle.task_id

                tasks = await service.list_tasks(ListTasksRequest(
                    tenant_id="ha-tenant", user_id="ha-user",
                    session_id="sess", request_id="req-list-1",
                ))
                snap = next(t for t in tasks if t.task_id == handle.task_id)
                assert run.run_id in snap.run_ids

                one = await service.get_task(TaskLookupRequest(
                    tenant_id="ha-tenant", user_id="ha-user",
                    session_id="sess", request_id="req-get-1",
                    task_id=handle.task_id,
                ))
                assert one.task_id == handle.task_id
            finally:
                await service.close()

    asyncio.run(scenario())


def test_start_run_rejects_unknown_task() -> None:
    import tests.test_ha_r1_harness as harness

    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service, _store = harness.service_at(Path(tmp))
            try:
                try:
                    await service.start_run(harness.StartRunRequest(
                        tenant_id="ha-tenant", user_id="ha-user",
                        session_id="sess",
                        request_id="req-run-bad",
                        request_text="ping",
                        task_id="task-missing",
                    ))
                except AgentServiceError as error:
                    assert str(error.code) == "ServiceErrorCode.INVALID_REQUEST" or "INVALID" in str(error.code)
                else:
                    raise AssertionError("start_run accepted an unknown task_id")
            finally:
                await service.close()

    asyncio.run(scenario())


def test_start_run_auto_creates_task() -> None:
    import tests.test_ha_r1_harness as harness

    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service, _store = harness.service_at(Path(tmp))
            try:
                run = await service.start_run(harness.StartRunRequest(
                    tenant_id="ha-tenant", user_id="ha-user",
                    session_id="sess",
                    request_id="req-run-auto",
                    request_text="ping",
                ))
                assert run.task_id.startswith("task-")
            finally:
                await service.close()

    asyncio.run(scenario())
