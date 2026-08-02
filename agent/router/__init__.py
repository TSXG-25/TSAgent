"""Router Layer — 执行路由层。

接收 IntentEngine 的输出 (domain, action)，决定：
- workflow_router: 哪个执行链负责
- skill_router: 哪个能力模块
- tool_router: 调哪个工具
"""
from .workflow_router import WorkflowRouter
from .skill_router import SkillRouter

__all__ = ["WorkflowRouter", "SkillRouter"]