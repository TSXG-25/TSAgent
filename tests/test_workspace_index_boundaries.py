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


def test_repository_index_artifacts_live_inside_index_dir(tmp_path: Path) -> None:
    """Real-eval observation: symbol indexes were written loose into the
    user's project root. They are service-owned artifacts and belong inside
    .repo_index/ next to the vector store."""
    from agent.repository.indexer import RepositoryIndexer

    indexer = RepositoryIndexer(tmp_path)

    assert indexer.symbol_index_path == tmp_path / ".repo_index" / ".symbol_index.json"
    assert indexer.file_symbols_path == tmp_path / ".repo_index" / ".file_symbols.json"
    assert not (tmp_path / ".repo_index").exists(), (
        "a fresh indexer must not eagerly create .repo_index/ in the project"
    )


def test_repository_index_migrates_legacy_root_artifacts(tmp_path: Path) -> None:
    from agent.repository.indexer import (
        FILE_SYMBOLS_FILE,
        SYMBOL_INDEX_FILE,
        RepositoryIndexer,
    )

    (tmp_path / SYMBOL_INDEX_FILE).write_text('{"f": "sym"}', encoding="utf-8")
    (tmp_path / FILE_SYMBOLS_FILE).write_text('{"f": []}', encoding="utf-8")

    indexer = RepositoryIndexer(tmp_path)

    assert indexer.symbol_index == {"f": "sym"}
    assert indexer.file_symbols == {"f": []}
    assert not (tmp_path / SYMBOL_INDEX_FILE).exists()
    assert not (tmp_path / FILE_SYMBOLS_FILE).exists()
    assert (tmp_path / ".repo_index" / SYMBOL_INDEX_FILE).exists()
