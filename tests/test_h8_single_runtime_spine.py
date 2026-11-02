"""H8 contracts for the single Runtime spine."""

import ast
from types import SimpleNamespace
from pathlib import Path

from agent.failure import (
    ClassificationSource,
    FailureKind,
    FailurePolicy,
    failure_fact,
)
from agent.action_result import ActionResult
from agent.executor.contract import executor_factory
from agent.orchestrator.executor import ExecutionStage
from agent.orchestrator.main import ExecutionOrchestrator
from agent.runtime_budget import RunBudget
from agent.task import ExecutionPlan, ExecutionStep, Task
from agent.workflow import ExecutionResult
import asyncio


ROOT = Path(__file__).resolve().parents[1]


def test_action_failure_is_structured_observation() -> None:
    result = ActionResult.failure(
        error_code="FILE_NOT_FOUND",
        content="missing.txt",
        classification_source=ClassificationSource.STRUCTURED.value,
    )

    assert result.failure_kind == FailureKind.ACTION.value
    assert result.classification_source == ClassificationSource.STRUCTURED.value
    assert result.to_dict()["error_code"] == "FILE_NOT_FOUND"


def test_structural_failure_enters_policy_and_produces_directive() -> None:
    failure = failure_fact(
        "CONTRACT_VIOLATION",
        message="compiler contract failed",
        component="compiler",
    )

    directive = FailurePolicy().resolve(failure, {"retries": 0})

    assert failure.kind is FailureKind.STRUCTURAL
    assert directive.failure.code == "CONTRACT_VIOLATION"
    assert directive.action in {"ask", "finish", "retry", "switch"}
    assert directive.event_id.startswith("runtime:CONTRACT_VIOLATION:")


def test_action_failure_does_not_run_reflection_policy() -> None:
    failure = failure_fact("FILE_NOT_FOUND", message="missing")

    directive = FailurePolicy().resolve(failure)

    assert directive.action == "observe"
    assert directive.diagnosis == ""
    assert directive.event_id == ""


def test_structural_action_result_is_routed_to_recovery_state(monkeypatch) -> None:
    class UnknownToolExecutor:
        async def execute(self, _task, _context):
            return ExecutionResult(
                success=False,
                error="UNKNOWN_TOOL: copy_file",
                action_result=ActionResult.failure(
                    error_code="UNKNOWN_TOOL",
                    content="copy_file is not registered",
                ),
                metadata={"executor": "unknown"},
            )

    monkeypatch.setitem(executor_factory._registry, "tool", UnknownToolExecutor)
    task = Task.from_dict({
        "id": "structural-failure",
        "verb": "read",
        "target": "input.txt",
        "target_type": "file",
    })
    state = {
        "messages": [],
        "plan": [task.to_dict()],
        "execution_plans": [
            ExecutionPlan(
                task=task,
                steps=[ExecutionStep(tool="filesystem.read", outputs=["content"])],
                executor="unknown",
            )
        ],
        "execution_mode": "result_driven",
        "inbox": {"next_step": [], "next_turn": []},
    }

    class Orchestrator:
        _timings: dict[str, float] = {}
        run_context = None

    plan = state["execution_plans"][0]
    task_dict = state["plan"][0]
    asyncio.run(
        ExecutionStage(Orchestrator()).execute_action(state, task_dict, plan)
    )

    assert state["runtime_failure_kind"] == FailureKind.STRUCTURAL.value
    assert state["runtime_failure"]["error_code"] == "UNKNOWN_TOOL"


def test_production_agent_code_does_not_import_evaluation() -> None:
    forbidden = []
    for path in (ROOT / "agent").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(name == "evaluation" or name.startswith("evaluation.") for name in names):
                forbidden.append(str(path.relative_to(ROOT)))

    assert forbidden == []


def test_production_execution_path_does_not_import_legacy_workspace() -> None:
    allowed = {"agent/compat/workspace.py"}
    offenders = []
    for path in (ROOT / "agent").rglob("*.py"):
        relative = str(path.relative_to(ROOT))
        if relative in allowed:
            continue
        source = path.read_text(encoding="utf-8")
        if "agent.compat.workspace" in source or "get_legacy_workspace_service" in source:
            offenders.append(relative)

    assert offenders == []


def test_production_agent_code_does_not_import_compat_modules() -> None:
    offenders = []
    for path in (ROOT / "agent").rglob("*.py"):
        relative = str(path.relative_to(ROOT))
        if relative == "agent/compat" or relative.startswith("agent/compat/"):
            continue
        source = path.read_text(encoding="utf-8")
        if "agent.compat" in source:
            offenders.append(relative)

    assert offenders == []


def test_runtime_dispatches_loop_from_plan_and_propagates_failure(monkeypatch) -> None:
    from types import SimpleNamespace

    import agent.runtime as runtime_module
    from agent.runtime import UniversalAgent

    calls: list[str] = []

    class MemoryView:
        def get_retrieved_memory(self, _value):
            return {"long_term": "", "facts": ""}

    class Tracker:
        def update(self, **_kwargs):
            pass

    class Orchestrator:
        replan_count = 0
        run_budget = None

        def reset_timings(self):
            pass

        def bind_run_budget(self, budget):
            self.run_budget = budget

        def get_timings(self):
            return {}

        async def plan(self, **kwargs):
            calls.append("plan")
            return kwargs["runtime_state"], "LOOP", None

        async def run_loop(self, state, *_args):
            calls.append("loop")
            state["plan"] = [{"id": "read-1", "verb": "read", "status": "failed"}]
            state["runtime_failure"] = {"error_code": "FILE_NOT_FOUND"}
            state["runtime_failure_code"] = "FILE_NOT_FOUND"
            state["runtime_terminal_status"] = "FAILED_TERMINAL"
            return "partial result"

        async def finalize(self, **_kwargs):
            return "partial result"

    agent = UniversalAgent.__new__(UniversalAgent)
    agent._timings = {}
    agent.orchestrator = Orchestrator()
    agent._memory_view = MemoryView()
    agent._memory_namespace = "h8"
    agent._session_context = SimpleNamespace(
        memory_scope="session",
        conversation_tracker=Tracker(),
    )
    agent._pending_execution_target = ""
    agent._run_context = None
    agent.last_run_evidence = {}
    agent._wall_total_seconds = 0.0
    agent._print_timing_summary = lambda: {}

    answer = asyncio.run(agent._run_in_context("读取 missing.txt"))

    assert answer == "partial result"
    assert calls == ["plan", "loop"]
    assert agent.last_run_evidence["failure_code"] == "FILE_NOT_FOUND"


def test_evaluation_failure_contract_is_a_production_facade() -> None:
    from agent.failure import Evidence as ProductionEvidence
    from evaluation.benchmark.failboard_v2 import Evidence as EvaluationEvidence

    assert EvaluationEvidence is ProductionEvidence
