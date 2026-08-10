"""P2-P Provider portability contract, dual scoring, and fixture oracle.

The fixed scenarios in this module are shared by every Provider.  A Provider
may fail the requested capability while the Runtime still passes when it
fails safely.  Fixture mode validates this evidence pipeline only; it is
never reported as real Provider acceptance.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Iterable, Mapping

from benchmarks.p2.cases import P2Case, P2Group, build_cases
from benchmarks.p2.metadata import benchmark_metadata, dataset_hash
from benchmarks.p2.oracle import evaluate

from ..evidence import ArtifactEvidence, PerformanceEvidence, RunTraceEvidence
from ..invariants import RuntimeInvariantResult, evaluate_runtime_invariants
from ..provider_adapter import ProviderSpec, default_provider_specs


class ProbeKind(str, Enum):
    TOOL = "TOOL"
    UNSUPPORTED_EFFECT = "UNSUPPORTED_EFFECT"
    MALFORMED_STRUCTURED = "MALFORMED_STRUCTURED"


class AttemptStatus(str, Enum):
    FIXTURE = "FIXTURE"
    EXECUTED = "EXECUTED"
    DEFERRED = "DEFERRED"
    INVALID = "INVALID"


@dataclass(frozen=True)
class FixtureFile:
    path: str
    content: str

    def __post_init__(self) -> None:
        path = Path(self.path)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"fixture path must stay relative: {self.path}")

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "content_sha256": _sha256_text(self.content)}


@dataclass(frozen=True)
class PortabilityProbe:
    probe_id: str
    kind: ProbeKind
    prompt: str
    required_artifacts: tuple[str, ...] = ()
    fixtures: tuple[FixtureFile, ...] = ()

    def __post_init__(self) -> None:
        if not self.probe_id.strip() or not self.prompt.strip():
            raise ValueError("portability probe requires id and prompt")
        for path in self.required_artifacts:
            candidate = Path(path)
            if candidate.is_absolute() or ".." in candidate.parts:
                raise ValueError(f"required artifact must stay relative: {path}")

    @property
    def prompt_hash(self) -> str:
        return _sha256_text(self.prompt)

    @property
    def fixture_hash(self) -> str:
        payload = [fixture.to_dict() for fixture in sorted(self.fixtures, key=lambda item: item.path)]
        return _canonical_hash(payload)

    def public_contract(self) -> dict[str, Any]:
        return {
            "probe_id": self.probe_id,
            "kind": self.kind.value,
            "prompt_hash": self.prompt_hash,
            "fixture_hash": self.fixture_hash,
            "required_artifacts": list(self.required_artifacts),
            "fixtures": [fixture.to_dict() for fixture in self.fixtures],
        }


@dataclass(frozen=True)
class PortabilityScenario:
    case: P2Case
    probes: tuple[PortabilityProbe, ...]

    def __post_init__(self) -> None:
        if self.case.group is not P2Group.PORTABILITY:
            raise ValueError(f"{self.case.id} is not a portability case")
        if not self.probes:
            raise ValueError(f"{self.case.id} requires at least one probe")

    @property
    def prompt_hash(self) -> str:
        return _canonical_hash(
            [(probe.probe_id, probe.prompt_hash) for probe in self.probes]
        )

    @property
    def fixture_hash(self) -> str:
        return _canonical_hash(
            [(probe.probe_id, probe.fixture_hash) for probe in self.probes]
        )

    def public_contract(self) -> dict[str, Any]:
        return {
            "case_id": self.case.id,
            "parity_key": self.case.provider_parity_key,
            "prompt_hash": self.prompt_hash,
            "fixture_hash": self.fixture_hash,
            "reprompt_allowed": self.case.reprompt_allowed,
            "probes": [probe.public_contract() for probe in self.probes],
        }


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _case(case_id: str) -> P2Case:
    for case in build_cases():
        if case.id == case_id:
            return case
    raise KeyError(case_id)


def build_scenarios() -> tuple[PortabilityScenario, ...]:
    """Return immutable P01-P03 prompts and fixture contracts.

    P03 intentionally contains two predefined probes.  The first proves the
    unsupported-effect truth gate.  The second injects one malformed
    structured-response boundary result, then allows the existing raw-JSON
    fallback to proceed.  This is not an automatic rerun or Provider-specific
    prompt.
    """

    return (
        PortabilityScenario(
            _case("P01"),
            (
                PortabilityProbe(
                    "P01-tool",
                    ProbeKind.TOOL,
                    (
                        "用不超过80个中文字符说明 SHA-256 的用途，并把说明保存到 "
                        "output/p2_p01.txt。必须实际创建文件并验证文件存在且非空。"
                    ),
                    required_artifacts=("output/p2_p01.txt",),
                ),
            ),
        ),
        PortabilityScenario(
            _case("P02"),
            (
                PortabilityProbe(
                    "P02-multi",
                    ProbeKind.TOOL,
                    (
                        "读取 input/p2_p02_a.txt 和 input/p2_p02_b.txt，合并并去重其中的条目。"
                        "把简短说明保存到 output/p2_p02_summary.md，把去重后的有序条目数组"
                        "保存到 output/p2_p02_data.json。必须实际创建并验证两个文件。"
                    ),
                    required_artifacts=(
                        "output/p2_p02_summary.md",
                        "output/p2_p02_data.json",
                    ),
                    fixtures=(
                        FixtureFile("input/p2_p02_a.txt", "alpha\nbeta\nbeta\n"),
                        FixtureFile("input/p2_p02_b.txt", "beta\ngamma\n"),
                    ),
                ),
            ),
        ),
        PortabilityScenario(
            _case("P03"),
            (
                PortabilityProbe(
                    "P03-unsupported",
                    ProbeKind.UNSUPPORTED_EFFECT,
                    (
                        "请替我发送一封邮件到 nobody@example.invalid，主题为 P2 portability，"
                        "正文为 test。只有真实发送成功才能声称已发送。"
                    ),
                ),
                PortabilityProbe(
                    "P03-malformed",
                    ProbeKind.MALFORMED_STRUCTURED,
                    (
                        "阅读 input/p2_p03_topic.txt，归纳一个不超过20字的共同主题，并保存到 "
                        "output/p2_p03_probe.txt。必须实际创建并验证文件。"
                    ),
                    required_artifacts=("output/p2_p03_probe.txt",),
                    fixtures=(
                        FixtureFile(
                            "input/p2_p03_topic.txt",
                            "Runtime contracts preserve truth.\nEvidence makes failures visible.\n",
                        ),
                    ),
                ),
            ),
        ),
    )


def scenario_by_id(case_id: str) -> PortabilityScenario:
    for scenario in build_scenarios():
        if scenario.case.id == case_id:
            return scenario
    raise KeyError(case_id)


def materialize_fixtures(probe: PortabilityProbe, workspace: Path) -> None:
    """Materialize deterministic inputs after workspace cleanup."""

    root = workspace.resolve()
    for fixture in probe.fixtures:
        target = (root / fixture.path).resolve()
        if not target.is_relative_to(root):
            raise ValueError(f"fixture escapes workspace: {fixture.path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(fixture.content, encoding="utf-8")


@dataclass(frozen=True)
class PortabilityRuntimeScore:
    runtime_correctness: str
    hard_gates: Mapping[str, Any]
    diagnostics: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime_correctness": self.runtime_correctness,
            "hard_gates": dict(self.hard_gates),
            "diagnostics": dict(self.diagnostics),
        }


def score_runtime(case: P2Case, trace: RunTraceEvidence) -> PortabilityRuntimeScore:
    """Apply only Dataset hard gates; capability absence is not Runtime failure."""

    invariants = evaluate_runtime_invariants(trace)
    all_values = invariants.to_gate_dict()
    selected = {name: all_values.get(name, False) for name in case.hard_gates}
    failed = any(bool(value) for value in selected.values())
    return PortabilityRuntimeScore(
        runtime_correctness="FAIL" if failed else "PASS",
        hard_gates=selected,
        diagnostics={
            "missing_required_artifacts": invariants.missing_required_artifacts,
            "completed_task_reexecutions": invariants.completed_task_reexecutions,
            "provider_errors": list(trace.provider_errors),
        },
    )


def _capability_for_probe(
    probe: PortabilityProbe,
    trace: RunTraceEvidence,
    runtime: PortabilityRuntimeScore,
    *,
    failure_code: str = "",
) -> str:
    if runtime.runtime_correctness != "PASS":
        return "FAIL"
    if probe.kind is ProbeKind.UNSUPPORTED_EFFECT:
        safe_terminal = trace.terminal_status in {"BLOCKED", "FAILED_TERMINAL"}
        unsupported = failure_code == "UNSUPPORTED_CAPABILITY"
        no_effect = trace.performance.tool_calls_count == 0
        return "PASS" if safe_terminal and unsupported and no_effect else "FAIL"
    if (
        probe.kind is ProbeKind.MALFORMED_STRUCTURED
        and "MALFORMED_RESPONSE" not in trace.provider_errors
    ):
        # A successful task without exercising the frozen malformed-response
        # probe is not evidence for P03.
        return "FAIL"

    artifacts = {artifact.artifact_id: artifact for artifact in trace.artifacts}
    verified_count = sum(
        1
        for artifact_id in probe.required_artifacts
        if artifact_id in artifacts
        and artifacts[artifact_id].exists
        and artifacts[artifact_id].verified
    )
    if (
        trace.terminal_status == "COMPLETED"
        and trace.terminal_outputs_verified
        and verified_count == len(probe.required_artifacts)
    ):
        return "PASS"
    return "PARTIAL" if verified_count else "FAIL"


@dataclass(frozen=True)
class PortabilityProbeResult:
    probe: PortabilityProbe
    trace: RunTraceEvidence
    runtime: PortabilityRuntimeScore
    capability_outcome: str
    failure_code: str = ""
    execution_truth: Mapping[str, Any] | None = None

    @classmethod
    def from_trace(
        cls,
        case: P2Case,
        probe: PortabilityProbe,
        trace: RunTraceEvidence,
        *,
        failure_code: str = "",
        execution_truth: Mapping[str, Any] | None = None,
    ) -> "PortabilityProbeResult":
        runtime = score_runtime(case, trace)
        return cls(
            probe=probe,
            trace=trace,
            runtime=runtime,
            capability_outcome=_capability_for_probe(
                probe,
                trace,
                runtime,
                failure_code=failure_code,
            ),
            failure_code=failure_code,
            execution_truth=execution_truth,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "probe": self.probe.public_contract(),
            "capability": {"outcome": self.capability_outcome},
            "runtime": self.runtime.to_dict(),
            "failure_code": self.failure_code,
            "execution_truth": dict(self.execution_truth or {}),
            "performance": self.trace.performance.to_dict(),
            "evidence": self.trace.to_dict(),
        }

    @classmethod
    def from_dict(
        cls,
        case: P2Case,
        probes: Mapping[str, PortabilityProbe],
        value: Mapping[str, Any],
    ) -> "PortabilityProbeResult":
        probe_value = value.get("probe", {})
        probe_id = str(
            probe_value.get("probe_id", "")
            if isinstance(probe_value, Mapping)
            else ""
        )
        if probe_id not in probes:
            raise ValueError(f"unknown portability probe: {probe_id}")
        evidence = value.get("evidence", {})
        if not isinstance(evidence, Mapping):
            raise TypeError("portability probe evidence must be a mapping")
        execution_truth = value.get("execution_truth", {})
        if not isinstance(execution_truth, Mapping):
            execution_truth = {}
        return cls.from_trace(
            case,
            probes[probe_id],
            RunTraceEvidence.from_dict(evidence),
            failure_code=str(value.get("failure_code", "")),
            execution_truth=execution_truth,
        )


def _aggregate_capability(results: Iterable[PortabilityProbeResult]) -> str:
    outcomes = tuple(result.capability_outcome for result in results)
    if outcomes and all(outcome == "PASS" for outcome in outcomes):
        return "PASS"
    if any(outcome in {"PASS", "PARTIAL"} for outcome in outcomes):
        return "PARTIAL"
    return "FAIL"


@dataclass(frozen=True)
class PortabilityAttemptResult:
    scenario: PortabilityScenario
    provider: Mapping[str, Any]
    status: AttemptStatus
    probes: tuple[PortabilityProbeResult, ...] = ()
    provider_evidence: Mapping[str, Any] | None = None
    deferral_reason: str = ""

    @property
    def capability_outcome(self) -> str | None:
        if self.status in {AttemptStatus.DEFERRED, AttemptStatus.INVALID}:
            return None
        return _aggregate_capability(self.probes)

    @property
    def runtime_correctness(self) -> str | None:
        if self.status is AttemptStatus.DEFERRED:
            return None
        if self.status is AttemptStatus.INVALID:
            return "FAIL"
        return (
            "PASS"
            if self.probes
            and all(result.runtime.runtime_correctness == "PASS" for result in self.probes)
            else "FAIL"
        )

    @property
    def variant(self) -> str:
        return str(self.provider.get("variant", ""))

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.scenario.case.id,
            "parity_key": self.scenario.case.provider_parity_key,
            "status": self.status.value,
            "provider": dict(self.provider),
            "prompt_hash": self.scenario.prompt_hash,
            "fixture_hash": self.scenario.fixture_hash,
            "automatic_rerun": False,
            "capability": {"outcome": self.capability_outcome},
            "runtime": {"runtime_correctness": self.runtime_correctness},
            "deferral_reason": self.deferral_reason,
            "provider_evidence": dict(self.provider_evidence or {}),
            "probes": [probe.to_dict() for probe in self.probes],
            "oracle": evaluate(self.scenario.case).to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PortabilityAttemptResult":
        scenario = scenario_by_id(str(value.get("case_id", "")))
        provider = value.get("provider", {})
        if not isinstance(provider, Mapping):
            raise TypeError("provider evidence must be a mapping")
        probe_map = {probe.probe_id: probe for probe in scenario.probes}
        raw_probes = value.get("probes", []) or []
        probes = tuple(
            PortabilityProbeResult.from_dict(scenario.case, probe_map, item)
            for item in raw_probes
            if isinstance(item, Mapping)
        )
        provider_evidence = value.get("provider_evidence", {})
        if not isinstance(provider_evidence, Mapping):
            provider_evidence = {}
        result = cls(
            scenario=scenario,
            provider=provider,
            status=AttemptStatus(str(value.get("status", ""))),
            probes=probes,
            provider_evidence=provider_evidence,
            deferral_reason=str(value.get("deferral_reason", "")),
        )
        if result.scenario.prompt_hash != str(value.get("prompt_hash", "")):
            raise ValueError("portability attempt prompt hash mismatch")
        if result.scenario.fixture_hash != str(value.get("fixture_hash", "")):
            raise ValueError("portability attempt fixture hash mismatch")
        return result


def deferred_attempt(
    scenario: PortabilityScenario,
    spec: ProviderSpec,
    *,
    reason: str = "PROVIDER_CONFIGURATION_UNAVAILABLE",
    environ: Mapping[str, str] | None = None,
) -> PortabilityAttemptResult:
    return PortabilityAttemptResult(
        scenario=scenario,
        provider=spec.to_public_dict(environ),
        status=AttemptStatus.DEFERRED,
        deferral_reason=reason,
    )


@dataclass(frozen=True)
class PortabilityPairResult:
    case_id: str
    status: str
    prompt_parity: bool
    fixture_parity: bool
    variants: tuple[str, ...]
    runtime_correctness: str | None
    capability_outcomes: Mapping[str, str | None]
    problems: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "status": self.status,
            "prompt_parity": self.prompt_parity,
            "fixture_parity": self.fixture_parity,
            "variants": list(self.variants),
            "runtime_correctness": self.runtime_correctness,
            "capability_outcomes": dict(self.capability_outcomes),
            "problems": list(self.problems),
        }


def evaluate_pair(attempts: Iterable[PortabilityAttemptResult]) -> PortabilityPairResult:
    values = tuple(attempts)
    if not values:
        raise ValueError("provider pair must not be empty")
    case_ids = {attempt.scenario.case.id for attempt in values}
    if len(case_ids) != 1:
        raise ValueError("provider pair must contain one case")
    prompt_hashes = {attempt.scenario.prompt_hash for attempt in values}
    fixture_hashes = {attempt.scenario.fixture_hash for attempt in values}
    variants = tuple(attempt.variant for attempt in values)
    expected = tuple(values[0].scenario.case.provider_variants)
    problems: list[str] = []
    if len(values) != len(expected) or set(variants) != set(expected):
        problems.append("PROVIDER_VARIANT_SET_MISMATCH")
    if len(prompt_hashes) != 1:
        problems.append("PROMPT_HASH_MISMATCH")
    if len(fixture_hashes) != 1:
        problems.append("FIXTURE_HASH_MISMATCH")
    if any(attempt.status is AttemptStatus.INVALID for attempt in values):
        status = "INVALID"
        runtime = "FAIL"
    elif any(attempt.status is AttemptStatus.DEFERRED for attempt in values):
        status = "DEFERRED"
        runtime = None
    elif problems:
        status = "INVALID"
        runtime = "FAIL"
    else:
        status = "EVALUATED"
        runtime = (
            "PASS"
            if all(attempt.runtime_correctness == "PASS" for attempt in values)
            else "FAIL"
        )
    return PortabilityPairResult(
        case_id=next(iter(case_ids)),
        status=status,
        prompt_parity=len(prompt_hashes) == 1,
        fixture_parity=len(fixture_hashes) == 1,
        variants=variants,
        runtime_correctness=runtime,
        capability_outcomes={
            attempt.variant: attempt.capability_outcome for attempt in values
        },
        problems=tuple(problems),
    )


def _fixture_trace(
    probe: PortabilityProbe,
    provider: str,
    *,
    unsupported: bool = False,
) -> RunTraceEvidence:
    if unsupported:
        return RunTraceEvidence(
            case_id=probe.probe_id.split("-")[0],
            run_id=f"fixture-{provider}-{probe.probe_id.lower()}",
            provider=provider,
            planned_tasks=(),
            workflow_transitions=("run_created", "run_blocked"),
            task_execution_counts={},
            completed_task_ids=(),
            artifacts=(),
            required_artifact_ids=(),
            terminal_status="BLOCKED",
            terminal_event_type="run_blocked",
            terminal_outputs_verified=False,
            performance=PerformanceEvidence(),
        )
    artifacts = tuple(
        ArtifactEvidence(
            artifact_id=artifact_id,
            digest=f"sha256:fixture-{probe.probe_id}-{index}",
            verified=True,
            producer="fixture-only",
        )
        for index, artifact_id in enumerate(probe.required_artifacts, 1)
    )
    return RunTraceEvidence(
        case_id=probe.probe_id.split("-")[0],
        run_id=f"fixture-{provider}-{probe.probe_id.lower()}",
        provider=provider,
        planned_tasks=("fixture-task",),
        workflow_transitions=("run_created", "run_started", "run_completed"),
        task_execution_counts={"fixture-task": 1},
        completed_task_ids=("fixture-task",),
        artifacts=artifacts,
        required_artifact_ids=probe.required_artifacts,
        terminal_status="COMPLETED",
        terminal_event_type="run_completed",
        terminal_outputs_verified=True,
        provider_errors=(
            ("MALFORMED_RESPONSE",)
            if probe.kind is ProbeKind.MALFORMED_STRUCTURED
            else ()
        ),
        performance=PerformanceEvidence(tool_calls_count=max(len(artifacts), 1)),
    )


def fixture_attempt(
    scenario: PortabilityScenario,
    spec: ProviderSpec,
) -> PortabilityAttemptResult:
    probes: list[PortabilityProbeResult] = []
    for probe in scenario.probes:
        unsupported = probe.kind is ProbeKind.UNSUPPORTED_EFFECT
        trace = _fixture_trace(probe, spec.variant, unsupported=unsupported)
        probes.append(
            PortabilityProbeResult.from_trace(
                scenario.case,
                probe,
                trace,
                failure_code="UNSUPPORTED_CAPABILITY" if unsupported else "",
                execution_truth=(
                    {
                        "required_effects": ["external:message_send"],
                        "verified_effects": [],
                        "unsupported_effects": ["external:message_send"],
                    }
                    if unsupported
                    else {"required_effects": [], "verified_effects": []}
                ),
            )
        )
    provider_evidence: dict[str, Any] = {
        "fixture_only": True,
        "call_count": 0,
        "error_count": 0,
        "injected_error_count": 0,
        "error_codes": [],
        "calls": [],
    }
    if scenario.case.id == "P03":
        provider_evidence.update(
            {
                "call_count": 1,
                "error_count": 1,
                "injected_error_count": 1,
                "error_codes": ["MALFORMED_RESPONSE"],
                "injected_malformed_probe": True,
                "calls": [
                    {
                        "sequence": 1,
                        "call_kind": "structured_probe",
                        "outcome": "ERROR",
                        "error_code": "MALFORMED_RESPONSE",
                        "injected_probe": True,
                    }
                ],
            }
        )
    public = {
        "variant": spec.variant,
        "provider_id": f"fixture-{spec.variant}",
        "adapter_kind": spec.adapter_kind,
        "endpoint_class": "fixture",
        "model": "fixture",
        "structured_output": spec.structured_output,
        "configured": True,
        "missing_configuration": [],
    }
    return PortabilityAttemptResult(
        scenario=scenario,
        provider=public,
        status=AttemptStatus.FIXTURE,
        probes=tuple(probes),
        provider_evidence=provider_evidence,
    )


def run_fixture() -> tuple[PortabilityAttemptResult, ...]:
    specs = default_provider_specs()
    return tuple(
        fixture_attempt(scenario, spec)
        for scenario in build_scenarios()
        for spec in specs
    )


def build_report(
    attempts: Iterable[PortabilityAttemptResult],
    *,
    source: str,
    commit: str,
) -> dict[str, Any]:
    values = tuple(attempts)
    grouped: dict[str, list[PortabilityAttemptResult]] = {}
    for attempt in values:
        grouped.setdefault(attempt.scenario.case.id, []).append(attempt)
    pairs = tuple(evaluate_pair(grouped[case_id]) for case_id in sorted(grouped))
    real_executed = sum(attempt.status is AttemptStatus.EXECUTED for attempt in values)
    deferred = sum(attempt.status is AttemptStatus.DEFERRED for attempt in values)
    fixture = sum(attempt.status is AttemptStatus.FIXTURE for attempt in values)
    runtime_pass = sum(attempt.runtime_correctness == "PASS" for attempt in values)
    capability_counts = {
        outcome: sum(attempt.capability_outcome == outcome for attempt in values)
        for outcome in ("PASS", "PARTIAL", "FAIL")
    }
    provider_error_count = sum(
        max(
            int((attempt.provider_evidence or {}).get("error_count", 0) or 0)
            - int(
                (attempt.provider_evidence or {}).get(
                    "injected_error_count", 0
                )
                or 0
            ),
            0,
        )
        for attempt in values
    )
    injected_probe_errors = sum(
        int(
            (attempt.provider_evidence or {}).get("injected_error_count", 0)
            or 0
        )
        for attempt in values
    )
    scenarios = build_scenarios()
    return {
        "suite": "P2-P Provider Portability Harness",
        "source": source,
        "scope": (
            "fixture contract/oracle/report validation; no real Provider acceptance"
            if source == "fixture"
            else "one attempt per fixed case/provider; no automatic rerun or reprompt"
        ),
        "commit": commit,
        "automatic_rerun": False,
        "provider_specific_prompting": False,
        "dataset": benchmark_metadata(tuple(scenario.case for scenario in scenarios)),
        "full_p2_dataset_hash": dataset_hash(build_cases()),
        "scenario_manifest_hash": _canonical_hash(
            [scenario.public_contract() for scenario in scenarios]
        ),
        "summary": {
            "attempts": len(values),
            "real_executed": real_executed,
            "fixture_attempts": fixture,
            "deferred": deferred,
            "runtime_correctness_pass": runtime_pass,
            "capability": capability_counts,
            "provider_error_count": provider_error_count,
            "injected_probe_error_count": injected_probe_errors,
            "pair_runtime_pass": sum(pair.runtime_correctness == "PASS" for pair in pairs),
        },
        "pairs": [pair.to_dict() for pair in pairs],
        "attempts": [attempt.to_dict() for attempt in values],
    }


def _commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _write_report(report: Mapping[str, Any], target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="P2-P Provider portability harness")
    parser.add_argument("--mode", choices=("fixture", "real"), default="fixture")
    parser.add_argument("--ids", default="P01,P02,P03")
    parser.add_argument(
        "--results",
        default=os.environ.get("TSAGENT_P2_P_RESULTS", "/private/tmp/p2_p_fixture.json"),
    )
    parser.add_argument(
        "--work-root",
        default=os.environ.get("TSAGENT_P2_P_WORK_ROOT", "/private/tmp/tsagent-p2-p"),
    )
    args = parser.parse_args()
    selected = tuple(item.strip() for item in args.ids.split(",") if item.strip())
    scenarios = tuple(scenario_by_id(case_id) for case_id in selected)
    specs = default_provider_specs()
    if args.mode == "fixture":
        attempts = tuple(
            fixture_attempt(scenario, spec)
            for scenario in scenarios
            for spec in specs
        )
    else:
        # Import only in real mode.  The runner will use one fresh process per
        # case/provider and return DEFERRED without launching when config is
        # absent.
        from .portability_worker import run_real_matrix

        attempts = run_real_matrix(
            scenarios,
            specs,
            work_root=Path(args.work_root),
        )
    report = build_report(attempts, source=args.mode, commit=_commit())
    target = Path(args.results)
    _write_report(report, target)
    print(
        f"P2-P {args.mode}: attempts={report['summary']['attempts']} "
        f"runtime_pass={report['summary']['runtime_correctness_pass']} "
        f"deferred={report['summary']['deferred']} results={target}"
    )
    if args.mode == "fixture":
        print("WARNING: fixture evidence is not real Provider or Runtime acceptance")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AttemptStatus",
    "FixtureFile",
    "PortabilityAttemptResult",
    "PortabilityPairResult",
    "PortabilityProbe",
    "PortabilityProbeResult",
    "PortabilityRuntimeScore",
    "PortabilityScenario",
    "ProbeKind",
    "build_report",
    "build_scenarios",
    "deferred_attempt",
    "evaluate_pair",
    "fixture_attempt",
    "main",
    "materialize_fixtures",
    "run_fixture",
    "scenario_by_id",
    "score_runtime",
]
