"""SkillRouter — 能力路由。

接收 IntentEngine 的 domain，选择对应的 Skill。
当 workflow_router 未命中时，skill_router 提供 fallback。
"""
from typing import Optional
from agent.registry.skill_registry import skill_registry
from agent.cognition.intent_schema import IntentResult, DOMAIN_CHAT, DOMAIN_DEVELOPMENT, DOMAIN_FILE


class SkillRouter:
    """能力路由。
    
    根据 domain 选择最匹配的 Skill。
    """

    def route(self, intent: IntentResult) -> Optional[str]:
        """返回 skill 名称或 None。"""
        # Chat domain → no skill needed
        if intent.is_chat:
            return None
        
        # Use skill_registry's embedding-based selection as fallback
        skill = skill_registry.select(intent.raw_input)
        if skill:
            return skill.name
        
        return None


# 全局单例
router = SkillRouter()