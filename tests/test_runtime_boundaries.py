"""Regression tests for the unified runtime and context/metric boundaries."""
import asyncio
import pytest
from langchain_core.messages import HumanMessage

from agent.context import (
    ExecutorContext,
    PlannerContext,
    ReflectionContext,
    RuntimeContext,
)
from agent.cognition import CognitiveContext
from agent.decision import DecisionInput
from agent.executor.contract import executor_factory
from agent.orchestrator.executor import ExecutionStage
from agent.task import ExecutionPlan, ExecutionStep, Task
from agent.workflow import ExecutionContext, ExecutionResult
from evaluation.metrics import MetricCollector, MetricDefinition, TrendGate
from evaluation.metrics_v1 import MetricsV1
from evaluation.metrics_v2 import MetricsV2, trend_gate
from agent.reflection.reflector import reflect_context
from evaluation.benchmark.failboard_v2 import Evidence


class _Selector:
    def compile(self, task, context=None):
        steps = []
        if task.verb.value == "write":
            steps.append(ExecutionStep(tool="filesystem.write"))
        return ExecutionPlan(task=task, steps=steps, executor="fake")


class _Orchestrator:
    _selector = _Selector()
    _timings = {}


class _FakeExecutor:
    async def execute(self, task, context):
        assert isinstance(task, Task)
        assert context.task is task
        return ExecutionResult(
            success=True,
            outputs={"text": "unified result"},
            metadata={"executor": "fake", "time_s": 0.01},
        )


def test_execution_stage_compiles_state_and_uses_factory(monkeypatch):
    monkeypatch.setitem(executor_factory._registry, "fake", lambda: _FakeExecutor())
    state = {
        "messages": [HumanMessage(content="run task")],
        "plan": [{
            "id": "task-1",
            "verb": "read",
            "target": "example.py",
            "target_type": "file",
            "goal": "读取 example.py",
            "status": "pending",
            "observations": [],
        }],
        "execution_plans": [],
    }

    updated, next_state = asyncio.run(ExecutionStage(_Orchestrator()).run(state))

    assert next_state == "NEXT_TASK"
    assert updated["plan"][0]["status"] == "succeeded"
    assert updated["execution_plans"][0].executor == "fake"
    assert updated["plan"][0]["observations"][0]["action"] == "fake_executor"


def test_execution_stage_rejects_unverified_file_write(monkeypatch):
    monkeypatch.setitem(executor_factory._registry, "fake", lambda: _FakeExecutor())
    state = {
        "messages": [
            HumanMessage(
                content="创建 output/definitely_missing_from_test.py"
            )
        ],
        "plan": [{
            "id": "task-write",
            "verb": "write",
            "target": "output/definitely_missing_from_test.py",
            "target_type": "file",
            "goal": "创建测试文件",
            "status": "pending",
            "observations": [],
        }],
        "execution_plans": [],
    }

    updated, next_state = asyncio.run(ExecutionStage(_Orchestrator()).run(state))

    assert next_state == "RECOVER"
    assert updated["plan"][0]["status"] == "failed"
    assert "UNVERIFIED" in updated["plan"][0]["error"]


def test_context_views_are_frozen_snapshots():
    task = Task(id="task-1", goal="inspect")
    execution = ExecutionContext(
        task=task,
        user_input="inspect",
        facts={"ready": True},
        variables={"workspace": "ws"},
    )

    runtime = execution.runtime_view(user_id="u-1", request_id="r-1")
    executor = execution.executor_view(user_id="u-1", request_id="r-1")
    reflection = execution.reflection_view(
        task_id="task-1", failure="timeout", symptom="timeout"
    )

    assert isinstance(runtime, RuntimeContext)
    assert isinstance(executor, ExecutorContext)
    assert isinstance(reflection, ReflectionContext)
    assert executor.task is task
    assert executor.facts["ready"] is True
    with pytest.raises(TypeError):
        executor.facts["new"] = True


def test_planner_context_is_the_named_compatible_planning_view():
    planner = PlannerContext(query="inspect")
    assert isinstance(planner, CognitiveContext)
    assert planner.short_summary() == "无上下文"


def test_reflection_and_decision_consume_narrow_context_views():
    reflection_context = ReflectionContext(
        runtime=RuntimeContext(query="inspect"),
        task_id="task-1",
        failure="file not found",
        evidence=(Evidence(
            source="grounder",
            location="workspace.resolve",
            expected="candidate",
            actual="无匹配",
        ),),
        symptom="hallucination",
        retry_count=1,
        last_action="read_file",
    )

    reflection = reflect_context(reflection_context)
    decision_input = DecisionInput.from_reflection_context(
        reflection_context,
        diagnosis="grounding_miss",
        diagnosis_confidence=reflection.diagnosis.confidence,
    )

    assert reflection.diagnosis.root_cause == "grounding"
    assert decision_input.event_id == "task-1"
    assert decision_input.state.retry_count == 1


def test_metric_definition_collector_and_trend_gate():
    definitions = (
        MetricDefinition("accuracy", direction="ge"),
        MetricDefinition("latency", direction="le"),
    )
    collector = MetricCollector(definitions)
    previous = collector.collect({"accuracy": 0.8, "latency": 1.0})
    current = collector.collect({"accuracy": 0.9, "latency": 1.1})
    result = TrendGate.evaluate(current, previous)

    assert not result.passes
    assert "latency" in result.failures[0]


def test_legacy_metric_models_expose_shared_reports():
    assert "planning_success" in MetricsV1().to_report().to_dict()
    assert "diagnosis_accuracy" in MetricsV2().to_report().to_dict()

    current = MetricsV2(goal_coverage=0.4)
    previous = MetricsV2(goal_coverage=0.5)
    passed, failures = trend_gate(current, previous)
    assert not passed
    assert any("goal_coverage" in failure for failure in failures)
