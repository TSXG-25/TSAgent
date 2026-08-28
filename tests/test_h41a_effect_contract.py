"""H4.1a contracts: capability requirements, internal effects and target binding."""

from agent.cognition.effect_authorization import EffectAuthorization
from agent.cognition.execution_need import EffectScope, RequestedOutcome
from agent.cognition.resource_binding import extract_bound_targets, extract_explicit_paths
from agent.orchestrator.planner import _extract_explicit_output_paths


def test_directory_phrase_binds_the_full_persistent_target() -> None:
    request = "在 output/ 下新建 probe_tool_test.py 并运行"
    targets = extract_bound_targets(request)

    assert [target.path for target in targets] == ["output/probe_tool_test.py"]
    assert _extract_explicit_output_paths(request) == ["output/probe_tool_test.py"]


def test_bound_target_preserves_explicit_write_scope() -> None:
    authorization = EffectAuthorization.from_request(
        "分析源码并把总结保存到 output/report.md"
    )

    assert authorization.bound_targets[0].path == "output/report.md"
    assert authorization.allows_path("output/report.md") is True
    assert authorization.allows_path("report.md") is False
    assert authorization.allows_path("agent/runtime.py") is False


def test_execution_outcomes_declare_capability_requirements() -> None:
    command = EffectAuthorization.from_request("执行 date 命令并原样贴输出")
    code = EffectAuthorization.from_request("用 Python 算 1+2 并实际执行")

    assert command.command_execution_allowed is True
    assert command.code_execution_allowed is False
    assert code.code_execution_allowed is True
    assert code.validate_plan(
        type("Plan", (), {"executor": "llm", "steps": []})()
    ) is not None


def test_internal_execution_effect_is_not_user_file_authorization() -> None:
    authorization = EffectAuthorization.from_request(
        "用 Python 算 1+2 并实际执行"
    )
    task = {
        "verb": "write",
        "target": ".tsagent/tmp/attempt.py",
        "policy": {"effect_scope": EffectScope.INTERNAL_EXECUTION_EFFECT.value},
    }

    assert authorization.allows_file_mutation is False
    assert authorization.validate_task(task) is None
    assert authorization.validate_task({
        **task,
        "target": "agent/runtime.py",
    }) is not None


def test_explicit_paths_are_extracted_without_dropping_directory_prefix() -> None:
    paths = extract_explicit_paths(
        "读取 output/a.txt 和 output/b.txt，保存到 output/report.md"
    )

    assert paths == ("output/a.txt", "output/b.txt", "output/report.md")
