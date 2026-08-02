# tools/memory.py
"""Memory query tools for the agent.

Provides tools to query and manage the agent's three-layer memory system:
- Layer 2: Short-term (recent conversations)
- Layer 3: Long-term (semantic summaries + user facts)
- Legacy: Semantic memory + preferences
"""
from agent.registry.tool_registry import registry


def query_memory(query: str, k: int = 5, user_id: str = "default") -> str:
    """查询语义记忆和长期摘要，检索与查询最相关的历史记忆。

    综合查询会话记录、短期记忆、长期摘要记忆和语义记忆。

    Args:
        query: 搜索查询文本
        k: 返回结果数量（默认 5）
        user_id: 用户名（默认 "default"）

    Returns:
        检索到的记忆片段列表
    """
    from agent.services import MemoryService
    from agent.memory.session import get_session_context

    parts = []

    # Session context (current conversation)
    session = get_session_context(user_id, n=5)
    if session:
        parts.append("【当前会话】\n" + session)

    # Short-term history
    short_term = MemoryService.get_short_term_history(user_id, n=3)
    if short_term:
        parts.append("【近期对话】\n" + short_term)

    # Long-term summaries
    long_term = MemoryService.retrieve_long_term(user_id, query, k=k)
    if long_term:
        parts.append("【历史摘要】\n" + long_term)

    # Legacy semantic
    legacy = MemoryService.retrieve_semantic(user_id, query, k=k)
    if legacy:
        parts.append("【语义记忆】\n" + legacy)

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

    # Legacy preferences
    prefs = MemoryService.get_preferences(user_id)
    if prefs:
        lines = [f"{k}: {v}" for k, v in prefs.items()]
        result_parts.append("偏好设置：\n" + "\n".join(lines))

    if not result_parts:
        return f"用户 {user_id} 暂无事实或偏好设置。"

    return "\n\n".join(result_parts)


def save_fact(fact: str, user_id: str = "default") -> str:
    """保存一条关于用户的事实信息到记忆系统。

    通过 LLM 提取事实并存储到长期记忆的事实数据库。

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
        loop.create_task(MemoryService.extract_and_save_facts(user_id, fact))
        return f"事实已调度保存: {fact[:100]}..."
    except RuntimeError:
        asyncio.run(MemoryService.extract_and_save_facts(user_id, fact))
        return f"事实已保存: {fact[:100]}..."


def get_session_info(user_id: str = "default") -> str:
    """获取当前会话的详细信息，包括消息数量、会话主题和最近对话内容。

    Args:
        user_id: 用户名（默认 "default"）

    Returns:
        详细的会话信息
    """
    from agent.memory.session import get_message_count, get_last_topic, get_session_context
    count = get_message_count(user_id)
    topic = get_last_topic(user_id)
    if count == 0:
        return "当前没有活跃会话。"

    # Get recent messages
    recent = get_session_context(user_id, n=5)
    
    lines = [f"## 会话信息"]
    lines.append(f"消息数量: {count}")
    if topic:
        lines.append(f"当前话题: {topic}")
    if recent:
        lines.append(f"\n最近对话:\n{recent}")
    
    return "\n".join(lines)


# 注册工具
registry.register(query_memory, category="memory", tags=["memory", "query", "semantic"])
registry.register(get_user_preference, category="memory", tags=["memory", "preference", "facts"])
registry.register(save_fact, category="memory", tags=["memory", "fact", "save"])
registry.register(get_session_info, category="memory", tags=["memory", "session", "info"])