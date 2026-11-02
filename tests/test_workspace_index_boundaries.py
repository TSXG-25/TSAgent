"""Workspace index traversal must prune dependency and runtime-state trees."""

from pathlib import Path

from agent.workspace.index import ProjectIndex


def test_project_index_prunes_dependency_and_runtime_state_directories(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "index.js").write_text("module.exports = 1\n", encoding="utf-8")
    (tmp_path / "venv" / "lib").mkdir(parents=True)
    (tmp_path / "venv" / "lib" / "ignored.py").write_text("VALUE = 2\n", encoding="utf-8")
    (tmp_path / ".tsagent" / "runtime_store").mkdir(parents=True)
    (tmp_path / ".tsagent" / "runtime_store" / "state.json").write_text("{}\n", encoding="utf-8")
    (tmp_path / "nested-repository" / ".git").mkdir(parents=True)
    (tmp_path / "nested-repository" / "source.py").write_text("VALUE = 4\n", encoding="utf-8")

    index = ProjectIndex(tmp_path)
    index.build()

    assert index.all_files() == ["src/main.py"]


def test_project_index_refresh_uses_the_same_pruned_traversal(tmp_path: Path) -> None:
    (tmp_path / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    index = ProjectIndex(tmp_path)
    index.build()

    (tmp_path / "node_modules" / "pkg").mkdir(parents=True)
    (tmp_path / "node_modules" / "pkg" / "generated.py").write_text("VALUE = 2\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("VALUE = 3\n", encoding="utf-8")
    index.refresh()

    assert index.all_files() == ["app.py"]
    assert index.lookup("app.py").hash != ""
