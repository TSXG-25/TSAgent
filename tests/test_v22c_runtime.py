"""v2.2C smoke regressions that do not require a Provider."""
from __future__ import annotations

from agent.checkpoint import ArtifactSnapshot
from agent.workflow import ExecutionContext, hydrate_checkpoint_artifacts
from benchmarks.v22c.offline_dryrun import main as offline_dryrun_main


def test_v22c_offline_c02_c03_c07_gate_passes():
    assert offline_dryrun_main() == 0


def test_artifact_hydration_requires_matching_file_digest(tmp_path):
    path = tmp_path / "spec.md"
    path.write_text("# spec\n", encoding="utf-8")
    from agent.checkpoint.recorder import fact_digest

    context = ExecutionContext()
    report = hydrate_checkpoint_artifacts(
        (
            ArtifactSnapshot(
                artifact_id="spec-1",
                artifact_type="spec_content",
                digest=fact_digest("# spec\n"),
                reference=str(path),
                metadata=(
                    ("encoding", "utf-8"),
                    ("producer_stage_id", "read_spec"),
                ),
            ),
        ),
        context,
    )
    assert report.hydrated_types == ("spec_content",)
    assert context.get_artifact("spec_content").content == "# spec\n"

    path.write_text("tampered\n", encoding="utf-8")
    tampered_context = ExecutionContext()
    tampered = hydrate_checkpoint_artifacts(
        (
            ArtifactSnapshot(
                artifact_id="spec-1",
                artifact_type="spec_content",
                digest=fact_digest("# spec\n"),
                reference=str(path),
            ),
        ),
        tampered_context,
    )
    assert tampered.mismatched_types == ("spec_content",)
    assert tampered_context.get_artifact("spec_content") is None


def test_artifact_hydration_rejects_snapshot_marked_missing(tmp_path):
    path = tmp_path / "spec.md"
    path.write_text("# spec\n", encoding="utf-8")
    context = ExecutionContext()
    report = hydrate_checkpoint_artifacts(
        (
            ArtifactSnapshot(
                artifact_id="spec-1",
                artifact_type="spec_content",
                digest="ignored",
                exists=False,
                reference=str(path),
            ),
        ),
        context,
    )
    assert report.missing_types == ("spec_content",)
    assert context.get_artifact("spec_content") is None
