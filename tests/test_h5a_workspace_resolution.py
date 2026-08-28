"""H5a: exact directory identity must beat fuzzy indexed candidates."""

from pathlib import Path

import tools.filesystem as filesystem


class _FuzzyFileWorkspace:
    def __init__(self, candidate: Path) -> None:
        self.candidate = candidate

    def resolve(self, _path: str):
        return [type("Match", (), {"path": self.candidate})()]


def test_list_directory_prefers_exact_directory_after_workspace_index(
    monkeypatch,
    tmp_path: Path,
) -> None:
    directory = tmp_path / "agent" / "checkpoint"
    directory.mkdir(parents=True)
    (directory / "__init__.py").write_text("", encoding="utf-8")
    fuzzy_file = directory / "__init__.py"

    monkeypatch.setattr(filesystem, "ROOT", tmp_path)
    monkeypatch.setattr(filesystem, "_working_directory", ".")
    monkeypatch.setattr(filesystem, "_path_cache", {})
    monkeypatch.setattr(
        filesystem,
        "_workspace_service",
        _FuzzyFileWorkspace(fuzzy_file),
    )

    result = filesystem.list_directory("agent/checkpoint")

    assert "不是有效目录" not in result
    assert "agent/checkpoint/" in result
    assert "__init__.py" in result


def test_typed_directory_resolution_does_not_accept_file_candidate(
    monkeypatch,
    tmp_path: Path,
) -> None:
    fuzzy_file = tmp_path / "checkpoint.py"
    fuzzy_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(filesystem, "ROOT", tmp_path)
    monkeypatch.setattr(filesystem, "_working_directory", ".")
    monkeypatch.setattr(filesystem, "_path_cache", {})
    monkeypatch.setattr(
        filesystem,
        "_workspace_service",
        _FuzzyFileWorkspace(fuzzy_file),
    )

    resolved = filesystem._resolve_path(
        "missing-directory",
        expected_kind="directory",
    )

    assert resolved == (tmp_path / "missing-directory").resolve()


def test_boundary_rejection_does_not_trigger_workspace_discovery(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(filesystem, "ROOT", tmp_path)
    monkeypatch.setattr(filesystem, "_working_directory", ".")

    class _UnexpectedDiscovery:
        def resolve(self, _path: str):
            raise AssertionError("boundary violations must fail before discovery")

    monkeypatch.setattr(filesystem, "_workspace_service", _UnexpectedDiscovery())

    result = filesystem.list_directory("../outside")

    assert "超出 workspace" in result
