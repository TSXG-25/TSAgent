"""H4.1a contracts: capability requirements, internal effects and write scopes."""

from agent.cognition.effect_authorization import EffectAuthorization
from agent.cognition.execution_need import EffectScope, RequestedOutcome


def _state(**values):
    state = dict(
        original_request=values.pop("original_request", ""),
        resolved_request=values.pop("resolved_request", ""),
        requested_outcomes=values.pop("requested_outcomes", ()),
        artifact_required=values.pop("artifact_required", False),
        intent_write_targets=values.pop("intent_write_targets", ()),
        goal_state=values.pop("goal_state", {}),
    )
    state.update(values)
    return state


def test_declared_write_targets_scope_mutation() -> None:
    authorization = EffectAuthorization.from_state(
        _state(
            original_request="把总结保存到 output/report.md",
            requested_outcomes=["FILE_MUTATION"],
            intent_write_targets=["output/report.md"],
        ),
        "把总结保存到 output/report.md",
    )

    assert authorization.allows_path("output/report.md") is True
    assert authorization.allows_path("report.md") is False
    assert authorization.allows_path("agent/runtime.py") is False


def test_empty_write_targets_with_mutation_scopes_output_directory() -> None:
    authorization = EffectAuthorization.from_state(
        _state(requested_outcomes=["FILE_MUTATION"], artifact_required=True),
        "写个计算器",
    )

    assert authorization.write_scopes == ("output/",)
    assert authorization.allows_path("output/calculator.py") is True
    assert authorization.allows_path("agent/runtime.py") is False


def test_no_mutation_outcome_rejects_writes() -> None:
    authorization = EffectAuthorization.from_state(
        _state(requested_outcomes=["USER_VISIBLE_OUTPUT"]),
        "解释一下",
    )

    assert authorization.allows_file_mutation is False
    assert authorization.validate_task({
        "verb": "write", "target": "output/a.txt", "target_type": "file",
    }) is not None


def test_execution_outcomes_declare_capability_requirements() -> None:
    command = EffectAuthorization.from_state(
        _state(requested_outcomes=["COMMAND_EXECUTION"]), "执行 date"
    )
    code = EffectAuthorization.from_state(
        _state(requested_outcomes=["CODE_EXECUTION"]), "运行脚本"
    )

    assert command.command_execution_allowed is True
    assert command.code_execution_allowed is False
    assert code.code_execution_allowed is True
    assert code.validate_plan(
        type("Plan", (), {"executor": "llm", "steps": []})()
    ) is not None


def test_internal_execution_effect_is_not_user_file_authorization() -> None:
    authorization = EffectAuthorization.from_state(
        _state(requested_outcomes=["CODE_EXECUTION"]), "用 Python 算 1+2"
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
