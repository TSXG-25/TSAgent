"""ProjectKnowledge — knows what the system can do.

Scans Registry to answer questions like:
- What workflows are available?
- What tools are registered?
- What skills exist?

This is separate from Workspace (which knows WHERE files are).
Knowledge reads Registry, not filesystem.
"""
from typing import Any


class ProjectKnowledge:
    """Project-level knowledge about system capabilities.

    Built from Registry after tools/skills/workflows are loaded.
    """

    def __init__(self):
        self._workflows: list[dict] = []
        self._tools: list[dict] = []
        self._skills: list[dict] = []
        self._built = False

    def build(self) -> None:
        """Scan all registries and build knowledge index."""
        self._scan_workflows()
        self._scan_tools()
        self._scan_skills()
        self._built = True

    def _scan_workflows(self) -> None:
        try:
            from agent.registry.workflow_registry import workflow_registry
            workflows = workflow_registry.list_workflows()
            self._workflows = [
                {"id": wf.id, "name": wf.name if hasattr(wf, 'name') else wf.id}
                for wf in workflows
            ]
        except (ImportError, AttributeError):
            self._workflows = []

    def _scan_tools(self) -> None:
        try:
            from agent.registry.tool_registry import registry
            tools = registry.list_tools()
            self._tools = [
                {"name": t.get("name", str(t)), "category": t.get("category", "")}
                for t in tools
            ]
        except (ImportError, AttributeError):
            self._tools = []

    def _scan_skills(self) -> None:
        try:
            from agent.registry.skill_registry import skill_registry
            skills = skill_registry.list_skills()
            self._skills = [
                {"name": s.name, "description": s.description if hasattr(s, 'description') else ""}
                for s in skills
            ]
        except (ImportError, AttributeError):
            self._skills = []

    @property
    def workflows(self) -> list[dict]:
        return list(self._workflows)

    @property
    def tools(self) -> list[dict]:
        return list(self._tools)

    @property
    def skills(self) -> list[dict]:
        return list(self._skills)

    @property
    def summary(self) -> str:
        """Human-readable summary of project capabilities."""
        lines = [
            f"📋 项目能力概览",
            f"   Workflows: {len(self._workflows)} 个",
            f"   Tools:     {len(self._tools)} 个",
            f"   Skills:    {len(self._skills)} 个",
        ]
        if self._workflows:
            lines.append(f"   Workflow列表: {', '.join(w['id'] for w in self._workflows[:10])}")
        return "\n".join(lines)