"""P2-LH3 regressions for preserving verified effects during replan."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from agent.orchestrator.planner import (
    PlannerStage,
    _reconcile_replan_tasks,
    _task_effect_signature,
)
from agent.task import ExecutionPlan, Task


def _task(
    task_id: str,
    verb: str,
    target: str,
    status: str,
    *,
    dependencies: list[str] | None = None,
) -> dict:
    return {
        "id": task_id,
        "verb": verb,
        "target": target,
        "target_type": "file" if target.startswith("output/") else "text",
        "goal": f"{verb} {target}",
        "description": "",
        "success_condition": "",
        "dependencies": dependencies or [],
        "children": [],
        "inputs": {},
        "status": status,
        "observations": [],
        "error": "",
    }


def test_reconcile_filters_verified_effects_and_remaps_dependencies() -> None:
    current = [
        _task("task-1", "write", "output/main.md", "succeeded"),
        _task("task-2", "write", "output/checklist.md", "succeeded"),
        _task("task-3", "explain", "final summary", "failed"),
    ]
    proposed = [
        _task("task-1", "write", "output/main.md", "pending"),
        _task("task-2", "write", "output/checklist.md", "pending"),
        _task(
            "task-3",
            "explain",
            "final summary",
            "pending",
            dependencies=["task-1", "task-2"],
        ),
    ]

    reconciled, skipped = _reconcile_replan_tasks(
        current,
        proposed,
        replan_attempt=1,
    )

    assert skipped == ("task-1", "task-2")
    assert [task["verb"] for task in reconciled] == ["explain"]
    assert reconciled[0]["id"] == "replan-1-3"
    assert reconciled[0]["dependencies"] == []
    assert reconciled[0]["id"] not in {task["id"] for task in current}


def test_effect_signature_is_content_free_and_operation_specific() -> None:
    first = _task("task-1", "write", "OUTPUT\\Result.txt", "succeeded")
    first["inputs"] = {"content": "secret-one"}
    second = _task("task-2", "write", "output/result.txt", "pending")
    second["inputs"] = {"content": "secret-two"}
    modify = _task("task-3", "modify", "output/result.txt", "pending")

    assert _task_effect_signature(first) == _task_effect_signature(second)
    assert _task_effect_signature(first) != _task_effect_signature(modify)
    assert "secret" not in repr(_task_effect_signature(first))


def test_reconcile_preserves_dependency_on_existing_unfinished_task() -> None:
    current = [
        _task("task-4", "read", "output/source.md", "pending"),
        _task("task-5", "explain", "final summary", "failed"),
    ]
    proposed = [
        _task(
            "task-1",
            "explain",
            "final summary",
            "pending",
            dependencies=["task-4", "task-5"],
        )
    ]

    reconciled, _skipped = _reconcile_replan_tasks(
        current,
        proposed,
        replan_attempt=1,
    )

    assert reconciled[0]["dependencies"] == ["task-4"]


def test_replan_executes_only_replacement_task_after_verified_writes(
    monkeypatch,
) -> None:
    captured_prompts: list[str] = []

    async def fake_plan_with_metadata(prompt, *_args, **_kwargs):
        captured_prompts.append(prompt)
        return SimpleNamespace(
            tasks=[
                _task(
                    "task-1",
                    "write",
                    "output/p2_l05.md",
                    "pending",
                ),
                _task(
                    "task-2",
                    "write",
                    "output/p2_l05_checklist.md",
                    "pending",
                ),
                _task(
                    "task-3",
                    "explain",
                    "final summary",
                    "pending",
                    dependencies=["task-1", "task-2"],
                )
            ]
        )

    monkeypatch.setattr(
        "agent.orchestrator.planner.plan_with_metadata",
        fake_plan_with_metadata,
    )

    class Selector:
        def compile(self, task: Task, *, context) -> ExecutionPlan:
            return ExecutionPlan(task=task, executor="llm")

    orchestrator = SimpleNamespace(
        replan_count=0,
        _selector=Selector(),
        _timings={},
    )
    current = [
        _task("task-1", "write", "output/p2_l05.md", "succeeded"),
        _task(
            "task-2",
            "write",
            "output/p2_l05_checklist.md",
            "succeeded",
        ),
        {
            **_task("task-3", "explain", "final summary", "failed"),
            "error": "temporary model response failure",
        },
    ]
    state = {
        "plan": current,
        "execution_plans": [],
    }

    updated, next_state = asyncio.run(
        PlannerStage(orchestrator).replan(
            state,
            (
                "保存主要结果到 output/p2_l05.md，再保存验证清单到 "
                "output/p2_l05_checklist.md"
            ),
            "p2-user",
        )
    )

    assert next_state == "EXECUTE"
    assert [task["verb"] for task in updated["plan"]] == ["explain"]
    assert updated["plan"][0]["id"].startswith("replan-1-")
    assert len(updated["execution_plans"]) == 1
    assert updated["replan_skipped_verified_effects"] == 2
    assert "已经成功并通过执行验证" in captured_prompts[0]
    assert "output/p2_l05.md" in captured_prompts[0]
