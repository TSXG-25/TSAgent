"""Static production-path boundary checks for v2.3B-4."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_durable_production_path_has_no_legacy_persistence_imports() -> None:
    production_files = (
        ROOT / "agent" / "executor" / "executors" / "workflow.py",
        ROOT / "agent" / "checkpoint" / "recorder.py",
        ROOT / "agent" / "run_resume" / "coordinator.py",
    )
    forbidden = (
        "JsonRunResumeStore",
        "JsonCheckpointStore",
        "InMemoryRunResumeStore",
        "InMemoryCheckpointStore",
        "SqliteRuntimeStore(",
    )
    for path in production_files:
        source = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, f"{path} still references legacy writer: {token}"


def test_coordinator_uses_prepare_and_bundle_finalization_boundary() -> None:
    source = (
        ROOT / "agent" / "run_resume" / "coordinator.py"
    ).read_text(encoding="utf-8")
    assert "runtime_store_view" in source
    assert "prepare_operation" in source
    assert "finalize_bundle" in source
    assert "CheckpointStagingBuffer" in source


def test_application_run_scope_owns_durable_store_view() -> None:
    source = (ROOT / "agent" / "runtime_context.py").read_text(encoding="utf-8")
    assert "runtime_store_path" in source
    assert "DurableRuntimeStoreView" in source
    assert "durable_store_view" in source
    assert "durable Runtime Store 模式禁止同时注入 legacy" in source
