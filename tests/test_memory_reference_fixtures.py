"""Reference fixture lifecycle and benchmark-hygiene contracts."""

import asyncio

from benchmarks.memory.cases import MemoryCase, build_continuation_cases
from benchmarks.memory.runner import (
    materialize_reference_fixture,
    PROJECT_ROOT,
    run_case,
    summarize_results,
    teardown_reference_fixture,
    validate_reference_fixture,
)


def _reference_cases():
    return [
        case
        for case in build_continuation_cases(n_per_sub=10)
        if case.sub == "reference_resume"
    ]


def test_reference_cases_declare_valid_static_fixtures():
    cases = _reference_cases()

    assert len(cases) == 10
    assert all(case.fixture_source for case in cases)
    assert all(case.fixture_target for case in cases)
    assert all(validate_reference_fixture(case) == () for case in cases)


def test_reference_fixture_materialize_and_teardown_isolated(tmp_path):
    case = _reference_cases()[0]

    fixture = materialize_reference_fixture(case, target_root=tmp_path)
    target = tmp_path / "output" / "ref0.py"
    assert fixture.target == target
    assert target.is_file()
    assert "def calculate" in target.read_text(encoding="utf-8")
    assert validate_reference_fixture(
        case, target_root=tmp_path, require_target=True
    ) == ()

    teardown_reference_fixture(fixture)
    assert not target.exists()


def test_reference_fixture_restores_existing_target(tmp_path):
    case = _reference_cases()[1]
    target = tmp_path / "output" / "ref1.py"
    target.parent.mkdir(parents=True)
    original = b"# user-owned output\n"
    target.write_bytes(original)

    fixture = materialize_reference_fixture(case, target_root=tmp_path)
    assert target.read_bytes() != original
    teardown_reference_fixture(fixture)
    assert target.read_bytes() == original


def test_missing_reference_fixture_is_invalid_benchmark_without_agent_call():
    case = MemoryCase(
        id="invalid-reference-fixture",
        group="continuation",
        sub="reference_resume",
        turns=["首轮分析", "继续刚才那个函数"],
        expected="",
        continuation_contract="CONTINUE_REFERENCE",
        fixture_source="benchmarks/memory/fixtures/reference/missing.py",
        fixture_target="output/missing.py",
        fixture_symbol="missing",
        validation_mode="runtime_contract",
        metric_scope="continuation",
    )

    class DummySession:
        session_id = "fixture-test-session"
        user_id = "fixture-test-user"
        agent = object()

        def __init__(self):
            self.calls = 0

        async def run(self, _text):
            self.calls += 1
            return "must not run"

    session = DummySession()
    result = asyncio.run(
        run_case(case, "fixture-test-user", session=session, keep_output=True)
    )

    assert session.calls == 0
    assert result["benchmark_invalid"] is True
    assert result["passed"] is False
    assert result["exc"] is None
    assert result["detail"].startswith("INVALID_BENCHMARK")

    metric = summarize_results([result])["metrics"][0]
    assert metric["benchmark_invalid"] == 1
    assert metric["benchmark_valid"] == 0
    assert metric["non_exception_pass_rate"] is None


def test_runner_materializes_reference_before_turns_and_tears_down_afterwards():
    case = _reference_cases()[0]
    target = PROJECT_ROOT / "output" / "ref0.py"
    original_exists = target.exists()
    original_bytes = target.read_bytes() if original_exists else None

    class DummyAgent:
        last_run_evidence = {}

    class DummySession:
        session_id = "fixture-runner-session"
        user_id = "fixture-runner-user"

        def __init__(self):
            self.agent = DummyAgent()
            self.calls = 0

        async def run(self, _text):
            self.calls += 1
            if self.calls == 2:
                self.agent.last_run_evidence = {
                "conversation_intent": "continue_reference",
                "requires_execution": False,
                "resolved_target": "calculate",
                "resolved_symbol": "",
            }
            return "fixture-backed response"

    session = DummySession()
    result = asyncio.run(
        run_case(case, "fixture-runner-user", session=session, keep_output=True)
    )

    assert session.calls == 2
    assert result["passed"] is True
    assert result["benchmark_invalid"] is False
    assert target.exists() is original_exists
    if original_exists:
        assert target.read_bytes() == original_bytes


def test_reference_contract_accepts_file_target_or_symbol_evidence():
    case = _reference_cases()[0]
    evidence = {
        "conversation_intent": "continue_reference",
        "requires_execution": False,
        "resolved_target": "output/ref0.py",
        "resolved_symbol": "calculate",
    }

    from benchmarks.memory.runner import validate

    passed, detail = validate(case, "", evidence=evidence)
    assert passed is True
    assert detail == "runtime_contract_ok"
