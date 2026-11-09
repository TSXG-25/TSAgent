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


def test_spawn_subtask_child_inherits_workspace_and_refs() -> None:
    """The derived child session has no workspace binding — the child Run's
    start record must carry the parent's resolved root and the refs."""
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service, store = harness.service_at(Path(tmp))
            try:
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
                from agent.event_bus import EventBus
                from agent.services.workspace_service import WorkspaceService
                from agent.service.run_projector import (
                    request_context_refs,
                    request_workspace_root,
                )

                workspace = WorkspaceService.scoped(
                    Path(tmp), event_bus=EventBus(), build_index=False,
                )
                workspace.write_text("note.md", "attached")
                run_context = SimpleNamespace(
                    run_id="parent-run",
                    user_id="ha-user",
                    durable_store_view=view,
                    workspace=workspace,
                )

                await service.spawn_subtask(
                    run_context,
                    request_text="child reads the note",
                    target_planet="mercury",
                    context_refs=("file:note.md",),
                )

                tasks = await service.list_tasks(ListTasksRequest(
                    tenant_id="ha-tenant", user_id="ha-user",
                    session_id="sess", request_id="req-lt",
                ))
                child = next(t for t in tasks if t.parent_task_id == "task-parent")
                child_run_id = child.run_ids[0]
                read = store.read_run_snapshot(
                    "ha-tenant", child_run_id,
                    session_id=f"sess:subtask:{child.task_id}",
                )
                assert request_workspace_root(read) == str(Path(tmp).resolve())
                assert request_context_refs(read) == ("file:note.md",)
            finally:
                await service.close()

    asyncio.run(scenario())


def test_spawn_subtask_marks_child_depth_and_blocks_second_level() -> None:
    """Depth invariant: the spawned child record carries delegation_depth=1
    and a depth-1 context cannot delegate again."""
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service, store = harness.service_at(Path(tmp))
            try:
                store.initialize_run(
                    "ha-tenant", "sess", "parent-run",
                    request_id="req-parent", task_id="task-parent",
                )
                store.create_task_head("ha-tenant", "sess", "task-parent")
                view = DurableRuntimeStoreView(
                    store, tenant_id="ha-tenant", session_id="sess",
                    run_id="parent-run", request_id="req-parent",
                    writer_id="test-writer",
                )
                from agent.service.run_projector import _request_record
                run_context = SimpleNamespace(
                    run_id="parent-run",
                    user_id="ha-user",
                    durable_store_view=view,
                    workspace=None,
                    delegation_depth=0,
                )
                await service.spawn_subtask(
                    run_context,
                    request_text="child",
                    target_planet="mercury",
                )
                tasks = await service.list_tasks(ListTasksRequest(
                    tenant_id="ha-tenant", user_id="ha-user",
                    session_id="sess", request_id="req-lt",
                ))
                child = next(t for t in tasks if t.parent_task_id == "task-parent")
                child_session = f"sess:subtask:{child.task_id}"
                read = store.read_run_snapshot(
                    "ha-tenant", child.run_ids[0], session_id=child_session,
                )
                record = _request_record(read)
                assert record["metadata"]["delegation_depth"] == 1

                import pytest
                from agent.service.errors import AgentServiceError

                deep_context = SimpleNamespace(
                    run_id=child.run_ids[0],
                    user_id="ha-user",
                    durable_store_view=DurableRuntimeStoreView(
                        store, tenant_id="ha-tenant",
                        session_id=child_session, run_id=child.run_ids[0],
                        request_id="req-deep", writer_id="test-writer-2",
                    ),
                    workspace=None,
                    delegation_depth=1,
                )
                with pytest.raises(AgentServiceError) as err:
                    await service.spawn_subtask(
                        deep_context, request_text="grandchild",
                    )
                assert "cannot delegate" in str(err.value)
            finally:
                await service.close()

    asyncio.run(scenario())


def test_toolset_omits_spawn_subtask_for_delegated_runs() -> None:
    from agent.loop.toolset import build_toolset
    from agent.cognition.effect_authorization import EffectAuthorization

    authorization = EffectAuthorization.from_state({}, "test")
    tools = build_toolset(authorization, delegation_depth=1)
    assert "spawn_subtask" not in tools
    tools = build_toolset(authorization, delegation_depth=0)
    assert "spawn_subtask" in tools


def test_list_sessions_hides_derived_subtask_sessions() -> None:
    async def scenario() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            service, store = harness.service_at(Path(tmp))
            try:
                from agent.service.contracts import ListSessionsRequest
                store.ensure_session("ha-tenant", "sess", "ha-user")
                store.ensure_session(
                    "ha-tenant", "sess:subtask:task-x", "ha-user",
                )
                sessions = await service.list_sessions(ListSessionsRequest(
                    tenant_id="ha-tenant", user_id="ha-user",
                    request_id="req-ls",
                ))
                ids = [getattr(s, "session_id", "") for s in sessions]
                assert "sess" in ids
                assert "sess:subtask:task-x" not in ids
            finally:
                await service.close()

    asyncio.run(scenario())
