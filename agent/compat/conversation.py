"""Compatibility access to the legacy process-global Conversation Runtime."""
from __future__ import annotations

import warnings


def get_legacy_conversation_tracker():
    from agent.conversation import conversation_tracker

    warnings.warn(
        "legacy global ConversationTracker access; pass SessionContext.conversation",
        DeprecationWarning,
        stacklevel=2,
    )
    return conversation_tracker


def get_legacy_conversation_retriever():
    from agent.conversation import conversation_retriever

    warnings.warn(
        "legacy global ConversationRetriever access; pass SessionContext.conversation_retriever",
        DeprecationWarning,
        stacklevel=2,
    )
    return conversation_retriever


__all__ = [
    "get_legacy_conversation_retriever",
    "get_legacy_conversation_tracker",
]
