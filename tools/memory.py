# tools/memory.py
"""Memory query tools for the agent.

Provides tools to query and manage the agent's long-term memory layer
(semantic summaries + user facts).  Conversation history lives in the
durable run log, not in a memory layer.
"""
from agent.registry.tool_registry import registry


def query_memory(query: str, k: int = 5, user_id: str = "default") -> str:
    """查询语义记忆和长期摘要，检索与查询最相关的历史记忆。

    综合查询长期摘要记忆和语义记忆。

    Args:
        query: 搜索查询文本
        k: 返回结果数量（默认 5）
        user_id: 用户名（默认 "default"）

    Returns:
        检索到的记忆片段列表
    """
    from agent.services import MemoryService

    parts = []

    # Long-term summaries
    long_term = MemoryService.retrieve_long_term(user_id, query, k=k)
    if long_term:
        parts.append("【历史摘要】\n" + long_term)

    if not parts:
        return "未找到相关记忆。"

    return "\n\n".join(parts)


def get_user_preference(user_id: str = "default") -> str:
    """获取指定用户的偏好设置和已知事实。

    Args:
        user_id: 用户名（默认 "default"）

    Returns:
        用户的事实和偏好设置列表
    """
    from agent.services import MemoryService

    # New facts store
    facts = MemoryService.get_user_facts(user_id)
    result_parts = []
    if facts:
        result_parts.append("已知事实：\n" + facts)

    if not result_parts:
        return f"用户 {user_id} 暂无事实或偏好设置。"

    return "\n\n".join(result_parts)


def save_fact(fact: str, user_id: str = "default") -> str:
    """保存一条关于用户的事实信息到记忆系统。

    通过生产 Memory Learning Provider、授权策略和持久化边界保存事实。

    Args:
        fact: 要保存的事实文本
        user_id: 用户名（默认 "default"）

    Returns:
        保存确认信息
    """
    from agent.services import MemoryService
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(MemoryService.learn_from_interaction(user_id, fact))
        return f"事实保存任务已调度，尚未确认提交: {fact[:100]}..."
    except RuntimeError:
        result = asyncio.run(MemoryService.learn_from_interaction(user_id, fact))
        if result.committed:
            return f"事实已保存: {fact[:100]}..."
        return f"事实未确认保存，未获得持久化提交证据: {fact[:100]}..."



# 注册工具
registry.register(query_memory, category="memory", tags=["memory", "query", "semantic"])
registry.register(get_user_preference, category="memory", tags=["memory", "preference", "facts"])
registry.register(save_fact, category="memory", tags=["memory", "fact", "save"])
