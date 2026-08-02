from agent.registry.skill_registry import skill_registry, Skill

skill_registry.register(
    Skill(
        name="research",
        description="搜索网络信息、调研资料、查找外部知识",
        planner_hint="优先使用 web_search 工具获取信息，再整理回答。",
    )
)