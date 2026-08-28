"""Execute the H8 deterministic contract dataset without a Provider."""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from agent.action_result import ActionResult
from agent.compiler.tool_selector import CompileError, Compiler
from agent.failure import FailurePolicy, FailureKind, failure_fact
from agent.goal import GoalVerifier
from agent.orchestrator.main import ExecutionOrchestrator
from agent.runtime_budget import RunBudget
from agent.task import ExecutionPlan, ExecutionStep, Task

from benchmarks.h8.cases import DATASET_VERSION, dataset_hash
from benchmarks.h8.oracle import validate_records


def _record(case_id: str, passed: bool, detail: str = "") -> dict[str, object]:
    return {"case_id": case_id, "passed": passed, "detail": detail}


def run() -> dict[str, object]:
    records: list[dict[str, object]] = []

    action = FailurePolicy().resolve(failure_fact("FILE_NOT_FOUND", message="missing"))
    records.append(_record("H801", action.action == "observe" and not action.event_id))

    structural = FailurePolicy().resolve(
        failure_fact("UNKNOWN_TOOL", message="copy_file", component="compiler")
    )
    records.append(_record("H802", structural.action != "observe" and bool(structural.event_id)))

    offenders: list[str] = []
    for path in (ROOT / "agent").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            modules = (
                [item.name for item in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else []
            )
            if any(item == "evaluation" or item.startswith("evaluation.") for item in modules):
                offenders.append(str(path.relative_to(ROOT)))
    records.append(_record("H803", not offenders, ",".join(offenders)))

    compiler = Compiler()
    task = Task.from_dict({"id": "unknown", "verb": "read", "target": "x.txt", "target_type": "file"})
    unknown = ExecutionPlan(task=task, steps=[ExecutionStep(tool="not_registered", outputs=["x"])])
    try:
        compiler._static_check(unknown)
    except CompileError:
        records.append(_record("H804", True))
    else:
        records.append(_record("H804", False, "unknown tool passed static check"))

    from agent.orchestrator.executor import ExecutionStage

    class Orchestrator:
        _timings: dict[str, float] = {}
        run_context = None

    mutation = Task.from_dict({
        "id": "write",
        "verb": "write",
        "target": "output/x.txt",
        "target_type": "file",
    })
    error = ExecutionStage(Orchestrator())._verify_completion(mutation, None)
    records.append(_record("H805", "FILE_OPERATION_UNVERIFIED" in error))

    budget = RunBudget(max_seconds=1.0, max_transitions=2, max_goal_rounds=2, max_recoveries=1)
    budget.start(now=10.0)
    transitions = [budget.consume_transition(now=10.0) for _ in range(3)]
    recoveries = [budget.consume_recovery(), budget.consume_recovery()]
    records.append(_record(
        "H806",
        transitions == [True, True, False]
        and recoveries == [True, False]
        and budget.exhausted_code(now=10.0) == "RUNTIME_TRANSITION_BUDGET_EXHAUSTED",
    ))

    incomplete = GoalVerifier.verify({
        "plan": [{"id": "t1", "status": "succeeded"}],
        "requested_outcomes": ["CODE_EXECUTION", "USER_VISIBLE_OUTPUT"],
        "execution_evidence": [],
        "required_effects": [],
        "verified_effects": [],
        "unsupported_effects": [],
        "failed_effects": [],
        "answer_required": True,
    }, "")
    records.append(_record("H807", not incomplete.can_complete))

    orchestrator = ExecutionOrchestrator()
    async def exercise_observe():
        return await orchestrator.observe_failure(
            {
                "runtime_failure_retryable": False,
                "runtime_failure_code": "FILE_NOT_FOUND",
                "plan": [],
                "inbox": {},
            },
            "read missing",
            "h8",
        )

    observed, next_state = asyncio.run(exercise_observe())
    records.append(_record(
        "H808",
        next_state == "FAIL"
        and observed["runtime_terminal_status"] == "FAILED_TERMINAL"
        and not orchestrator.replan_count,
    ))

    summary = validate_records(records)
    return {
        "dataset_version": DATASET_VERSION,
        "dataset_hash": dataset_hash(),
        "cases": records,
        "summary": summary,
    }


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
