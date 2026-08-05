"""Conversation Runtime — 当前交互状态机（v2.1B-1，ADR-0013）。

ADR-0013-A：Conversation Runtime stores conversation state, not knowledge.
不属于 Memory 层；recent_goal / last_instruction / last_answer / turn_count
全部是 Runtime 的当前交互状态，不是长期记忆。
"""
from agent.conversation.state import (
    ConversationState,
    ConversationSnapshot,
    ConversationIntent,
    ConversationEvent,
    ConversationTracker,
    ConversationRetriever,
    ConversationRetrieverProtocol,
    ReferenceType,
    classify_conversation_intent,
    render_snapshot,
    resolve_reference_type,
    render_reference,
    conversation_tracker,
    conversation_retriever,
)

__all__ = [
    "ConversationState",
    "ConversationSnapshot",
    "ConversationIntent",
    "ConversationEvent",
    "ConversationTracker",
    "ConversationRetriever",
    "ConversationRetrieverProtocol",
    "ReferenceType",
    "classify_conversation_intent",
    "render_snapshot",
    "resolve_reference_type",
    "render_reference",
    "conversation_tracker",
    "conversation_retriever",
]
