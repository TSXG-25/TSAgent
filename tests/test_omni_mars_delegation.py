"""Workstation -> Mars explicit delegation, end to end.

The desktop's @planet path walks the durable delegation chain instead of
stamping a flat origin label: source-side parent Task -> delegate_run
(child Task + Handoff) -> start_run (child executes in the *source*
session with the inherited workspace). The chain is surface-agnostic —
Omni and Mercury drive the exact same call sequence — so the test is
parametrized over the source planet and asserts the record the user
described: Mars edits and tests, and the result is visible back on the
source scope and discoverable on the Mars scope.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import tests.test_ha_r1_harness as harness
from agent.service.contracts import (
    CreateTaskRequest,
    DelegateRunRequest,
    ListHandoffsRequest,
    ListRunsRequest,
    ListTasksRequest,
    RunLookupRequest,
    StartRunRequest,
)

REQUEST = "add normalize() to util.py and run pytest"
UTIL = "def normalize(s):\n    return ' '.join(s.split())\n"
TEST = (
    "from util import normalize\n\n"
    "def test_normalize():\n"
    "    assert normalize(' a  b ') == 'a b'\n"
)


def _child_tool_turns():
    """write_file x2 (util + test), then shell pytest, then the report."""
    return [
        (REQUEST, {
            "tool_calls": [
                {"name": "write_file",
                 "arguments": {"path": "util.py", "content": UTIL}},
                {"name": "write_file",
                 "arguments": {"path": "test_util.py", "content": TEST}},
            ],
        }),
        (REQUEST, harness.call_tool(
            "shell", cmd=f"{sys.executable} -B -m pytest -q")),
        (REQUEST, "normalize() added to util.py; pytest passed"),
    ]


async def _wait_terminal(service, tenant, session, run_id, timeout=60.0):
    for _ in range(int(timeout / 0.1)):
        head = service._store.get_run_head(
            tenant, run_id, session_id=session
        )
        status = getattr(head, "run_status", "") or ""
        if status.startswith(("COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT")):
            return head
        await asyncio.sleep(0.1)
    raise AssertionError(f"run {run_id} did not reach a terminal status")


import pytest


@pytest.mark.parametrize("source", ["sun", "mercury"])
def test_delegated_mars_run_executes_and_reports_back(
    source: str, monkeypatch, tmp_path: Path
) -> None:
    async def scenario() -> None:
        workspace = tmp_path / "repo"
        workspace.mkdir()
        service, store = harness.service_at(tmp_path)
        try:
            session = f"{source}-sess"
            store.ensure_session("t", session, "u")
            store.upsert_workspace("t", "ws", path=str(workspace))
            store.assign_session_workspace("t", session, "ws")

            # @mars send — source-side parent Task then task-anchored
            # delegate_run, exactly what delegateFromPlanet calls.
            parent = await service.create_task(CreateTaskRequest(
                tenant_id="t", user_id="u", session_id=session,
                request_id="req-parent", origin_planet=source,
            ))
            delegation = await service.delegate_run(DelegateRunRequest(
                tenant_id="t", user_id="u", session_id=session,
                request_id="req-delegate", parent_task_id=parent.task_id,
                request_text=REQUEST, target_planet="mars",
            ))
            assert delegation["workspace_root"] == str(workspace)

            child = await service.start_run(StartRunRequest(
                tenant_id="t", user_id="u", session_id=session,
                request_id="req-child", request_text=delegation["request_text"],
                task_id=delegation["task_id"], origin_planet="mars",
                workspace_root=delegation["workspace_root"],
                metadata={"delegation_depth": 1},
            ))
            head = await _wait_terminal(
                service, "t", session, child.run_id
            )
            assert head.run_status == "COMPLETED", head.run_status

            # Task tree: source parent -> Mars child task -> child run.
            tasks = await service.list_tasks(ListTasksRequest(
                tenant_id="t", user_id="u", session_id=session,
                request_id="req-tasks",
            ))
            child_task = next(
                t for t in tasks if t.task_id == delegation["task_id"]
            )
            assert child_task.parent_task_id == parent.task_id
            assert child_task.origin_planet == "mars"
            assert child.run_id in child_task.run_ids

            # Handoff edge binds the child run at admission — the source
            # scope resolves progress/result through it.
            handoffs = await service.list_handoffs(ListHandoffsRequest(
                tenant_id="t", user_id="u", session_id=session,
                request_id="req-handoffs",
            ))
            assert len(handoffs) == 1
            assert handoffs[0].source_task_id == parent.task_id
            assert handoffs[0].target_run_id == child.run_id
            assert handoffs[0].target_planet == "mars"

            # Result back on the source scope — the run is listed for the
            # source session and carries the Mars report as output.
            runs = await service.list_runs(ListRunsRequest(
                tenant_id="t", user_id="u", session_id=session,
                request_id="req-runs",
            ))
            mars_run = next(r for r in runs if r.run_id == child.run_id)
            assert mars_run.origin_planet == "mars"
            assert "pytest passed" in mars_run.output.text

            # The same record is discoverable for the Mars workstation via
            # the tenant catalog (its delegated section filters
            # origin_planet + foreign session).
            catalog = await service.list_runs(ListRunsRequest(
                tenant_id="t", user_id="u", session_id="",
                origin_planet="mars", request_id="req-catalog",
            ))
            assert child.run_id in [r.run_id for r in catalog]

            # Real effects: Mars wrote both files and the verification ran.
            assert (workspace / "util.py").read_text() == UTIL
            assert (workspace / "test_util.py").exists()
        finally:
            await service.close()

    harness.setup_provider(monkeypatch, [
        (REQUEST, harness.intent(
            REQUEST, artifact=False,
            outcomes=("FILE_MUTATION", "COMMAND_EXECUTION"),
            write_targets=(".",),
        )),
        *_child_tool_turns(),
    ])
    asyncio.run(scenario())


def test_delegate_run_rejects_unknown_parent_task(tmp_path: Path) -> None:
    async def scenario() -> None:
        service, store = harness.service_at(tmp_path)
        try:
            store.ensure_session("t", "omni-sess", "u")
            import pytest

            from agent.service.errors import AgentServiceError

            with pytest.raises(AgentServiceError) as err:
                await service.delegate_run(DelegateRunRequest(
                    tenant_id="t", user_id="u", session_id="omni-sess",
                    request_id="req-d", parent_task_id="task-missing",
                    request_text="x", target_planet="mars",
                ))
            assert "parent task not found" in str(err.value)
        finally:
            await service.close()

    asyncio.run(scenario())
