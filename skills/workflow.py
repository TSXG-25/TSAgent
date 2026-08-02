from agent.registry.skill_registry import skill_registry, Skill

skill_registry.register(
    Skill(
        name="workflow",
        description="多步骤复杂任务，需要按顺序完成多个子目标",
        planner_hint="将任务拆分为清晰的顺序步骤，每步对应一个可执行目标。",
    )
)
