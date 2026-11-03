"""Skill files — skills/<name>/SKILL.md loaded at bootstrap.

A skill is progressive disclosure: the model sees only name + description in
the tool index and calls ``load_skill(name)`` to pull the full instructions.
Frontmatter is plain ``key: value`` lines between ``---`` markers; ``name``
and ``description`` are required.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str


def _parse_frontmatter(text: str, source: Path) -> tuple[dict[str, str], str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"SKILL.md missing frontmatter: {source}")
    end = next(
        (index for index in range(1, len(lines)) if lines[index].strip() == "---"),
        None,
    )
    if end is None:
        raise ValueError(f"SKILL.md unterminated frontmatter: {source}")
    fields: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise ValueError(f"SKILL.md malformed frontmatter line in {source}: {line!r}")
        fields[key.strip()] = value.strip()
    body = "\n".join(lines[end + 1:]).strip()
    return fields, body


def _parse_skill(path: Path) -> Skill:
    fields, body = _parse_frontmatter(path.read_text(encoding="utf-8"), path)
    name = fields.get("name", "")
    description = fields.get("description", "")
    if not name or not description:
        raise ValueError(f"SKILL.md requires name and description: {path}")
    if not body:
        raise ValueError(f"SKILL.md has no body: {path}")
    return Skill(name=name, description=description, body=body)


def load_skills(root: Path | None = None) -> dict[str, Skill]:
    directory = root or SKILLS_DIR
    loaded: dict[str, Skill] = {}
    if not directory.is_dir():
        return loaded
    for path in sorted(directory.glob("*/SKILL.md")):
        skill = _parse_skill(path)
        if skill.name in loaded:
            raise ValueError(f"duplicate skill name {skill.name!r}: {path}")
        loaded[skill.name] = skill
    return loaded


_loaded: dict[str, Skill] = {}


def load_project_skills(root: Path | None = None) -> dict[str, Skill]:
    """Populate the process skill index; called once by bootstrap."""
    global _loaded
    _loaded = load_skills(root)
    return _loaded


def skills_index() -> dict[str, Skill]:
    return _loaded
