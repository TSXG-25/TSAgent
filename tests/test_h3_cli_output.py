"""CLI must render durable RunOutput instead of only the terminal status."""

from main import ServiceCLI
from agent.service import (
    FailureSummary,
    RunOutput,
    RunSnapshot,
    RunStatus,
)


def _snapshot(
    *,
    output: RunOutput | None = None,
    failure_summary: FailureSummary | None = None,
) -> RunSnapshot:
    return RunSnapshot(
        tenant_id="tenant-a",
        run_id="run-a",
        session_id="session-a",
        status=RunStatus.COMPLETED if output is not None else RunStatus.BLOCKED,
        request_text="请求",
        active_workflow_id=None,
        request_id="request-a",
        created_at="2026-08-10T00:00:00Z",
        updated_at="2026-08-10T00:00:01Z",
        output=output,
        failure_summary=failure_summary,
    )


def test_cli_renders_durable_user_visible_output(capsys) -> None:
    ServiceCLI._print_snapshot(
        _snapshot(
            output=RunOutput(
                run_id="run-a",
                revision=3,
                text="这是实际返回给用户的分析结果。",
                created_at="2026-08-10T00:00:01Z",
            )
        )
    )

    rendered = capsys.readouterr().out
    assert "COMPLETED" in rendered
    assert "这是实际返回给用户的分析结果。" in rendered


def test_cli_renders_failure_when_no_output_exists(capsys) -> None:
    ServiceCLI._print_snapshot(
        _snapshot(
            failure_summary=FailureSummary(
                code="MISSING_USER_OUTPUT",
                message="没有可展示的用户输出",
                retryable=False,
            )
        )
    )

    rendered = capsys.readouterr().out
    assert "BLOCKED" in rendered
    assert "MISSING_USER_OUTPUT" in rendered
    assert "没有可展示的用户输出" in rendered
