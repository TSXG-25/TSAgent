"""Subtask delegation: spawn_subtask creates child Task+Run and returns output."""
from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace

import tests.test_ha_r1_harness as harness
from agent.runtime_store.view import DurableRuntimeStoreView
from agent.service.contracts import ListTasksRequest, StartRunRequest


def test_spawn_subtask_creates_child_tree_and_returns_result() -> None:
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service, store = harness.service_at(Path(tmp))
            try:
                # A still-running parent: initialize the head directly and
                # hold a live writer view, the same shape the loop sees.
                parent_head = store.initialize_run(
                    "ha-tenant", "sess", "parent-run",
                    request_id="req-parent", task_id="task-parent",
                )
                store.create_task_head("ha-tenant", "sess", "task-parent")
                view = DurableRuntimeStoreView(
                    store, tenant_id="ha-tenant", session_id="sess",
                    run_id="parent-run", request_id="req-parent",
                    writer_id="test-writer",
                )
                run_context = SimpleNamespace(
                    run_id="parent-run",
                    user_id="ha-user",
                    durable_store_view=view,
                    workspace=None,
                )

                text = await service.spawn_subtask(
                    run_context,
                    request_text="child research task",
                    target_planet="mercury",
                    context_refs=("run:parent-run", "task:task-parent"),
                )
                assert isinstance(text, str) and text

                tasks = await service.list_tasks(ListTasksRequest(
                    tenant_id="ha-tenant", user_id="ha-user",
                    session_id="sess", request_id="req-lt",
                ))
                children = [t for t in tasks if t.parent_task_id == "task-parent"]
                assert len(children) == 1
                assert children[0].origin_planet == "mercury"
                assert children[0].run_ids  # child run attached

                events = store.read_events(
                    "ha-tenant", "parent-run", session_id="sess",
                    after_sequence=0,
                )
                types = [e.event_type for e in events]
                assert "task.delegated" in types
                assert "task.result" in types
                delegated = next(
                    e for e in events if e.event_type == "task.delegated"
                )
                payload = json.loads(delegated.payload_json)
                assert payload["context_refs"] == ["run:parent-run", "task:task-parent"]
                assert payload["child_task_id"] == children[0].task_id

                # Unresolvable refs are rejected before any delegation.
                import pytest

                from agent.service.errors import AgentServiceError

                with pytest.raises(AgentServiceError):
                    await service.spawn_subtask(
                        run_context,
                        request_text="bad ref",
                        context_refs=("run:no-such-run",),
                    )
            finally:
                await service.close()

    asyncio.run(scenario())
