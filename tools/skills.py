"""load_skill tool — pull a SKILL.md body into the conversation."""
from agent.registry.tool_registry import registry
from agent.skills import skills_index


def load_skill(name: str) -> str:
    """加载指定技能的完整操作说明。

    仅在任务确实需要该技能描述的专门流程时调用；参数必须是
    工具索引中列出的技能名。

    Args:
        name: 技能名（如 coding、research）

    Returns:
        该技能的完整说明文本
    """
    skills = skills_index()
    skill = skills.get(str(name).strip())
    if skill is None:
        available = ", ".join(sorted(skills)) or "无"
        return f"UNKNOWN_SKILL: '{name}' 不存在。可用技能: {available}"
    return skill.body


registry.register(load_skill, category="skills", tags=["skills", "instructions"])
