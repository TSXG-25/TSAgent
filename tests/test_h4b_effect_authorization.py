"""H4b: user-authorized effect scope is independent from workspace scope."""

from agent.cognition.effect_authorization import EffectAuthorization


def test_analysis_request_does_not_authorize_source_mutation() -> None:
    authorization = EffectAuthorization.from_request("分析 agent/runtime.py")

    assert authorization.allows_file_mutation is False
    assert authorization.validate_task({
        "verb": "write",
        "target": "agent/runtime.py",
    }) is not None


def test_explicit_output_target_is_authorized() -> None:
    authorization = EffectAuthorization.from_request(
        "分析两个文件并写总结到 output/agent_roles_summary.md"
    )

    assert authorization.allows_file_mutation is True
    assert authorization.allows_path("output/agent_roles_summary.md") is True
    assert authorization.validate_task({
        "verb": "write",
        "target": "output/agent_roles_summary.md",
    }) is None
    assert authorization.validate_task({
        "verb": "write",
        "target": "agent/runtime.py",
    }) is not None


def test_explicit_execution_requires_an_execution_plan() -> None:
    authorization = EffectAuthorization.from_request("执行 date 命令，原样贴输出")

    assert authorization.validate_plan(
        type("Plan", (), {"executor": "llm", "steps": []})()
    ) is not None


def test_execution_plan_with_command_step_is_allowed() -> None:
    authorization = EffectAuthorization.from_request("执行 date 命令，原样贴输出")
    step = type("Step", (), {"tool": "shell"})()
    plan = type("Plan", (), {"executor": "tool", "steps": [step]})()

    assert authorization.validate_plan(plan) is None
