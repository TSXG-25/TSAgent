"""v2.2B single-Workflow Checkpoint/Resume integration tests."""
from __future__ import annotations

import asyncio
from itertools import count

import pytest

from agent.checkpoint import (
    CheckpointStatus,
    ExternalStateGuard,
    GuardStatus,
    InMemoryCheckpointStore,
    ResumeAction,
    ResumeContext,
    SideEffectState,
    TaskEffectRecord,
    WorkflowCheckpointRequest,
    append_checkpoint,
)
from agent.executor.contract import executor_factory
from agent.executor.executors.workflow import WorkflowExecutor
from agent.workflow import (
    ExecutionContext,
    ExecutionResult,
    ExecutionSpec,
    ExecutorType,
    Stage,
    Workflow,
)


class RecordingExecutor:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail_ids: set[str] = set()

    async def execute(self, target, context):
        self.calls.append(target.id)
        if target.id in self.fail_ids:
            return ExecutionResult(
                success=False,
                error=f"fake failure: {target.id}",
                metadata={"executor": "fake"},
            )
        return ExecutionResult(
            success=True,
            outputs={"text": f"done:{target.id}"},
            metadata={"executor": "fake"},
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


def _workflow(*, first_description: str = "write output/checkpoint.txt", replayable: bool = False):
    return Workflow(
        id="checkpoint-workflow",
        version="1.0.0",
        description="checkpoint workflow",
        stages=[
            Stage(
                id="stage-1",
                execution=ExecutionSpec(executor=ExecutorType.TOOL),
                description=first_description,
            ),
            Stage(
                id="stage-2",
                execution=ExecutionSpec(executor=ExecutorType.LLM),
                description="explain the result",
                idempotent=replayable,
            ),
        ],
    )


def _request_factory():
    ids = count()

    def make_id(prefix: str) -> str:
        return f"{prefix}-{next(ids)}"

    def make_request(store, **updates):
        values = {
            "store": store,
            "run_id": "run-workflow-1",
            "session_id": "session-1",
            "conversation_id": "conversation-1",
            "user_scope": "user-1",
            "target_summary": "checkpoint workflow",
            "clock": lambda: "2026-08-05T00:00:00Z",
            "checkpoint_id_factory": make_id,
        }
        values.update(updates)
        return WorkflowCheckpointRequest(**values)

    return make_request


def _resume_context(*, action=None, external_state_evidence=()):
    return ResumeContext(
        workflow_id="checkpoint-workflow",
        workflow_version="1.0.0",
        plan_version="1.0",
        requested_action=action,
        requested_target="checkpoint workflow",
        candidate_run_ids=("run-workflow-1",),
        external_state_evidence=external_state_evidence,
    )


def _run(workflow, context, request):
    return asyncio.run(
        WorkflowExecutor().execute(
            workflow,
            context,
            checkpoint_request=request,
        )
    )


def test_exact_resume_skips_completed_stage_and_completes_run(fake_executor):
    workflow = _workflow()
    context = ExecutionContext(workflow_id=workflow.id)
    store = InMemoryCheckpointStore()
    make_request = _request_factory()
    request = make_request(store, interrupt_after_stage_id="stage-1")

    interrupted = _run(workflow, context, request)
    checkpoint = store.latest(request.run_id)

    assert not interrupted.success
    assert checkpoint is not None
    assert checkpoint.status is CheckpointStatus.SUSPENDED
    assert checkpoint.active_stage_id == "stage-2"
    assert checkpoint.completed_stage_ids == ("stage-1",)
    assert fake_executor.calls == ["stage-1"]

    resumed = _run(
        workflow,
        context,
        make_request(
            store,
            checkpoint=checkpoint,
            resume_context=_resume_context(),
        ),
    )
    latest = store.latest(request.run_id)

    assert resumed.success, resumed.error
    assert fake_executor.calls == ["stage-1", "stage-2"]
    assert latest is not None
    assert latest.status is CheckpointStatus.COMPLETED
    assert latest.completed_stage_ids == ("stage-1", "stage-2")
    assert [item.sequence_number for item in store.history(request.run_id)] == list(range(7))


def test_idempotent_stage_can_be_replayed_without_replaying_previous_stage(fake_executor):
    workflow = _workflow(replayable=True)
    context = ExecutionContext(workflow_id=workflow.id)
    store = InMemoryCheckpointStore()
    make_request = _request_factory()
    request = make_request(store, interrupt_after_stage_id="stage-1")

    _run(workflow, context, request)
    checkpoint = store.latest(request.run_id)
    assert checkpoint is not None

    replayed = _run(
        workflow,
        context,
        make_request(
            store,
            checkpoint=checkpoint,
            resume_context=_resume_context(
                action=ResumeAction.REPLAY_FROM_STAGE,
            ),
        ),
    )

    assert replayed.success, replayed.error
    assert fake_executor.calls == ["stage-1", "stage-2"]
    assert replayed.metadata["resume_decision"]["action"] == "REPLAY_FROM_STAGE"


def test_non_idempotent_replay_is_rejected_before_executor(fake_executor):
    workflow = _workflow(first_description="read input", replayable=False)
    context = ExecutionContext(workflow_id=workflow.id)
    store = InMemoryCheckpointStore()
    make_request = _request_factory()
    request = make_request(store, interrupt_after_stage_id="stage-1")

    _run(workflow, context, request)
    checkpoint = store.latest(request.run_id)
    assert checkpoint is not None
    result = _run(
        workflow,
        context,
        make_request(
            store,
            checkpoint=checkpoint,
            resume_context=_resume_context(
                action=ResumeAction.REPLAY_FROM_STAGE,
            ),
        ),
    )

    assert not result.success
    assert result.metadata["resume_decision"]["reason_code"] == "NON_IDEMPOTENT_STAGE"
    assert fake_executor.calls == ["stage-1"]


def test_committed_side_effect_blocks_duplicate_active_task(fake_executor):
    workflow = _workflow()
    context = ExecutionContext(workflow_id=workflow.id)
    store = InMemoryCheckpointStore()
    make_request = _request_factory()
    request = make_request(store, interrupt_after_stage_id="stage-1")

    _run(workflow, context, request)
    checkpoint = store.latest(request.run_id)
    assert checkpoint is not None
    unsafe = append_checkpoint(
        checkpoint,
        checkpoint_id="run-workflow-1-manual-committed",
        updated_at="2026-08-05T00:01:00Z",
        active_stage_id="stage-1",
        active_task_id="stage-1",
        completed_stage_ids=(),
        completed_task_ids=(),
    )
    store.save(unsafe)

    result = _run(
        workflow,
        context,
        make_request(
            store,
            checkpoint=unsafe,
            resume_context=_resume_context(),
        ),
    )

    assert not result.success
    assert result.metadata["resume_decision"]["disposition"] == "REQUIRE_CLARIFICATION"
    assert result.metadata["resume_decision"]["reason_code"] == "DUPLICATE_SIDE_EFFECT"
    assert fake_executor.calls == ["stage-1"]


def test_unknown_side_effect_requires_clarification_and_never_executes(fake_executor):
    workflow = _workflow()
    context = ExecutionContext(workflow_id=workflow.id)
    store = InMemoryCheckpointStore()
    make_request = _request_factory()
    request = make_request(store, interrupt_after_stage_id="stage-1")

    _run(workflow, context, request)
    checkpoint = store.latest(request.run_id)
    assert checkpoint is not None
    unknown = append_checkpoint(
        checkpoint,
        checkpoint_id="run-workflow-1-manual-unknown",
        updated_at="2026-08-05T00:02:00Z",
        active_stage_id="stage-1",
        active_task_id="stage-1",
        completed_stage_ids=(),
        completed_task_ids=(),
        task_effect_records=(TaskEffectRecord(
            task_id="stage-1",
            idempotency_key="run-workflow-1:stage-1",
            effect_state=SideEffectState.UNKNOWN,
        ),),
        idempotency_keys=("run-workflow-1:stage-1",),
    )
    store.save(unknown)

    result = _run(
        workflow,
        context,
        make_request(
            store,
            checkpoint=unknown,
            resume_context=_resume_context(),
        ),
    )

    assert not result.success
    decision = result.metadata["resume_decision"]
    assert decision["disposition"] == "REQUIRE_CLARIFICATION"
    assert decision["resulting_status"] == "WAITING_USER"
    assert decision["reason_code"] == "UNKNOWN_SIDE_EFFECT"
    assert fake_executor.calls == ["stage-1"]


def test_external_guard_mismatch_rejects_before_workflow_execution(fake_executor):
    workflow = _workflow()
    context = ExecutionContext(workflow_id=workflow.id)
    store = InMemoryCheckpointStore()
    make_request = _request_factory()
    request = make_request(store, interrupt_after_stage_id="stage-1")

    _run(workflow, context, request)
    checkpoint = store.latest(request.run_id)
    assert checkpoint is not None
    guarded = append_checkpoint(
        checkpoint,
        checkpoint_id="run-workflow-1-manual-guard",
        updated_at="2026-08-05T00:03:00Z",
        external_state_guards=(ExternalStateGuard(
            resource_id="output/checkpoint.txt",
            guard_type="FILE_CONTENT_HASH",
            expected_value="old",
            observed_value="old",
            status=GuardStatus.VERIFIED,
        ),),
    )
    store.save(guarded)
    mismatch = ExternalStateGuard(
        resource_id="output/checkpoint.txt",
        guard_type="FILE_CONTENT_HASH",
        expected_value="old",
        observed_value="new",
        status=GuardStatus.MISMATCH,
    )

    result = _run(
        workflow,
        context,
        make_request(
            store,
            checkpoint=guarded,
            resume_context=_resume_context(
                external_state_evidence=(mismatch,),
            ),
        ),
    )

    assert not result.success
    assert result.metadata["resume_decision"]["reason_code"] == "EXTERNAL_STATE_MISMATCH"
    assert fake_executor.calls == ["stage-1"]


def test_failed_stage_creates_recoverable_checkpoint_and_can_resume(fake_executor):
    workflow = _workflow()
    context = ExecutionContext(workflow_id=workflow.id)
    store = InMemoryCheckpointStore()
    make_request = _request_factory()
    request = make_request(store)
    fake_executor.fail_ids.add("stage-1")

    failed = _run(workflow, context, request)
    checkpoint = store.latest(request.run_id)

    assert not failed.success
    assert checkpoint is not None
    assert checkpoint.status is CheckpointStatus.FAILED_RECOVERABLE
    assert checkpoint.failure_event is not None
    assert checkpoint.active_stage_id == "stage-1"

    fake_executor.fail_ids.clear()
    resumed = _run(
        workflow,
        context,
        make_request(
            store,
            checkpoint=checkpoint,
            resume_context=_resume_context(),
        ),
    )

    assert resumed.success, resumed.error
    assert fake_executor.calls == ["stage-1", "stage-1", "stage-2"]
    assert store.latest(request.run_id).status is CheckpointStatus.COMPLETED
