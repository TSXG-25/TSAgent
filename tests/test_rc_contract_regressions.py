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
    intent, run_turn, service_at, setup_provider,
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
        ("用户需求", {"tasks": [
            dict(id="write", verb="write", target="output/calculator.py",
                 target_type="file", goal="创建计算器"),
        ]}),
        ("你是文件生成器", "def add(a, b):\n    return a + b\n"),
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


# ── B) workflow selector routing contract ────────────────────────────────


def _real_workflow_selector(monkeypatch):
    """Undo the conftest decline-stub so the real Provider-backed selector runs."""
    from agent.workflow_selector import WorkflowDecisionSelector

    monkeypatch.setattr(
        "agent.orchestrator.main.WorkflowDecisionSelector",
        WorkflowDecisionSelector,
    )


def test_b_selection_prompt_declines_when_bindings_not_derivable():
    """Contract-level: the prompt must route underivable bindings to DECLINE
    and reserve ASK for genuine workflow-shaped requests."""
    from agent.workflow_selector import WORKFLOW_SELECTION_PROMPT

    prompt = WORKFLOW_SELECTION_PROMPT
    assert "required bindings cannot be fully derived" in prompt
    assert "not a match" in prompt
    assert "decline" in prompt.lower()
    assert "generic Planner can deliver" in prompt
    # ASK is reserved for goals clearly about the Workflow's own task.
    assert "Ask only when the Goal is clearly about" in prompt


def test_b_code_generation_projection_states_question_file_scope():
    """The catalog metadata is the only applicability signal the selector gets."""
    from agent.workflow_selector import WorkflowDefinitionProjection
    from workflows.code_generation import code_generation_workflow

    projection = WorkflowDefinitionProjection.from_workflow(code_generation_workflow)

    assert "question_path" in projection.required_bindings
    assert "题目" in projection.description
    assert "不适用" in projection.description or "仅" in projection.description


def test_b_instantiate_with_derivable_bindings_still_selects():
    """A question-file request can still instantiate code_generation."""
    from agent.workflow_decision import WorkflowDecisionKind
    from agent.workflow_selector import (
        WorkflowContextProjection,
        WorkflowDecisionSelector,
        WorkflowDefinitionProjection,
    )
    from workflows.code_generation import code_generation_workflow

    class Provider:
        async def ainvoke(self, messages):
            return SimpleNamespace(content=json.dumps({
                "kind": "instantiate",
                "workflow_id": "code_generation",
                "bindings": {
                    "question_path": "input/question.docx",
                    "output_path": "output/solution.py",
                },
                "reason": "user supplied a question file to solve",
            }, ensure_ascii=False))

    projection = WorkflowDefinitionProjection.from_workflow(code_generation_workflow)
    selector = WorkflowDecisionSelector(
        provider=Provider(), supports_structured_output=False,
    )
    decision = asyncio.run(selector.select(
        "解答 input/question.docx 并写入 output/solution.py",
        WorkflowContextProjection(
            capabilities=("filesystem.read", "filesystem.write"),
        ),
        (projection,),
    ))

    assert decision.kind is WorkflowDecisionKind.INSTANTIATE
    assert decision.workflow_id == "code_generation"
    assert decision.bindings["question_path"] == "input/question.docx"


def test_b_generic_request_decline_falls_back_to_generic_planner(
    monkeypatch, tmp_path,
):
    """E2E: '写个计算器' reaches the real selector; DECLINE → generic Planner
    completes the delivery instead of blocking."""
    request = "写个计算器"
    _real_workflow_selector(monkeypatch)
    provider = setup_provider(monkeypatch, [
        (request, intent(request, artifact=True, outcomes=["FILE_MUTATION"])),
        ("Workflow Selector", {
            "kind": "decline",
            "workflow_id": "",
            "bindings": {},
            "reason": "generic programming request; question_path is not derivable",
        }),
        ("用户需求", {"tasks": [
            dict(id="write", verb="write", target="output/calculator.py",
                 target_type="file", goal="创建计算器"),
        ]}),
        ("你是文件生成器", "def add(a, b):\n    return a + b\n"),
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
    selector_call = next(c for c in provider.calls if "Workflow Selector" in c)
    assert "question_path" in selector_call


def test_b_genuine_workflow_ask_still_blocks_with_clarification(
    monkeypatch, tmp_path,
):
    """ASK is honored: a workflow-shaped request missing a required input
    still surfaces a clarification instead of silently declining."""
    request = "帮我解答那道题，生成解法文件"
    _real_workflow_selector(monkeypatch)
    provider = setup_provider(monkeypatch, [
        (request, intent(request, outcomes=["FILE_READ", "FILE_MUTATION"])),
        ("Workflow Selector", {
            "kind": "ask",
            "workflow_id": "",
            "bindings": {},
            "reason": "请提供题目文件的具体路径（question_path）",
        }),
    ])

    async def scenario():
        service, _ = service_at(tmp_path)
        try:
            snapshot, _ = await run_turn(service, request, 1)
            assert snapshot.status.value == "BLOCKED", snapshot
            assert "question_path" in snapshot.output.text
        finally:
            await service.close()

    asyncio.run(scenario())
    assert not provider.replies


# ── B2) fabricated input-path bindings are rejected at the Run boundary ──
#
# Real-ollama regression: the Selector instantiated code_generation with a
# fabricated question_path (output/question.md, absent) and the run died on
# the dead read.  Binding completeness is the Selector's contract; input
# file existence is verified at the orchestrator boundary where the Run
# workspace is reachable.  A missing declared input is "not a match" →
# generic Planner, never a doomed Workflow run.


def _question_workflow_decision(question_path="output/question.md"):
    return {
        "kind": "instantiate",
        "workflow_id": "code_generation",
        "bindings": {
            "question_path": question_path,
            "output_path": "output/solution.py",
        },
        "reason": "user supplied a question file to solve",
    }


def test_b2_code_generation_declares_question_path_as_input_file():
    """The capability metadata marks input bindings vs. write targets."""
    import tools.filesystem  # noqa: F401 — registers filesystem tools
    from agent.workflow_selector import WorkflowDefinitionProjection
    from workflows.code_generation import code_generation_workflow

    projection = WorkflowDefinitionProjection.from_workflow(code_generation_workflow)

    assert projection.required_input_paths == ("question_path",)
    # output_path is a write target: absence must never be checked.
    assert "output_path" not in projection.required_input_paths


def test_b2_missing_input_files_are_reported_only_for_declared_bindings(tmp_path):
    """Unit: the orchestrator helper flags missing question_path only."""
    import tools.filesystem  # noqa: F401 — registers filesystem tools
    from agent.event_bus import EventBus
    from agent.orchestrator.planner import _missing_required_input_files
    from agent.services.workspace_service import WorkspaceService
    from agent.workflow_selector import WorkflowDefinitionProjection
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
        assert _missing_required_input_files(
            projection, bindings, workspace,
        ) == ["question_path=output/question.md"]

        workspace.write_text("input/question.md", "把 100 USD 换成 CNY")
        bindings["question_path"] = "input/question.md"
        # output/solution.py still does not exist — not an input binding.
        assert _missing_required_input_files(
            projection, bindings, workspace,
        ) == []

        # No bound workspace → nothing to resolve → nothing reported.
        assert _missing_required_input_files(projection, bindings, None) == []
    finally:
        workspace.close()


def test_b2_instantiate_with_missing_question_file_falls_back_to_planner(
    monkeypatch, tmp_path,
):
    """E2E: Selector INSTANTIATEs a fabricated question_path; the file is
    absent → the orchestrator declines the match and the generic Planner
    completes the request instead of running a doomed read."""
    import tools.filesystem  # noqa: F401 — selector capability check
    request = "写个汇率计算器"
    _real_workflow_selector(monkeypatch)
    provider = setup_provider(monkeypatch, [
        (request, intent(request, artifact=True, outcomes=["FILE_MUTATION"])),
        ("Workflow Selector", _question_workflow_decision()),
        ("专用工作流未完成", {"tasks": [
            dict(id="write", verb="write", target="output/calculator.py",
                 target_type="file", goal="创建计算器"),
        ]}),
        ("你是文件生成器", "def add(a, b):\n    return a + b\n"),
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
    fallback_call = next(
        c for c in provider.calls if "专用工作流未完成" in c
    )
    assert request in fallback_call


def test_b2_instantiate_with_seeded_question_file_runs_workflow(
    monkeypatch, tmp_path,
):
    """E2E: the same INSTANTIATE decision is honored when question_path is a
    real workspace file — code_generation runs end to end and writes the
    solution; output_path legitimately did not exist beforehand."""
    import tools.filesystem  # noqa: F401 — selector capability check
    request = "解答 input/question.md 并把答案保存为 output/solution.py"
    _real_workflow_selector(monkeypatch)
    provider = setup_provider(monkeypatch, [
        (request, intent(
            request, artifact=True,
            outcomes=["FILE_READ", "FILE_MUTATION"],
        )),
        ("Workflow Selector", _question_workflow_decision(
            question_path="input/question.md",
        )),
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


# ── C) authorization boundary feeds back into planning ───────────────────


def test_c_planner_receives_authorized_outcomes_boundary(monkeypatch):
    """The Planner prompt states which effects the Intent authorized."""
    import agent.planner.planner as planner_module
    from agent.cognition.effect_authorization import EffectAuthorization
    from agent.cognition.execution_need import RequestedOutcome
    from tests.test_ha_r1_harness import ScriptedProvider

    provider = ScriptedProvider([
        ("用户需求", {"tasks": [
            {"verb": "write", "target": "output/calculator.py",
             "target_type": "file", "goal": "创建计算器"},
        ]}),
    ])
    monkeypatch.setattr(planner_module, "llm", provider)
    authorization = EffectAuthorization(
        requested_outcomes=(
            RequestedOutcome.USER_VISIBLE_OUTPUT,
            RequestedOutcome.FILE_MUTATION,
        ),
        write_scopes=("output/",),
    )

    output = asyncio.run(planner_module.plan_with_metadata(
        "写个计算器", authorization=authorization,
    ))

    assert output.tasks
    planner_prompt = provider.calls[0]
    assert "已授权结果" in planner_prompt
    assert "FILE_MUTATION" in planner_prompt
    assert "未授权 execute" in planner_prompt


def test_c_unauthorized_execute_plan_retries_with_feedback_then_completes(
    monkeypatch, tmp_path,
):
    """E2E: read→modify→execute on output/calculator.py is not authorized
    (user never asked to run it).  The violation feeds back into the
    Planner, which returns an executable plan; the run completes and no
    execute side effect ever happens."""
    request = "写个计算器"
    provider = setup_provider(monkeypatch, [
        (request, intent(request, artifact=True, outcomes=["FILE_MUTATION"])),
        ("原始需求", {"tasks": [
            dict(id="read", verb="read", target="output/calculator.py",
                 target_type="file", goal="读取计算器"),
            dict(id="modify", verb="modify", target="output/calculator.py",
                 target_type="file", goal="修改计算器"),
            dict(id="run", verb="execute", target="output/calculator.py",
                 target_type="file", goal="运行计算器"),
        ]}),
        ("包含未授权的效果", {"tasks": [
            dict(id="write", verb="write", target="output/calculator.py",
                 target_type="file", goal="创建计算器"),
        ]}),
        ("你是文件生成器", "def add(a, b):\n    return a + b\n"),
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
    feedback_call = next(
        c for c in provider.calls if "包含未授权的效果" in c
    )
    assert "EFFECT_SCOPE_VIOLATION" in feedback_call


def test_c_persistent_unauthorized_plan_fails_effect_scope_violation(
    monkeypatch, tmp_path,
):
    """Still violating after the retry budget → honest EFFECT_SCOPE_VIOLATION
    terminal; no task executes, so no side effect can happen."""
    request = "写个计算器"
    provider = setup_provider(monkeypatch, [
        (request, intent(request, artifact=True, outcomes=["FILE_MUTATION"])),
        ("原始需求", {"tasks": [
            dict(id="write", verb="write", target="output/calculator.py",
                 target_type="file", goal="创建计算器"),
            dict(id="run", verb="execute", target="output/calculator.py",
                 target_type="file", goal="运行计算器"),
        ]}),
        ("包含未授权的效果", {"tasks": [
            dict(id="write", verb="write", target="output/calculator.py",
                 target_type="file", goal="创建计算器"),
            dict(id="run", verb="execute", target="output/calculator.py",
                 target_type="file", goal="运行计算器"),
        ]}),
    ])

    async def scenario():
        service, store = service_at(tmp_path)
        try:
            snapshot, _ = await run_turn(service, request, 1)
            assert snapshot.status.value == "BLOCKED", snapshot
            persisted = store.read_run_snapshot(
                "ha-tenant", snapshot.run_id, session_id="ha-session",
            )
            payload = json.loads(persisted.terminal_event.payload_json)
            assert payload["failure_code"] == "EFFECT_SCOPE_VIOLATION"
            # Preflight fires before the first side effect.
            assert not (tmp_path / "output/calculator.py").exists()
        finally:
            await service.close()

    asyncio.run(scenario())
    assert not provider.replies
