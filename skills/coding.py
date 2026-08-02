from agent.registry.skill_registry import skill_registry, Skill

skill_registry.register(
    Skill(
        name="coding",
        description="修复 bug、生成代码、读写文件、应用补丁、运行 shell 命令",
        planner_hint=(
            "优先使用 read_file、write_file、propose_patch、apply_patch、shell 工具。"
            "修复代码时：先读取文件，再生成补丁，再应用补丁，最后验证。"
        ),
    )
)