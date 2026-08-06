"""Production v2.2C Run-level Store/Resolver/Coordinator tests."""
from __future__ import annotations

import asyncio
from dataclasses import replace
from itertools import count

import pytest

from agent.checkpoint import (
    InMemoryCheckpointStore,
    WorkflowCheckpointRequest,
    fact_digest,
)
from agent.executor.contract import executor_factory
from agent.executor.executors.workflow import WorkflowExecutor
from agent.run_resume import (
    ArtifactRequirement,
    InMemoryRunResumeStore,
    JsonRunResumeStore,
    RunArtifactFact,
    RunResumeActivationError,
    RunResumeCoordinator,
    RunResumeDisposition,
    RunResumeIndex,
    RunResumeReasonCode,
    RunResumeRequest,
    RunResumeResolver,
    RunWorkflowStatus,
    WorkflowDependency,
    WorkflowSummary,
    run_index_digest,
)
from agent.workflow import (
    ExecutionContext,
    ExecutionResult,
    ExecutionSpec,
    ExecutorType,
    OutputArtifact,
    Stage,
    Workflow,
)


class RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(self, target, context):
        self.calls.append(target.id)
        return ExecutionResult(
            success=True,
            outputs={"text": f"done:{target.id}"},
            metadata={"executor": "fake"},
        )


class InterruptOnceWorkflowExecutor:
    """Inject one deterministic post-stage interruption for the B resume case."""

    def __init__(self, workflow_id: str) -> None:
        self.workflow_id = workflow_id
        self.interrupted = False
        self.delegate = WorkflowExecutor()

    async def execute(self, workflow, context, *, checkpoint_request=None):
        request = checkpoint_request
        if (
            request is not None
            and workflow.id == self.workflow_id
            and not self.interrupted
        ):
            self.interrupted = True
            request = replace(
                request,
                interrupt_after_stage_id=workflow.topological_sort()[0].id,
            )
        return await self.delegate.execute(
            workflow,
            context,
            checkpoint_request=request,
        )


@pytest.fixture
def fake_executor():
    original = dict(executor_factory._registry)
    fake = RecordingExecutor()
    executor_factory._registry.clear()
    executor_factory.register("tool", lambda: fake)
    executor_factory.register("llm", lambda: fake)
    try:
        yield fake
    finally:
        executor_factory._registry.clear()
        executor_factory._registry.update(original)


def _index(checkpoint_id: str = "cp-b") -> RunResumeIndex:
    workflows = (
        WorkflowSummary(
            workflow_id="wf.a",
            workflow_version="1.0.0",
            status=RunWorkflowStatus.COMPLETED,
            checkpoint_id="cp-a",
        ),
        WorkflowSummary(
            workflow_id="wf.b",
            workflow_version="1.0.0",
            status=RunWorkflowStatus.SUSPENDED,
            checkpoint_id=checkpoint_id,
            depends_on=("wf.a",),
        ),
    )
    return RunResumeIndex(
        run_id="run-1",
        workflow_sequence=("wf.a", "wf.b"),
        workflows=workflows,
        completed_workflow_ids=("wf.a",),
        active_workflow_id="wf.b",
        active_checkpoint_id=checkpoint_id,
        pending_workflow_ids=(),
        workflow_dependencies=(
            WorkflowDependency("wf.a"),
            WorkflowDependency("wf.b", ("wf.a",)),
        ),
        created_at="2026-08-06T00:00:00Z",
        updated_at="2026-08-06T00:00:00Z",
    )


def _workflow() -> Workflow:
    return Workflow(
        id="wf.b",
        version="1.0.0",
        description="resume workflow B",
        stages=[
            Stage(
                id="stage-1",
                execution=ExecutionSpec(executor=ExecutorType.TOOL),
                description="read input",
            ),
            Stage(
                id="stage-2",
                execution=ExecutionSpec(executor=ExecutorType.TOOL),
                description="write output",
            ),
        ],
    )


def _one_stage_workflow(
    workflow_id: str,
    stage_id: str,
    *,
    output_type: str = "artifact",
) -> Workflow:
    return Workflow(
        id=workflow_id,
        version="1.0.0",
        description=f"resume workflow {workflow_id}",
        stages=[
            Stage(
                id=stage_id,
                execution=ExecutionSpec(executor=ExecutorType.TOOL),
                description=f"execute {stage_id}",
                outputs=(
                    [OutputArtifact(type=output_type)]
                    if output_type
                    else []
                ),
            )
        ],
    )


def _pending_abc_index(
    *,
    run_id: str = "run-abc",
    require_artifacts: bool = True,
) -> RunResumeIndex:
    requirements_b = (
        ArtifactRequirement(
            artifact_id="a-stage-0",
            expected_digest=fact_digest("done:a-stage"),
        ),
    ) if require_artifacts else ()
    requirements_c = (
        ArtifactRequirement(
            artifact_id="b-stage-0",
            expected_digest=fact_digest("done:b-stage"),
        ),
    ) if require_artifacts else ()
    workflows = (
        WorkflowSummary(
            workflow_id="wf.a",
            workflow_version="1.0.0",
            status=RunWorkflowStatus.PENDING,
        ),
        WorkflowSummary(
            workflow_id="wf.b",
            workflow_version="1.0.0",
            status=RunWorkflowStatus.PENDING,
            depends_on=("wf.a",),
            required_artifacts=requirements_b,
        ),
        WorkflowSummary(
            workflow_id="wf.c",
            workflow_version="1.0.0",
            status=RunWorkflowStatus.PENDING,
            depends_on=("wf.b",),
            required_artifacts=requirements_c,
        ),
    )
    return RunResumeIndex(
        run_id=run_id,
        workflow_sequence=("wf.a", "wf.b", "wf.c"),
        workflows=workflows,
        completed_workflow_ids=(),
        active_workflow_id="",
        active_checkpoint_id="",
        pending_workflow_ids=("wf.a", "wf.b", "wf.c"),
        workflow_dependencies=(
            WorkflowDependency("wf.a"),
            WorkflowDependency("wf.b", ("wf.a",)),
            WorkflowDependency("wf.c", ("wf.b",)),
        ),
        created_at="2026-08-06T00:00:00Z",
        updated_at="2026-08-06T00:00:00Z",
    )


def _completed_a_pending_b_index(
    *,
    artifact: RunArtifactFact | None = None,
) -> RunResumeIndex:
    workflows = (
        WorkflowSummary(
            workflow_id="wf.a",
            workflow_version="1.0.0",
            status=RunWorkflowStatus.COMPLETED,
            checkpoint_id="cp-a",
        ),
        WorkflowSummary(
            workflow_id="wf.b",
            workflow_version="1.0.0",
            status=RunWorkflowStatus.PENDING,
            depends_on=("wf.a",),
            required_artifacts=(
                ArtifactRequirement(
                    artifact_id="a-stage-0",
                    expected_digest=fact_digest("done:a-stage"),
                ),
            ),
        ),
    )
    return RunResumeIndex(
        run_id="run-ab",
        workflow_sequence=("wf.a", "wf.b"),
        workflows=workflows,
        completed_workflow_ids=("wf.a",),
        active_workflow_id="",
        active_checkpoint_id="",
        pending_workflow_ids=("wf.b",),
        workflow_dependencies=(
            WorkflowDependency("wf.a"),
            WorkflowDependency("wf.b", ("wf.a",)),
        ),
        artifacts=(artifact,) if artifact is not None else (),
        created_at="2026-08-06T00:00:00Z",
        updated_at="2026-08-06T00:00:00Z",
    )


def _checkpoint_request(store, factory, *, interrupt_after_stage_id: str | None = None):
    return WorkflowCheckpointRequest(
        store=store,
        run_id="run-1",
        session_id="session-1",
        conversation_id="conversation-1",
        user_scope="user-1",
        target_summary="resume workflow B",
        interrupt_after_stage_id=interrupt_after_stage_id,
        clock=lambda: "2026-08-06T00:00:00Z",
        checkpoint_id_factory=factory,
    )


def test_run_index_round_trip_and_strict_revision_store(tmp_path):
    index = _index()
    memory = InMemoryRunResumeStore()
    assert memory.save(index) == index

    next_index = index.evolve(
        parent_digest=run_index_digest(index),
        updated_at="2026-08-06T00:01:00Z",
    )
    assert memory.save(next_index) == next_index
    with pytest.raises(ValueError):
        memory.save(next_index.evolve(parent_digest="wrong"))

    path = tmp_path / "run-resume.json"
    disk = JsonRunResumeStore(path)
    disk.save(index)
    restored = JsonRunResumeStore(path).get("run-1")
    assert restored is not None
    assert restored.to_dict() == index.to_dict()


def test_resolver_selects_active_workflow_and_never_completed_workflow():
    decision = RunResumeResolver.resolve(
        _index(),
        RunResumeRequest(
            requested_run_id="run-1",
            candidate_run_ids=("run-1",),
        ),
    )
    assert decision.disposition is RunResumeDisposition.ALLOW
    assert decision.selected_workflow_id == "wf.b"
    assert decision.selected_checkpoint_id == "cp-b"
    assert decision.skipped_workflow_ids == ("wf.a",)


def test_resolver_blocks_ambiguous_run_and_version_conflict():
    ambiguous = RunResumeResolver.resolve(
        _index(), RunResumeRequest(candidate_run_ids=("run-1", "run-2"))
    )
    assert ambiguous.disposition is RunResumeDisposition.REQUIRE_CLARIFICATION
    assert ambiguous.reason_code is RunResumeReasonCode.AMBIGUOUS_RUN

    incompatible = RunResumeResolver.resolve(
        _index(),
        RunResumeRequest(current_workflow_versions=(("wf.b", "2.0.0"),)),
    )
    assert incompatible.disposition is RunResumeDisposition.REJECT
    assert incompatible.reason_code is RunResumeReasonCode.WORKFLOW_VERSION_INCOMPATIBLE


def test_coordinator_resumes_only_active_workflow_and_updates_run_index(fake_executor):
    workflow = _workflow()
    checkpoint_store = InMemoryCheckpointStore()
    ids = count()

    def checkpoint_id(prefix: str) -> str:
        return f"{prefix}-{next(ids)}"

    interrupted = asyncio.run(
        WorkflowExecutor().execute(
            workflow,
            ExecutionContext(workflow_id=workflow.id),
            checkpoint_request=_checkpoint_request(
                checkpoint_store,
                checkpoint_id,
                interrupt_after_stage_id="stage-1",
            ),
        )
    )
    assert not interrupted.success
    checkpoint = checkpoint_store.latest("run-1")
    assert checkpoint is not None
    assert checkpoint.active_stage_id == "stage-2"

    index = _index(checkpoint.checkpoint_id)
    run_store = InMemoryRunResumeStore()
    run_store.save(index)
    coordinator = RunResumeCoordinator(
        run_store=run_store,
        checkpoint_store=checkpoint_store,
        workflows={"wf.b": workflow},
        clock=lambda: "2026-08-06T00:02:00Z",
    )

    resumed = asyncio.run(
        coordinator.resume_active(
            "run-1",
            ExecutionContext(workflow_id=workflow.id),
        )
    )

    assert resumed.decision.selected_workflow_id == "wf.b"
    assert resumed.execution_result is not None
    assert resumed.execution_result.success, resumed.execution_result.error
    assert fake_executor.calls == ["stage-1", "stage-2"]
    latest = run_store.get("run-1")
    assert latest is not None
    assert latest.completed_workflow_ids == ("wf.a", "wf.b")
    assert latest.active_workflow_id == ""
    assert latest.active_checkpoint_id == ""
    assert latest.workflow("wf.b").status is RunWorkflowStatus.COMPLETED


def test_coordinator_rejects_missing_active_checkpoint(fake_executor):
    index = _index("cp-missing")
    run_store = InMemoryRunResumeStore()
    run_store.save(index)
    coordinator = RunResumeCoordinator(
        run_store=run_store,
        checkpoint_store=InMemoryCheckpointStore(),
        workflows={"wf.b": _workflow()},
    )

    result = asyncio.run(
        coordinator.resume_active(
            "run-1",
            ExecutionContext(workflow_id="wf.b"),
        )
    )
    assert result.execution_result is None
    assert result.decision.reason_code is RunResumeReasonCode.CHECKPOINT_NOT_FOUND
    assert fake_executor.calls == []


def test_pending_activation_is_atomic_idempotent_and_revision_safe():
    store = InMemoryRunResumeStore()
    initial = _pending_abc_index()
    store.save(initial)

    activated = store.activate_workflow(
        "run-abc",
        "wf.a",
        expected_revision=0,
        attempt_id="attempt-a",
    )
    assert activated.revision == 1
    assert activated.active_workflow_id == "wf.a"
    assert activated.active_checkpoint_id == ""
    assert activated.pending_workflow_ids == ("wf.b", "wf.c")
    assert activated.workflow("wf.a").status is RunWorkflowStatus.RUNNING
    assert activated.workflow("wf.a").activation_attempt_id == "attempt-a"

    retried = store.activate_workflow(
        "run-abc",
        "wf.a",
        expected_revision=0,
        attempt_id="attempt-a",
    )
    assert retried.to_dict() == activated.to_dict()

    with pytest.raises(RunResumeActivationError) as competing_attempt:
        store.activate_workflow(
            "run-abc",
            "wf.a",
            expected_revision=1,
            attempt_id="attempt-a-2",
        )
    assert competing_attempt.value.code == "ACTIVATION_ATTEMPT_CONFLICT"

    with pytest.raises(RunResumeActivationError) as competing_workflow:
        store.activate_workflow(
            "run-abc",
            "wf.b",
            expected_revision=1,
            attempt_id="attempt-b",
        )
    assert competing_workflow.value.code == "ACTIVE_WORKFLOW_EXISTS"

    stale_store = InMemoryRunResumeStore()
    stale_store.save(_pending_abc_index(run_id="run-stale"))
    with pytest.raises(RunResumeActivationError) as stale_revision:
        stale_store.activate_workflow(
            "run-stale",
            "wf.a",
            expected_revision=9,
            attempt_id="attempt-a",
        )
    assert stale_revision.value.code == "REVISION_CONFLICT"
    unchanged = stale_store.get("run-stale")
    assert unchanged is not None
    assert unchanged.active_workflow_id == ""
    assert unchanged.pending_workflow_ids == ("wf.a", "wf.b", "wf.c")


def test_activation_failure_before_commit_leaves_pending_and_completed_is_terminal():
    store = InMemoryRunResumeStore()
    initial = _pending_abc_index(run_id="run-before-commit")
    store.save(initial)

    with pytest.raises(RunResumeActivationError):
        store.activate_workflow(
            "run-before-commit",
            "wf.a",
            expected_revision=1,
            attempt_id="attempt-a",
        )
    before_commit = store.get("run-before-commit")
    assert before_commit is not None
    assert before_commit.to_dict() == initial.to_dict()

    activated = store.activate_workflow(
        "run-before-commit",
        "wf.a",
        expected_revision=0,
        attempt_id="attempt-a",
    )
    completed = activated.complete_active(
        "cp-a-final",
        parent_digest=run_index_digest(activated),
    )
    store.save(completed)
    with pytest.raises(RunResumeActivationError) as completed_error:
        store.activate_workflow(
            "run-before-commit",
            "wf.a",
            expected_revision=completed.revision,
            attempt_id="attempt-a-again",
        )
    assert completed_error.value.code == "WORKFLOW_ALREADY_COMPLETED"


def test_activation_commit_before_executor_crash_resumes_active_after_store_restart(
    fake_executor,
    tmp_path,
):
    path = tmp_path / "run-resume-crash-window.json"
    store = JsonRunResumeStore(path)
    initial = _pending_abc_index(run_id="run-crash")
    store.save(initial)
    committed = store.activate_workflow(
        "run-crash",
        "wf.a",
        expected_revision=0,
        attempt_id="attempt-a",
    )
    assert committed.active_workflow_id == "wf.a"
    assert committed.workflow("wf.a").checkpoint_id == ""

    restarted_store = JsonRunResumeStore(path)
    rehydrated = restarted_store.get("run-crash")
    assert rehydrated is not None
    assert rehydrated.active_workflow_id == "wf.a"
    assert rehydrated.pending_workflow_ids == ("wf.b", "wf.c")

    workflow = _one_stage_workflow("wf.a", "a-stage", output_type="spec")
    coordinator = RunResumeCoordinator(
        run_store=restarted_store,
        checkpoint_store=InMemoryCheckpointStore(),
        workflows={workflow.id: workflow},
        clock=lambda: "2026-08-06T00:03:00Z",
    )
    resumed = asyncio.run(
        coordinator.execute_or_resume(
            "run-crash",
            lambda selected: ExecutionContext(workflow_id=selected.id),
            attempt_id="attempt-a-retry",
        )
    )

    assert resumed.execution_result is not None
    assert resumed.execution_result.success
    assert fake_executor.calls == ["a-stage"]
    latest = restarted_store.get("run-crash")
    assert latest is not None
    assert latest.active_workflow_id == ""
    assert latest.completed_workflow_ids == ("wf.a",)
    assert latest.workflow("wf.a").checkpoint_id
    assert latest.revision == 2


def test_pending_activation_rejects_missing_or_changed_upstream_artifact():
    missing_store = InMemoryRunResumeStore()
    missing = _completed_a_pending_b_index()
    missing_store.save(missing)
    with pytest.raises(RunResumeActivationError) as missing_error:
        missing_store.activate_workflow(
            "run-ab",
            "wf.b",
            expected_revision=0,
            attempt_id="attempt-b",
        )
    assert missing_error.value.code == "UPSTREAM_ARTIFACT_MISSING"

    changed_store = InMemoryRunResumeStore()
    changed = _completed_a_pending_b_index(
        artifact=RunArtifactFact(
            artifact_id="a-stage-0",
            producer_workflow_id="wf.a",
            digest="wrong-digest",
        )
    )
    changed_store.save(changed)
    with pytest.raises(RunResumeActivationError) as changed_error:
        changed_store.activate_workflow(
            "run-ab",
            "wf.b",
            expected_revision=0,
            attempt_id="attempt-b",
        )
    assert changed_error.value.code == "UPSTREAM_ARTIFACT_CHANGED"


def test_a_to_b_to_c_resumes_once_and_publishes_artifact_facts(fake_executor):
    run_store = InMemoryRunResumeStore()
    run_store.save(_pending_abc_index())
    checkpoint_store = InMemoryCheckpointStore()
    workflows = {
        "wf.a": _one_stage_workflow("wf.a", "a-stage", output_type="spec"),
        "wf.b": _one_stage_workflow("wf.b", "b-stage", output_type="solution"),
        "wf.c": _one_stage_workflow("wf.c", "c-stage", output_type="report"),
    }
    coordinator = RunResumeCoordinator(
        run_store=run_store,
        checkpoint_store=checkpoint_store,
        workflows=workflows,
        clock=lambda: "2026-08-06T00:04:00Z",
    )

    for attempt_id in ("attempt-a", "attempt-b", "attempt-c"):
        execution = asyncio.run(
            coordinator.execute_or_resume(
                "run-abc",
                lambda selected: ExecutionContext(workflow_id=selected.id),
                attempt_id=attempt_id,
            )
        )
        assert execution.execution_result is not None
        assert execution.execution_result.success, execution.execution_result.error

    latest = run_store.get("run-abc")
    assert latest is not None
    assert latest.completed_workflow_ids == ("wf.a", "wf.b", "wf.c")
    assert latest.active_workflow_id == ""
    assert latest.pending_workflow_ids == ()
    assert [item.status for item in latest.workflows] == [
        RunWorkflowStatus.COMPLETED,
        RunWorkflowStatus.COMPLETED,
        RunWorkflowStatus.COMPLETED,
    ]
    assert [item.artifact_id for item in latest.artifacts] == [
        "a-stage-0",
        "b-stage-0",
        "c-stage-0",
    ]
    assert fake_executor.calls == ["a-stage", "b-stage", "c-stage"]

    terminal = asyncio.run(
        coordinator.execute_or_resume(
            "run-abc",
            lambda selected: ExecutionContext(workflow_id=selected.id),
            attempt_id="attempt-after-complete",
        )
    )
    assert terminal.execution_result is None
    assert terminal.decision.reason_code is RunResumeReasonCode.RUN_COMPLETED
    assert fake_executor.calls == ["a-stage", "b-stage", "c-stage"]


def test_a_to_b_to_c_resumes_interrupted_active_b_without_reactivation(fake_executor):
    run_store = InMemoryRunResumeStore()
    run_store.save(_pending_abc_index(require_artifacts=False))
    checkpoint_store = InMemoryCheckpointStore()
    workflows = {
        "wf.a": _one_stage_workflow("wf.a", "a-stage", output_type=""),
        "wf.b": _one_stage_workflow("wf.b", "b-stage", output_type=""),
        "wf.c": _one_stage_workflow("wf.c", "c-stage", output_type=""),
    }
    workflow_executor = InterruptOnceWorkflowExecutor("wf.b")
    coordinator = RunResumeCoordinator(
        run_store=run_store,
        checkpoint_store=checkpoint_store,
        workflows=workflows,
        workflow_executor=workflow_executor,
        clock=lambda: "2026-08-06T00:05:00Z",
    )

    first_a = asyncio.run(
        coordinator.execute_or_resume(
            "run-abc",
            lambda selected: ExecutionContext(workflow_id=selected.id),
            attempt_id="attempt-a",
        )
    )
    assert first_a.execution_result is not None and first_a.execution_result.success

    first_b = asyncio.run(
        coordinator.execute_or_resume(
            "run-abc",
            lambda selected: ExecutionContext(workflow_id=selected.id),
            attempt_id="attempt-b",
        )
    )
    assert first_b.execution_result is not None
    assert not first_b.execution_result.success
    active_after_interrupt = run_store.get("run-abc")
    assert active_after_interrupt is not None
    assert active_after_interrupt.active_workflow_id == "wf.b"
    assert active_after_interrupt.revision == 4

    resumed_b = asyncio.run(
        coordinator.execute_or_resume(
            "run-abc",
            lambda selected: ExecutionContext(workflow_id=selected.id),
            attempt_id="attempt-b-retry",
        )
    )
    assert resumed_b.execution_result is not None and resumed_b.execution_result.success
    resumed_c = asyncio.run(
        coordinator.execute_or_resume(
            "run-abc",
            lambda selected: ExecutionContext(workflow_id=selected.id),
            attempt_id="attempt-c",
        )
    )
    assert resumed_c.execution_result is not None and resumed_c.execution_result.success
    latest = run_store.get("run-abc")
    assert latest is not None
    assert latest.completed_workflow_ids == ("wf.a", "wf.b", "wf.c")
    assert latest.active_workflow_id == ""
    assert fake_executor.calls == ["a-stage", "b-stage", "c-stage"]
