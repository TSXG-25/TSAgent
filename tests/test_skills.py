"""SKILL.md loading and the load_skill tool."""
from __future__ import annotations

import pytest

from agent.skills import load_skills


def _skill(tmp_path, name: str, body: str) -> None:
    directory = tmp_path / name
    directory.mkdir(parents=True)
    (directory / "SKILL.md").write_text(body, encoding="utf-8")


def test_load_skills_reads_frontmatter(tmp_path):
    _skill(
        tmp_path,
        "coding",
        "---\nname: coding\ndescription: 写代码\n---\n\n详细流程。",
    )
    skills = load_skills(tmp_path)
    assert skills["coding"].description == "写代码"
    assert skills["coding"].body == "详细流程。"


def test_load_skills_requires_frontmatter(tmp_path):
    _skill(tmp_path, "broken", "no frontmatter here")
    with pytest.raises(ValueError, match="frontmatter"):
        load_skills(tmp_path)


def test_load_skills_requires_name_and_description(tmp_path):
    _skill(tmp_path, "bad", "---\nname: bad\n---\n\nbody")
    with pytest.raises(ValueError, match="name and description"):
        load_skills(tmp_path)


def test_load_skills_rejects_duplicate_names(tmp_path):
    body = "---\nname: dup\ndescription: x\n---\n\nbody"
    _skill(tmp_path, "a", body)
    _skill(tmp_path, "b", body)
    with pytest.raises(ValueError, match="duplicate"):
        load_skills(tmp_path)


def test_load_skill_tool_returns_body_and_errors():
    import tools.skills  # noqa: F401  (registers the tool)
    from agent import skills as skills_module
    from tools.skills import load_skill

    original = dict(skills_module._loaded)
    try:
        skills_module._loaded.clear()
        skills_module._loaded.update(
            {"coding": skills_module.Skill(
                name="coding", description="d", body="coding body"
            )}
        )
        assert load_skill("coding") == "coding body"
        assert "UNKNOWN_SKILL" in load_skill("nope")
    finally:
        skills_module._loaded.clear()
        skills_module._loaded.update(original)


def test_project_skills_load_from_repo():
    from agent.skills import load_project_skills

    loaded = load_project_skills()
    assert set(loaded) == {"coding", "research", "code-animation-prompts"}


def test_workspace_skills_overlay_global_index(tmp_path):
    from agent.skills import merged_skills, skills_index, workspace_skills

    private = tmp_path / "skills" / "proj"
    private.mkdir(parents=True)
    (private / "SKILL.md").write_text(
        "---\nname: proj\ndescription: private\n---\nworkspace body\n",
        encoding="utf-8",
    )
    loaded = workspace_skills(tmp_path)
    assert loaded["proj"].body == "workspace body"

    merged = merged_skills(tmp_path)
    assert set(merged) == set(skills_index()) | {"proj"}
    # Same-name workspace skill wins (private layer owns its territory).
    clash = tmp_path / "skills" / "coding"
    clash.mkdir(parents=True)
    (clash / "SKILL.md").write_text(
        "---\nname: coding\ndescription: override\n---\nprivate coding\n",
        encoding="utf-8",
    )
    assert merged_skills(tmp_path)["coding"].body == "private coding"


def test_workspace_skills_absent_returns_empty(tmp_path):
    from agent.skills import workspace_skills

    assert workspace_skills(tmp_path) == {}
    assert workspace_skills(None) == {}
