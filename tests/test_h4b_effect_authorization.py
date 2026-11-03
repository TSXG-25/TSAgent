"""H4b EffectAuthorization: intent-declared outcomes and write scopes."""

from agent.cognition.effect_authorization import EffectAuthorization


def _state(**values):
    base = dict(
        original_request="",
        resolved_request="",
        requested_outcomes=(),
        artifact_required=False,
        intent_write_targets=(),
        goal_state={},
    )
    base.update(values)
    return base


def test_analysis_request_does_not_authorize_source_mutation() -> None:
    authorization = EffectAuthorization.from_state(
        _state(requested_outcomes=["FILE_READ"]), "分析 agent/runtime.py"
    )

    assert authorization.allows_file_mutation is False
    assert authorization.validate_task({
        "verb": "write", "target": "agent/runtime.py", "target_type": "file",
    }) is not None


def test_explicit_output_target_is_authorized() -> None:
    authorization = EffectAuthorization.from_state(
        _state(
            requested_outcomes=["FILE_READ", "FILE_MUTATION"],
            intent_write_targets=["output/agent_roles_summary.md"],
        ),
        "分析两个文件并写总结到 output/agent_roles_summary.md",
    )

    assert authorization.allows_file_mutation is True
    assert authorization.allows_path("output/agent_roles_summary.md") is True
    assert authorization.allows_path("input/a.py") is False


def test_explicit_execution_requires_an_execution_plan() -> None:
    authorization = EffectAuthorization.from_state(
        _state(requested_outcomes=["COMMAND_EXECUTION"]),
        "执行 date 命令，原样贴输出",
    )
    llm_only_plan = type("Plan", (), {"executor": "llm", "steps": []})()

    assert authorization.validate_plan(llm_only_plan) is not None


def test_execution_plan_with_command_step_is_allowed() -> None:
    authorization = EffectAuthorization.from_state(
        _state(requested_outcomes=["COMMAND_EXECUTION"]),
        "执行 date 命令，原样贴输出",
    )
    step = type("Step", (), {"tool": "shell"})()
    tool_plan = type("Plan", (), {"executor": "tool", "steps": [step]})()

    assert authorization.validate_plan(tool_plan) is None
