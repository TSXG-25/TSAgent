import pytest

from agent.workflow_decision import WorkflowDecision, WorkflowDecisionKind
from agent.workflow_selector import WorkflowSelection, WorkflowSelectionEvidence


class _DeclineWorkflowSelector:
    """Offline default: no specialized workflow is selected.

    Patches only the selector the orchestrator constructs itself; tests that
    inject or call ``agent.workflow_selector.WorkflowDecisionSelector``
    directly exercise the real implementation.
    """

    async def select_with_evidence(self, goal, context, available_workflows):
        return WorkflowSelection(
            decision=WorkflowDecision(
                kind=WorkflowDecisionKind.DECLINE,
                reason="offline test default: no workflow",
            ),
            evidence=WorkflowSelectionEvidence(
                provider="test",
                provider_path="SINGLE_PROVIDER",
                format_path="RAW_ONLY",
                raw_output="decline",
                structured_error="",
            ),
        )


@pytest.fixture(autouse=True)
def _stub_workflow_selector(monkeypatch):
    monkeypatch.setattr(
        "agent.orchestrator.main.WorkflowDecisionSelector",
        lambda *a, **kw: _DeclineWorkflowSelector(),
    )
