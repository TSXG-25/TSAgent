"""H5b: failed Runs must disclose verified partial artifacts."""

from main import ServiceCLI
from agent.service import (
    ArtifactSummary,
    FailureSummary,
    RunOutput,
    RunSnapshot,
    RunStatus,
)


def _failed_snapshot() -> RunSnapshot:
    return RunSnapshot(
        tenant_id="tenant-a",
        run_id="run-partial",
        session_id="session-a",
        status=RunStatus.FAILED_TERMINAL,
        request_text="分析源码并写总结",
        active_workflow_id=None,
        request_id="request-partial",
        created_at="2026-08-13T00:00:00Z",
        updated_at="2026-08-13T00:00:02Z",
        artifacts=(
            ArtifactSummary(
                artifact_id="artifact-summary",
                artifact_type="file",
                digest="a" * 64,
                reference="artifact://tenant-a/run-partial/artifact-summary",
                exists=True,
                verified=True,
                display_name="output/agent_roles_summary.md",
            ),
        ),
        output=RunOutput(
            run_id="run-partial",
            revision=2,
            text="任务未完成，后续源码修改未执行。",
            artifact_ids=("artifact-summary",),
            created_at="2026-08-13T00:00:02Z",
        ),
        failure_summary=FailureSummary(
            code="RUNTIME_BUDGET_EXHAUSTED",
            message="执行达到时间或步骤上限",
            retryable=False,
        ),
    )


def test_cli_discloses_output_artifact_and_failure_together(capsys) -> None:
    ServiceCLI._print_snapshot(_failed_snapshot())

    rendered = capsys.readouterr().out
    assert "任务未完成" in rendered
    assert "output/agent_roles_summary.md" in rendered
    assert "RUNTIME_BUDGET_EXHAUSTED" in rendered


def test_verified_artifacts_are_not_rendered_as_success_status(capsys) -> None:
    ServiceCLI._print_snapshot(_failed_snapshot())

    rendered = capsys.readouterr().out
    assert "状态: FAILED_TERMINAL" in rendered
    assert "状态: COMPLETED" not in rendered
