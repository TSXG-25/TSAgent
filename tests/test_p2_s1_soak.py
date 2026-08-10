"""Deterministic P2-S1 soak gates over the real Service/SQLite/Context path."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from realtest_reports.harness.p2.groups.soak import (
    run_s01,
    run_s02,
    run_s03,
    run_s04,
)


def _run(coro):
    return asyncio.run(coro)


def test_s01_sequential_runs_have_no_lifecycle_leak(tmp_path: Path) -> None:
    result = _run(run_s01(tmp_path / "s01", run_count=50))

    assert result.runtime_correctness == "PASS"
    assert result.run_count == 50
    assert result.terminal_statuses == {"COMPLETED": 50}
    assert all(result.gates.values())
    assert result.resource_samples[0].label == "baseline"
    assert result.resource_samples[-1].label == "post-close-gc"
    assert result.resource_samples[-1].active_run_contexts == 0
    assert result.resource_samples[-1].event_subscriptions == 0
    assert result.resource_samples[-1].workspace_handles == 0
    assert result.resource_samples[-1].durable_active_runs == 0


def test_s02_session_run_matrix_isolated(tmp_path: Path) -> None:
    result = _run(run_s02(tmp_path / "s02", sessions=10, runs_per_session=5))

    assert result.runtime_correctness == "PASS"
    assert result.run_count == 50
    assert result.terminal_statuses == {"COMPLETED": 50}
    assert result.gates["cross_context_leakage"]
    assert result.gates["workspace_leakage"]
    assert result.gates["subscriber_leak"]


def test_s03_forced_interleaving_same_relative_path_isolated(tmp_path: Path) -> None:
    result = _run(run_s03(tmp_path / "s03", run_count=10))

    assert result.runtime_correctness == "PASS"
    assert result.run_count == 10
    assert result.gates["cross_context_leakage"]
    assert result.gates["workspace_leakage"]
    assert all(count == 1 for count in result.execution_counts.values())
    assert all(count == 1 for count in result.side_effect_counts.values())


def test_s04_replay_does_not_append_or_drift_cursor(tmp_path: Path) -> None:
    result = _run(run_s04(tmp_path / "s04", replay_cycles=500))

    assert result.runtime_correctness == "PASS"
    assert result.replay_cycles == 500
    assert result.gates["event_gap"]
    assert result.gates["replay_does_not_append"]
    assert result.gates["subscriber_leak"]
    assert result.records[0].event_sequences == (1, 2, 3)


def test_s1_result_is_json_serializable(tmp_path: Path) -> None:
    result = _run(run_s03(tmp_path / "json", run_count=3))
    restored = json.loads(json.dumps(result.to_dict(), ensure_ascii=False))

    assert restored["case_id"] == "S03"
    assert restored["runtime_correctness"] == "PASS"
    assert len(restored["records"]) == 3
