"""Architecture checks for the first v2.3A scoped-runtime slice."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_entrypoints_do_not_import_legacy_artifact_or_event_singletons() -> None:
    for relative in ("agent/runtime.py", "agent/session_runtime.py"):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "ArtifactService" not in source
        assert "from agent.event_bus import event_bus" not in source


def test_scoped_context_types_are_the_runtime_ownership_boundary() -> None:
    source = (ROOT / "agent/runtime_context.py").read_text(encoding="utf-8")

    for symbol in ("class ApplicationContext", "class SessionContext", "class RunContext"):
        assert symbol in source
    assert 'ArtifactStore(scope_id=f"run:{self.scope_id}")' in source
    assert 'EventBus(scope_id=f"run:{self.scope_id}")' in source


def test_workspace_legacy_fallback_is_confined_to_compat_adapter() -> None:
    for relative in (
        "agent/orchestrator/context_builder.py",
        "agent/orchestrator/executor.py",
        "agent/grounding/grounder.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "from agent.services.workspace_service import get_workspace_service" not in source

    compat = (ROOT / "agent/compat/workspace.py").read_text(encoding="utf-8")
    assert "get_workspace_service" in compat

    grounder = (ROOT / "agent/grounding/grounder.py").read_text(encoding="utf-8")
    assert "WorkspaceManager.current_workspace" not in grounder

    planner = (ROOT / "agent/orchestrator/planner.py").read_text(encoding="utf-8")
    assert "conversation_retriever as global_conversation_retriever" not in planner

    context_service = (ROOT / "agent/context/context_service.py").read_text(encoding="utf-8")
    assert "from agent.services.artifact_service import ArtifactService" not in context_service
