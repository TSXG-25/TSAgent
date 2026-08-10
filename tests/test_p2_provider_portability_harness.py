"""Deterministic checks for the P2-P portability harness and adapter."""

from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from realtest_reports.harness.p2.evidence import RunTraceEvidence
from realtest_reports.harness.p2.groups.portability import (
    AttemptStatus,
    PortabilityAttemptResult,
    PortabilityProbeResult,
    PortabilityScenario,
    build_report,
    build_scenarios,
    deferred_attempt,
    evaluate_pair,
    fixture_attempt,
    run_fixture,
    score_runtime,
)
from realtest_reports.harness.p2.groups.portability_worker import run_real_matrix
from realtest_reports.harness.p2.provider_adapter import (
    FixedProviderRouter,
    MalformedStructuredResponseError,
    ProviderErrorCode,
    ProviderSpec,
    classify_provider_error,
)


class _FakeModel:
    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, messages, **kwargs):
        self.calls += 1
        return {"content": "ok"}

    async def ainvoke(self, messages, **kwargs):
        self.calls += 1
        return type("Response", (), {"content": "ok"})()

    def with_structured_output(self, schema, **kwargs):
        return self

    def bind_tools(self, tools, **kwargs):
        return self


def _spec(variant: str = "primary") -> ProviderSpec:
    prefix = variant.upper()
    return ProviderSpec(
        variant=variant,
        provider_id=f"provider-{variant}",
        api_key_env=f"{prefix}_KEY",
        model_env=f"{prefix}_MODEL",
        base_url_env=f"{prefix}_URL",
        endpoint_class="test",
    )


def _configured(spec: ProviderSpec) -> dict[str, str]:
    return {
        spec.api_key_env: "secret-value-never-serialize",
        spec.model_env: "test-model",
        spec.base_url_env: "https://provider.invalid/v1",
    }


def test_fixed_portability_manifest_has_three_provider_neutral_scenarios() -> None:
    scenarios = build_scenarios()

    assert [scenario.case.id for scenario in scenarios] == ["P01", "P02", "P03"]
    assert [len(scenario.probes) for scenario in scenarios] == [1, 1, 2]
    assert all(not scenario.case.reprompt_allowed for scenario in scenarios)
    assert all(len(scenario.prompt_hash) == 64 for scenario in scenarios)
    assert all(len(scenario.fixture_hash) == 64 for scenario in scenarios)
    manifest = [scenario.public_contract() for scenario in scenarios]
    payload = json.dumps(manifest, ensure_ascii=False, sort_keys=True)
    assert "primary" not in payload
    assert "secondary" not in payload


def test_provider_spec_never_serializes_resolved_secret() -> None:
    spec = _spec()
    environ = _configured(spec)

    public = spec.to_public_dict(environ)
    config = spec.to_config_dict()
    serialized = json.dumps({"public": public, "config": config}, sort_keys=True)

    assert environ[spec.api_key_env] not in serialized
    assert public["configured"] is True
    assert config["api_key_env"] == spec.api_key_env
    assert ProviderSpec.from_config_dict(config) == spec


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TimeoutError("slow"), ProviderErrorCode.TIMEOUT),
        (RuntimeError("HTTP 401 unauthorized"), ProviderErrorCode.AUTH),
        (RuntimeError("HTTP 429 rate limit"), ProviderErrorCode.RATE_LIMIT),
        (RuntimeError("DNS name resolution failed"), ProviderErrorCode.NETWORK),
        (RuntimeError("response_format is unsupported"), ProviderErrorCode.STRUCTURED_OUTPUT_REJECTED),
        (RuntimeError("invalid json response"), ProviderErrorCode.MALFORMED_RESPONSE),
    ],
)
def test_provider_errors_map_to_stable_categories(error, expected) -> None:
    assert classify_provider_error(error) is expected


def test_malformed_structured_probe_is_once_only_and_raw_fallback_still_works() -> None:
    spec = _spec()
    model = _FakeModel()
    router = FixedProviderRouter(
        spec,
        environ=_configured(spec),
        model_factory=lambda _config, _timeout: model,
        inject_malformed_structured_once=True,
    )
    provider, name = router._get_active_provider()

    assert name == spec.provider_id
    with pytest.raises(MalformedStructuredResponseError):
        asyncio.run(provider.with_structured_output(dict).ainvoke([]))
    # The deterministic failure is consumed. A second structured call reaches
    # the same fixed Provider rather than falling back to another Provider.
    response = asyncio.run(provider.with_structured_output(dict).ainvoke([]))
    assert response.content == "ok"
    assert router.recorder.error_codes == ("MALFORMED_RESPONSE",)
    assert [call.injected_probe for call in router.recorder.calls] == [True, False]


def test_fixture_pipeline_is_six_attempts_and_never_claims_real_execution() -> None:
    attempts = run_fixture()
    report = build_report(attempts, source="fixture", commit="test")

    assert len(attempts) == 6
    assert all(attempt.status is AttemptStatus.FIXTURE for attempt in attempts)
    assert all(attempt.runtime_correctness == "PASS" for attempt in attempts)
    assert report["summary"]["real_executed"] == 0
    assert report["summary"]["fixture_attempts"] == 6
    assert report["summary"]["pair_runtime_pass"] == 3
    assert "no real Provider acceptance" in report["scope"]


def test_capability_failure_can_still_have_runtime_correctness_pass() -> None:
    scenario = build_scenarios()[0]
    probe = scenario.probes[0]
    trace = RunTraceEvidence(
        case_id="P01",
        run_id="safe-provider-failure",
        provider="provider-a",
        planned_tasks=(),
        workflow_transitions=("run_started", "run_failed"),
        task_execution_counts={},
        completed_task_ids=(),
        artifacts=(),
        required_artifact_ids=probe.required_artifacts,
        terminal_status="FAILED_TERMINAL",
        terminal_event_type="run_failed",
        terminal_outputs_verified=False,
        provider_errors=("TIMEOUT",),
    )

    runtime = score_runtime(scenario.case, trace)
    result = PortabilityProbeResult.from_trace(scenario.case, probe, trace)

    assert runtime.runtime_correctness == "PASS"
    assert runtime.diagnostics["missing_required_artifacts"] == 1
    assert result.capability_outcome == "FAIL"


def test_false_completed_fails_runtime_even_when_provider_returned_normally() -> None:
    scenario = build_scenarios()[0]
    probe = scenario.probes[0]
    trace = RunTraceEvidence(
        case_id="P01",
        run_id="false-completed",
        provider="provider-a",
        planned_tasks=(),
        workflow_transitions=("run_completed",),
        task_execution_counts={},
        completed_task_ids=(),
        artifacts=(),
        required_artifact_ids=probe.required_artifacts,
        terminal_status="COMPLETED",
        terminal_event_type="run_completed",
        terminal_outputs_verified=False,
    )

    result = PortabilityProbeResult.from_trace(scenario.case, probe, trace)

    assert result.runtime.runtime_correctness == "FAIL"
    assert result.runtime.hard_gates["false_completed"] is True
    assert result.capability_outcome == "FAIL"


def test_pair_validation_rejects_prompt_drift_between_providers() -> None:
    scenario = build_scenarios()[0]
    primary = fixture_attempt(scenario, _spec("primary"))
    changed_probe = replace(scenario.probes[0], prompt=scenario.probes[0].prompt + " changed")
    changed_scenario = PortabilityScenario(scenario.case, (changed_probe,))
    secondary = fixture_attempt(changed_scenario, _spec("secondary"))

    pair = evaluate_pair((primary, secondary))

    assert pair.status == "INVALID"
    assert pair.prompt_parity is False
    assert "PROMPT_HASH_MISMATCH" in pair.problems


def test_attempt_round_trip_recomputes_scores_and_checks_contract_hashes() -> None:
    attempt = fixture_attempt(build_scenarios()[2], _spec("primary"))

    restored = PortabilityAttemptResult.from_dict(attempt.to_dict())

    assert restored.status is AttemptStatus.FIXTURE
    assert restored.capability_outcome == "PASS"
    assert restored.runtime_correctness == "PASS"
    assert [probe.probe.probe_id for probe in restored.probes] == [
        "P03-unsupported",
        "P03-malformed",
    ]


def test_missing_provider_configuration_is_deferred_without_spawning(tmp_path) -> None:
    specs = (_spec("primary"), _spec("secondary"))
    scenario = build_scenarios()[0]

    attempts = run_real_matrix((scenario,), specs, work_root=tmp_path)

    assert len(attempts) == 2
    assert all(attempt.status is AttemptStatus.DEFERRED for attempt in attempts)
    assert all(attempt.capability_outcome is None for attempt in attempts)
    assert all(attempt.runtime_correctness is None for attempt in attempts)
    assert list(tmp_path.iterdir()) == []


def test_worker_module_does_not_import_runtime_consumers_before_provider_install() -> None:
    process = subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            (
                "import sys; "
                "import realtest_reports.harness.p2.groups.portability_worker; "
                "print('agent.llm' in sys.modules); "
                "print('agent.runtime' in sys.modules)"
            ),
        ],
        check=True,
        text=True,
        capture_output=True,
    )

    assert process.stdout.splitlines() == ["False", "False"]


def test_module_entrypoint_aggregates_deferred_attempts_across_module_aliases(
    tmp_path,
) -> None:
    """Exercise the real CLI path that previously loaded two Enum classes."""

    report_path = tmp_path / "report.json"
    work_root = tmp_path / "work"
    environment = os.environ.copy()
    environment.update(
        {
            "OPENAI_API_KEY": "",
            "P2_SECONDARY_API_KEY": "",
            "P2_SECONDARY_MODEL": "",
            "P2_SECONDARY_BASE_URL": "",
        }
    )
    subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "realtest_reports.harness.p2.groups.portability",
            "--mode",
            "real",
            "--work-root",
            str(work_root),
            "--results",
            str(report_path),
        ],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=environment,
        check=True,
        text=True,
        capture_output=True,
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["summary"]["attempts"] == 6
    assert report["summary"]["real_executed"] == 0
    assert report["summary"]["deferred"] == 6
    assert report["summary"]["runtime_correctness_pass"] == 0
    assert [pair["status"] for pair in report["pairs"]] == [
        "DEFERRED",
        "DEFERRED",
        "DEFERRED",
    ]


def test_deferred_report_does_not_leak_key_value() -> None:
    spec = _spec()
    environ = _configured(spec)
    attempt = deferred_attempt(
        build_scenarios()[0],
        spec,
        environ=environ,
    )

    serialized = json.dumps(attempt.to_dict(), ensure_ascii=False, sort_keys=True)

    assert environ[spec.api_key_env] not in serialized
    assert attempt.status is AttemptStatus.DEFERRED
