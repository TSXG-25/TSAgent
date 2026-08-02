from agent.registry.skill_registry import skill_registry, Skill

skill_registry.register(
    Skill(
        name="chat",
        description="日常对话、概念解释、纯问答，不需要调用工具",
        planner_hint="生成单个直接回答任务，不要拆分为工具步骤。",
    )
)