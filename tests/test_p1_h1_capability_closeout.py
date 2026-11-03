"""Deterministic P1-H1 capability closeout contracts."""

import asyncio

import pytest

from agent.compiler.context import CompilerContext
from agent.compiler.rules import DEFAULT_RULES
from agent.compiler.tool_selector import Compiler
from agent.cognition.cognitive_context import CognitiveContext
from agent.cognition.intent_engine import IntentEngine
from agent.executor.plan_executor import PlanExecutor
from agent.executor.verifier import ExecutionArtifacts, ExecutionVerifier
from agent.event_bus import EventBus
from agent.services.workspace_service import WorkspaceService
from agent.task import ExecutionPlan, Task, Verb


def _compiler() -> Compiler:
    compiler = Compiler()
    for rule in DEFAULT_RULES:
        compiler.add_rule(rule)
    return compiler


def test_copy_move_delete_have_ground_truth_verification(monkeypatch, tmp_path) -> None:
    import tools.filesystem as filesystem

    monkeypatch.setattr(filesystem, "ROOT", tmp_path)
    monkeypatch.setattr(filesystem, "_get_workspace_service", lambda: None)
    filesystem._path_cache.clear()
    source = tmp_path / "output" / "source.txt"
    source.parent.mkdir()
    source.write_text("stable content\n", encoding="utf-8")
    compiler = _compiler()
    executor = PlanExecutor()
    workspace = WorkspaceService.scoped(tmp_path, event_bus=EventBus(), build_index=True)

    copy_task = Task.from_dict({
        "id": "task-1", "verb": "copy",
        "target": "output/copied.txt", "target_type": "file",
        "goal": "copy file", "description": "copy file",
        "success_condition": "copied",
        "inputs": {"source": "output/source.txt", "destination": "output/copied.txt"},
    })
    assert copy_task is not None
    copy_plan = compiler.compile(copy_task, context=CompilerContext())
    copy_result = asyncio.run(executor.execute(copy_plan, workspace=workspace))
    assert copy_result["_error"] == ""
    assert (tmp_path / "output/copied.txt").read_text() == source.read_text()
    copy_verification = ExecutionVerifier().verify(
        copy_plan,
        ExecutionArtifacts(file_operations=copy_result["_file_operations"]),
        task=copy_task,
        workspace=workspace,
    )
    assert copy_verification.success is True

    move_task = Task.from_dict({
        "id": "task-1", "verb": "move",
        "target": "output/moved.txt", "target_type": "file",
        "goal": "move file", "description": "move file",
        "success_condition": "moved",
        "inputs": {"source": "output/copied.txt", "destination": "output/moved.txt"},
    })
    assert move_task is not None
    move_result = asyncio.run(executor.execute(
        compiler.compile(move_task, context=CompilerContext()),
        workspace=workspace,
    ))
    assert move_result["_error"] == ""
    assert not (tmp_path / "output/copied.txt").exists()
    assert (tmp_path / "output/moved.txt").exists()

    delete_task = Task.from_dict({
        "id": "task-1", "verb": "delete",
        "target": "output/moved.txt", "target_type": "file",
        "goal": "delete file", "description": "delete file",
        "success_condition": "deleted",
        "inputs": {"path": "output/moved.txt"},
    })
    assert delete_task is not None
    delete_plan = compiler.compile(delete_task, context=CompilerContext())
    delete_result = asyncio.run(executor.execute(delete_plan, workspace=workspace))
    assert delete_result["_error"] == ""
    assert not (tmp_path / "output/moved.txt").exists()
    delete_verification = ExecutionVerifier().verify(
        delete_plan,
        ExecutionArtifacts(file_operations=delete_result["_file_operations"]),
        task=delete_task,
        workspace=workspace,
    )
    assert delete_verification.success is True
    workspace.close()

