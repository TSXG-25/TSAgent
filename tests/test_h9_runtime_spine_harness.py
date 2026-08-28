"""Offline H9 harness contract tests."""

import asyncio
from types import SimpleNamespace

from realtest_reports.harness.h9_runtime_spine import _Probe, run_offline


def test_h9_offline_manifest_and_runtime_gate_pass() -> None:
    report = run_offline()

    assert report["manifest_version"] == "h9-runtime-spine-v3"
    assert len(report["records"]) == 20
    assert report["status"] == "PASS"
    assert report["metrics"]["false_completion_rate"] == 0.0
    assert report["metrics"]["runtime_failures"] == 0


def test_h9_offline_keeps_freshness_capability_deferred() -> None:
    report = run_offline()
    freshness = next(item for item in report["records"] if item["case_id"] == "H916")

    assert freshness["capability_outcome"] == "DEFERRED"
    assert freshness["runtime_correctness"] == "PASS"
    assert freshness["terminal_status"] == "BLOCKED"


def test_h9_probe_counts_sync_and_async_provider_calls(monkeypatch) -> None:
    import agent.llm as llm_module

    monkeypatch.setattr(
        llm_module.llm,
        "invoke",
        lambda _messages, **_kwargs: SimpleNamespace(content="sync"),
    )

    async def async_response(_messages, **_kwargs):
        return SimpleNamespace(content="async")

    monkeypatch.setattr(llm_module.llm, "ainvoke", async_response)
    probe = _Probe()
    probe.install()
    try:
        llm_module.llm.invoke([])
        asyncio.run(llm_module.llm.ainvoke([]))
        assert probe.llm_calls == 2
    finally:
        probe.close()
