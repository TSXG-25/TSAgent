"""Deterministic tests for the one logical-run budget."""

from agent.runtime_budget import RunBudget


def test_run_budget_bounds_transitions_and_recoveries() -> None:
    budget = RunBudget(max_seconds=10, max_transitions=2, max_recoveries=1)
    budget.start(now=100.0)

    assert budget.consume_transition(now=100.0) is True
    assert budget.consume_transition(now=100.0) is True
    assert budget.consume_transition(now=100.0) is False
    assert budget.exhausted_code(now=100.0) == "RUNTIME_TRANSITION_BUDGET_EXHAUSTED"
    assert budget.consume_recovery() is True
    assert budget.consume_recovery() is False


def test_run_budget_reports_wall_time_exhaustion() -> None:
    budget = RunBudget(max_seconds=1, max_transitions=20, max_recoveries=2)
    budget.start(now=10.0)

    assert budget.consume_transition(now=12.0) is False
    assert budget.exhausted_code(now=12.0) == "RUNTIME_TIME_BUDGET_EXHAUSTED"
