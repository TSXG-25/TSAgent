from __future__ import annotations

import subprocess
import sys


def test_minimal_bootstrap_can_defer_workspace_and_optional_registries(monkeypatch) -> None:
    import agent.bootstrap as bootstrap
    import agent.registry.capability_registry as capability_registry

    calls: list[str] = []
    monkeypatch.setattr(bootstrap, "init_workspace", lambda: calls.append("workspace"))
    monkeypatch.setattr(bootstrap, "load_all_tools", lambda: calls.append("tools"))
    monkeypatch.setattr(bootstrap, "load_all_skills", lambda: calls.append("skills"))
    monkeypatch.setattr(bootstrap, "load_all_workflows", lambda: calls.append("workflows"))
    monkeypatch.setattr(bootstrap, "build_knowledge", lambda: calls.append("knowledge"))
    monkeypatch.setattr(
        capability_registry,
        "register_default_capabilities",
        lambda: calls.append("capabilities"),
    )

    bootstrap.load_all(
        include_workspace=False,
        include_workflows=False,
        include_knowledge=False,
    )

    assert calls == ["tools", "skills", "capabilities"]


def test_web_dependencies_are_not_imported_during_registration() -> None:
    code = (
        "import sys; import tools.web; "
        "assert 'httpx' not in sys.modules; "
        "assert 'ddgs' not in sys.modules; "
        "assert 'duckduckgo_search' not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_skill_registry_does_not_import_numpy_until_semantic_selection() -> None:
    code = (
        "import sys; import agent.registry.skill_registry; "
        "assert 'numpy' not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_minimal_bootstrap_does_not_load_heavy_optional_stacks() -> None:
    code = (
        "import sys; from agent.bootstrap import load_all; "
        "load_all(include_workspace=False, include_workflows=False, include_knowledge=False); "
        "assert not any(name in sys.modules for name in "
        "('numpy', 'torch', 'transformers', 'httpx', 'ddgs', 'duckduckgo_search'))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
