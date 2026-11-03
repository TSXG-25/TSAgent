"""R-C regressions: lenient intent wire parsing + workflow selector routing.

Covers two real CLI failures observed after the big refactor:

A) An intent Provider reply that omits optional wire keys (acceptance_criteria
   at goal level and inside a delivery requirement, write_targets, …) must not
   crash the run.  Optional keys parse leniently; only missing required keys
   degrade to INTENT_CONTRACT_INVALID.  Restored goal_state dicts with the
   pre-refactor shape must also restore without a bare KeyError escaping as
   RUNTIME_EXCEPTION — and when a runtime exception does happen, the durable
   failure evidence must carry a sanitized detail.

B) A generic "写个计算器" request must decline the narrow code_generation
   Workflow (its required binding question_path is not derivable) and fall
   back to the generic Planner, instead of ASK -> BLOCKED.
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

from agent.goal import DeliveryRequirement, GoalState
from tests.test_ha_r1_harness import (
    call_tool, intent, run_turn, service_at, setup_provider,
)


# ── A) intent wire contract leniency ────────────────────────────────────


def _engine_with_payload(payload):
    from agent.cognition.intent_engine import IntentEngine

    class Provider:
        async def ainvoke(self, _messages):
            return SimpleNamespace(
                content=json.dumps(payload, ensure_ascii=False)
            )

    engine = IntentEngine()
    engine._llm = Provider()
    return engine


def _analyze(engine, query="写个计算器"):
    from agent.cognition.cognitive_context import CognitiveContext

    return asyncio.run(engine.analyze_async(CognitiveContext(query=query)))


def test_a_sparse_intent_wire_payload_parses_without_optional_keys():
    """Real-model reply shape: only required keys present."""
    payload = {
        "domain": "开发",
        "action": "code",
        "requested_outcomes": ["USER_VISIBLE_OUTPUT", "FILE_MUTATION"],
        "artifact_required": True,
        "delivery_requirements": [
            {"id": "calculator", "kind": "file", "quantity": 1, "target": ""}
        ],
        "prohibited_effects": [],
        "resolved_request": "写一个计算器",
        "conversation_relation": "new_request",
    }

    result = _analyze(_engine_with_payload(payload))

    assert result.failure_code == "", result.failure_message
    assert result.artifact_required is True
    assert result.requires_execution is True
    assert len(result.delivery_requirements) == 1
    assert result.delivery_requirements[0].acceptance_criteria == ()
    assert result.acceptance_criteria == ()
    assert result.resolved_request == "写一个计算器"


def test_a_missing_optional_keys_default_to_sane_values():
    """Every optional key omitted at once still parses."""
    payload = {
        "requested_outcomes": ["USER_VISIBLE_OUTPUT"],
        "artifact_required": False,
        "delivery_requirements": [],
        "prohibited_effects": [],
    }

    result = _analyze(_engine_with_payload(payload))

    assert result.failure_code == "", result.failure_message
    assert result.conversation_relation == "new_request"
    assert result.resolved_request == ""
    assert result.freshness_required is False
    assert result.source_grounding_required is False
    assert result.write_targets == ()
    assert result.external_effects == ()


def test_a_missing_required_wire_keys_fail_as_contract_not_crash():
    """Required keys stay strict: a clean contract failure, never a KeyError."""
    base = {
        "requested_outcomes": ["USER_VISIBLE_OUTPUT"],
        "artifact_required": False,
        "delivery_requirements": [],
        "prohibited_effects": [],
    }
    missing_artifact = {k: v for k, v in base.items() if k != "artifact_required"}
    missing_outcomes = {k: v for k, v in base.items() if k != "requested_outcomes"}
    missing_deliveries = {k: v for k, v in base.items() if k != "delivery_requirements"}
    missing_prohibited = {k: v for k, v in base.items() if k != "prohibited_effects"}

    for payload in (
        missing_artifact, missing_outcomes, missing_deliveries, missing_prohibited,
    ):
        result = _analyze(_engine_with_payload(payload))
        assert result.failure_code == "INTENT_CONTRACT_INVALID", payload

    with pytest.raises(ValueError, match="quantity"):
        DeliveryRequirement.from_dict({"id": "x", "kind": "file", "target": ""})
    with pytest.raises(ValueError, match="target"):
        DeliveryRequirement.from_dict({"id": "x", "kind": "file", "quantity": 1})
    with pytest.raises(ValueError):
        DeliveryRequirement.from_dict({"kind": "file", "quantity": 1, "target": ""})


def test_a_restored_goal_state_without_criterion_field_does_not_crash():
    """Pre-refactor persisted goal_state: delivery requirement dicts lack
    acceptance_criteria.  GoalState.from_dict is called on every planning
    round (_advance_goal_round) — a bare KeyError here escaped as
    RUNTIME_EXCEPTION before the intent print."""
    legacy_goal = {
        "objective": "写个计算器",
        "status": "ACTIVE",
        "requested_outcomes": ["USER_VISIBLE_OUTPUT", "FILE_MUTATION"],
        "delivery_requirements": [
            {"id": "calculator", "kind": "file", "quantity": 1, "target": ""}
        ],
    }

    goal = GoalState.from_dict(legacy_goal)

    assert goal.delivery_requirements[0].id == "calculator"
    assert goal.delivery_requirements[0].acceptance_criteria == ()


def test_a_sparse_intent_payload_completes_through_service(monkeypatch, tmp_path):
    """E2E: intent reply omitting optional keys no longer fails the run."""
    request = "写个计算器"
    semantics = intent(request, artifact=True, outcomes=["FILE_MUTATION"])
    for key in (
        "acceptance_criteria", "write_targets", "external_effects",
        "search_query", "search_timeliness", "context_needs", "memory_action",
        "freshness_required", "source_grounding_required", "summary",
        "entities", "confidence",
    ):
        semantics.pop(key, None)
    for requirement in semantics["delivery_requirements"]:
        requirement.pop("acceptance_criteria", None)
    provider = setup_provider(monkeypatch, [
        (request, semantics),
        (request, call_tool("write_file", path="output/calculator.py",
                            content="def add(a, b):\n    return a + b\n")),
        ("calculator.py", "已创建 output/calculator.py"),
    ])

    async def scenario():
        service, _ = service_at(tmp_path)
        try:
            snapshot, _ = await run_turn(service, request, 1)
            assert snapshot.status.value == "COMPLETED", snapshot
            assert (tmp_path / "output/calculator.py").is_file()
        finally:
            await service.close()

    asyncio.run(scenario())
    assert not provider.replies


def test_a_runtime_exception_records_sanitized_failure_detail(monkeypatch):
    """A stage exception persists type+message into the durable evidence,
    so RUNTIME_EXCEPTION stays diagnosable without exposing a traceback."""
    from agent.failure import FailurePolicy
    from agent.runtime import UniversalAgent

    class Orchestrator:
        replan_count = 0

        def reset_timings(self):
            pass

        def get_timings(self):
            return {}

        async def plan(self, **_kw):
            raise KeyError("acceptance_criteria")

        async def finalize(self, **kw):
            return str(kw.get("best_answer") or "recovered")

    agent = UniversalAgent.__new__(UniversalAgent)
    agent._memory_view = SimpleNamespace()
    agent._memory_namespace = "rc-user"
    agent._pending_execution_target = ""
    agent._run_context = SimpleNamespace(
        run_id="run-rc",
        repository_indexer=None,
        event_bus=SimpleNamespace(emit=lambda *_a, **_k: None),
        cancellation_view=SimpleNamespace(
            raise_if_requested=lambda *_a, **_k: None),
    )
    agent._timings = {}
    agent._wall_total_seconds = 0.0
    agent._session_context = SimpleNamespace(
        conversation_tracker=SimpleNamespace(update=lambda **_k: None),
        memory_scope="user",
    )
    agent.orchestrator = Orchestrator()
    agent._failure_policy = FailurePolicy()
    monkeypatch.setattr(agent, "_print_timing_summary", lambda: {})

    answer = asyncio.run(agent._run_in_context("写个计算器"))

    assert answer
    evidence = agent.last_run_evidence
    assert evidence["failure_code"] == "RUNTIME_EXCEPTION"
    assert evidence["terminal_status"] == "FAILED_TERMINAL"
    assert evidence["failure_detail"].startswith("KeyError:")
    assert "acceptance_criteria" in evidence["failure_detail"]


# ── B) workflow routing contract ─────────────────────────────────────────
#
# The model now selects a Workflow directly through the run_workflow tool;
# there is no selector LLM.  The durable binding checks (required bindings,
# declared input-file existence) still gate the call.


def test_b_code_generation_projection_states_question_file_scope():
    """The catalog metadata is the only applicability signal the model gets."""
    from agent.workflow.projection import WorkflowDefinitionProjection
    from workflows.code_generation import code_generation_workflow

    projection = WorkflowDefinitionProjection.from_workflow(code_generation_workflow)

    assert "question_path" in projection.required_bindings
    assert "题目" in projection.description
    assert "不适用" in projection.description or "仅" in projection.description


# ── B2) fabricated input-path bindings are rejected at the tool boundary ──
#
# Real-ollama regression: a fabricated question_path (output/question.md,
# absent) used to doom the run on a dead read.  Declared input-file
# existence is verified inside run_workflow where the Run workspace is
# reachable → WORKFLOW_INPUT_MISSING is a tool error, never a doomed
# Workflow run.


def test_b2_code_generation_declares_question_path_as_input_file():
    """The capability metadata marks input bindings vs. write targets."""
    import tools.filesystem  # noqa: F401 — registers filesystem tools
    from agent.workflow.projection import WorkflowDefinitionProjection
    from workflows.code_generation import code_generation_workflow

    projection = WorkflowDefinitionProjection.from_workflow(code_generation_workflow)

    assert projection.required_input_paths == ("question_path",)
    # output_path is a write target: absence must never be checked.
    assert "output_path" not in projection.required_input_paths


def test_b2_missing_input_files_are_reported_only_for_declared_bindings(tmp_path):
    """Unit: the run_workflow helper flags missing question_path only."""
    import tools.filesystem  # noqa: F401 — registers filesystem tools
    from agent.event_bus import EventBus
    from agent.loop.workflow_tool import missing_required_input_files
    from agent.services.workspace_service import WorkspaceService
    from agent.workflow.projection import WorkflowDefinitionProjection
    from workflows.code_generation import code_generation_workflow

    projection = WorkflowDefinitionProjection.from_workflow(code_generation_workflow)
    workspace = WorkspaceService.scoped(
        tmp_path, event_bus=EventBus(), build_index=False,
    )
    try:
        bindings = {
            "question_path": "output/question.md",
            "output_path": "output/solution.py",
        }
        assert missing_required_input_files(
            projection, bindings, workspace,
        ) == ["question_path=output/question.md"]

        workspace.write_text("input/question.md", "把 100 USD 换成 CNY")
        bindings["question_path"] = "input/question.md"
        # output/solution.py still does not exist — not an input binding.
        assert missing_required_input_files(
            projection, bindings, workspace,
        ) == []

        # No bound workspace → nothing to resolve → nothing reported.
        assert missing_required_input_files(projection, bindings, None) == []
    finally:
        workspace.close()


def test_b2_missing_question_file_is_a_tool_error_not_a_doomed_run(
    monkeypatch, tmp_path,
):
    """E2E: the model calls run_workflow with a fabricated question_path;
    the file is absent → WORKFLOW_INPUT_MISSING comes back as a tool error
    and the model completes the request itself instead."""
    import tools.filesystem  # noqa: F401 — workflow capability check
    request = "写个汇率计算器"
    provider = setup_provider(monkeypatch, [
        (request, intent(request, artifact=True, outcomes=["FILE_MUTATION"])),
        (request, call_tool("run_workflow", workflow_id="code_generation",
                            bindings={
                                "question_path": "output/question.md",
                                "output_path": "output/solution.py",
                            })),
        ("WORKFLOW_INPUT_MISSING", call_tool(
            "write_file", path="output/calculator.py",
            content="def add(a, b):\n    return a + b\n")),
        ("calculator.py", "已创建 output/calculator.py"),
    ])

    async def scenario():
        service, _ = service_at(tmp_path)
        try:
            snapshot, _ = await run_turn(service, request, 1)
            assert snapshot.status.value == "COMPLETED", snapshot
            assert (tmp_path / "output/calculator.py").is_file()
            # The workflow never ran: no solution file was produced.
            assert not (tmp_path / "output/solution.py").exists()
        finally:
            await service.close()

    asyncio.run(scenario())
    assert not provider.replies


def test_b2_seeded_question_file_runs_workflow_through_tool(
    monkeypatch, tmp_path,
):
    """E2E: run_workflow with a real question_path executes code_generation
    end to end and writes the solution through the durable coordinator."""
    import tools.filesystem  # noqa: F401 — workflow capability check
    request = "解答 input/question.md 并把答案保存为 output/solution.py"
    provider = setup_provider(monkeypatch, [
        (request, intent(
            request, artifact=True,
            outcomes=["FILE_READ", "FILE_MUTATION"],
        )),
        (request, call_tool("run_workflow", workflow_id="code_generation",
                            bindings={
                                "question_path": "input/question.md",
                                "output_path": "output/solution.py",
                            })),
        ("你是一个算法设计助手", "直接按汇率做乘法换算。"),
        (
            "你是一个 Python 代码生成助手",
            "def convert(amount, rate):\n"
            "    return amount * rate\n"
            "\n"
            "\n"
            "def main():\n"
            "    print(convert(100, 7.2))\n"
            "\n"
            "\n"
            "if __name__ == '__main__':\n"
            "    main()\n",
        ),
        ("solution.py", "已完成解答并写入 output/solution.py"),
    ])
    monkeypatch.setattr("agent.executor.llm_executor.llm", provider)
    (tmp_path / "input").mkdir()
    (tmp_path / "input/question.md").write_text(
        "把 100 USD 按汇率 7.2 换成 CNY", encoding="utf-8",
    )

    async def scenario():
        service, _ = service_at(tmp_path)
        try:
            snapshot, _ = await run_turn(service, request, 1)
            assert snapshot.status.value == "COMPLETED", snapshot
            solution = tmp_path / "output/solution.py"
            assert solution.is_file()
            assert "def convert" in solution.read_text()
            # The workflow's verified artifact is disclosed as a local file.
            assert "output/solution.py" in snapshot.output.text
        finally:
            await service.close()

    asyncio.run(scenario())
    assert not provider.replies


# ── C) authorization boundary feeds back as tool errors ──────────────────


def test_c_toolset_is_gated_by_authorized_outcomes():
    """The loop catalog only exposes effects the Intent authorized."""
    from agent.cognition.effect_authorization import EffectAuthorization
    from agent.cognition.execution_need import RequestedOutcome
    from agent.loop.toolset import build_toolset

    mutation = EffectAuthorization(
        requested_outcomes=(
            RequestedOutcome.USER_VISIBLE_OUTPUT,
            RequestedOutcome.FILE_MUTATION,
        ),
        write_scopes=("output/",),
    )
    names = set(build_toolset(mutation))
    assert "write_file" in names
    assert "edit_file" in names
    assert "read_file" in names
    # CODE_EXECUTION / COMMAND_EXECUTION were never authorized.
    assert "shell" not in names
    assert "run_python" not in names

    chat = EffectAuthorization(
        requested_outcomes=(RequestedOutcome.USER_VISIBLE_OUTPUT,),
        write_scopes=(),
    )
    names = set(build_toolset(chat))
    assert "write_file" not in names
    assert "edit_file" not in names
    assert "read_file" in names


def test_c_unauthorized_tool_call_retries_with_feedback_then_completes(
    monkeypatch, tmp_path,
):
    """E2E: a write outside the authorized scope is denied per call
    (EFFECT_SCOPE_VIOLATION); the model then uses an in-scope path and the
    run completes — the denied side effect never happens."""
    request = "写个计算器"
    provider = setup_provider(monkeypatch, [
        (request, intent(request, artifact=True, outcomes=["FILE_MUTATION"])),
        (request, call_tool("write_file", path="agent/evil.py",
                            content="x = 1\n")),
        ("EFFECT_SCOPE_VIOLATION", call_tool(
            "write_file", path="output/calculator.py",
            content="def add(a, b):\n    return a + b\n")),
        ("calculator.py", "已创建 output/calculator.py"),
    ])

    async def scenario():
        service, _ = service_at(tmp_path)
        try:
            snapshot, _ = await run_turn(service, request, 1)
            assert snapshot.status.value == "COMPLETED", snapshot
            assert (tmp_path / "output/calculator.py").is_file()
            # The out-of-scope write never happened.
            assert not (tmp_path / "agent/evil.py").exists()
        finally:
            await service.close()

    asyncio.run(scenario())
    assert not provider.replies


def test_c_persistent_unauthorized_calls_cannot_complete(
    monkeypatch, tmp_path,
):
    """A model that only produces scope violations and prose claims cannot
    complete the run; no side effect ever happens."""
    monkeypatch.setenv("TSAGENT_MAX_GOAL_ROUNDS", "2")
    request = "写个计算器"
    provider = setup_provider(monkeypatch, [
        (request, intent(request, artifact=True, outcomes=["FILE_MUTATION"])),
        (request, call_tool("write_file", path="agent/evil.py",
                            content="x = 1\n")),
        ("EFFECT_SCOPE_VIOLATION", "已创建 output/calculator.py"),
        ("deliverable", "已创建 output/calculator.py"),
    ])

    async def scenario():
        service, store = service_at(tmp_path)
        try:
            snapshot, _ = await run_turn(service, request, 1)
            assert snapshot.status.value != "COMPLETED", snapshot
            assert not (tmp_path / "agent/evil.py").exists()
            assert not (tmp_path / "output/calculator.py").exists()
        finally:
            await service.close()

    asyncio.run(scenario())
    assert not provider.replies
