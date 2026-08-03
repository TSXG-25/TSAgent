# agent/services/memory_service.py
"""Unified Memory Service — three-layer architecture.

Provides a single get_context() entry point that returns structured
memory context from all three layers.
"""

from typing import Optional


class MemoryService:
    # ===== Layer 1: Session (in-memory, always on) =====

    @staticmethod
    def record_user_message(user_id: str, content: str) -> None:
        """Record user message in session memory."""
        from agent.memory.session import add_user_message
        add_user_message(user_id, content)

    @staticmethod
    def record_assistant_message(user_id: str, content: str) -> None:
        """Record assistant message in session memory."""
        from agent.memory.session import add_assistant_message
        add_assistant_message(user_id, content)

    @staticmethod
    def get_session_context(user_id: str, n: int = 10) -> str:
        """Get current session conversation history."""
        from agent.memory.session import get_session_context as _get
        return _get(user_id, n=n)

    # ===== Layer 2: Short-Term (persistent, compressed) =====

    @staticmethod
    def add_exchange(user_id: str, user_input: str, assistant_response: str) -> None:
        """Record an exchange in short-term memory. Triggers auto-compression."""
        from agent.memory.short_term import add_exchange as _add
        _add(user_id, user_input, assistant_response)

    @staticmethod
    def get_short_term_history(user_id: str, n: int = 6) -> str:
        """Get recent short-term conversation history."""
        from agent.memory.short_term import get_history as _get
        return _get(user_id, n=n)

    # ===== Layer 3: Long-Term (ChromaDB semantic + SQLite facts) =====

    @staticmethod
    def retrieve_long_term(user_id: str, query: str, k: int = 5) -> str:
        """Retrieve semantically relevant long-term summaries."""
        from agent.memory.long_term import retrieve_summaries
        return retrieve_summaries(user_id, query, k=k)

    @staticmethod
    def store_summary(user_id: str, summary: str) -> None:
        """Store a conversation summary in long-term memory."""
        from agent.memory.long_term import store_summary
        store_summary(user_id, summary)

    # ===== User Facts =====

    @staticmethod
    def get_user_facts(user_id: str) -> str:
        """Get user facts as readable text."""
        from agent.memory.long_term import get_facts_text
        return get_facts_text(user_id)

    # ===== Layer 4: Cross-Session Resolution Memory（v1.2C）=====

    @staticmethod
    def record_resolution(user_id: str, utterance: str, resolved_target: str, kind: str, metadata: dict = None) -> None:
        """记录跨会话解析事实（Facts，非 ResolutionResult）。"""
        from agent.memory.resolution import record_resolution as _record
        _record(user_id, utterance, resolved_target, kind, metadata)

    @staticmethod
    def get_resolutions(user_id: str, n: int = 20) -> list:
        """最近 N 条跨会话解析事实。"""
        from agent.memory.resolution import get_resolutions as _get
        return _get(user_id, n=n)

    # ===== Legacy compatibility =====

    @staticmethod
    def get_preferences(user_id: str) -> dict:
        """Legacy: get user preferences dict."""
        from agent.memory.preference import get_user_preferences
        return get_user_preferences(user_id)

    @staticmethod
    def add_conversation(user_id: str, user_input: str, assistant_response: str):
        """Legacy: add to semantic conversation memory."""
        from agent.memory.semantic import add_conversation_memory
        add_conversation_memory(user_id, user_input, assistant_response)

    @staticmethod
    def retrieve_semantic(user_id: str, query: str, k: int = 3) -> str:
        """Legacy: retrieve from semantic memory."""
        from agent.memory.semantic import retrieve_similar_memories
        return retrieve_similar_memories(user_id, query, k=k)

    @staticmethod
    def record_conversation(user_id: str, user_input: str, assistant_response: str):
        """Legacy: record in conversation log."""
        from agent.memory.conversation import add_exchange as _conv_add
        _conv_add(user_id, user_input, assistant_response)

    @staticmethod
    def get_recent_conversation(user_id: str, n: int = 5) -> str:
        """Legacy: get recent conversation log."""
        from agent.memory.conversation import get_recent as _get
        return _get(user_id, n=n)

    # ===== Unified Interface =====

    @staticmethod
    def _filter_negative_context(text: str) -> str:
        """Filter out negative/empty results from memory context.

        Removes lines containing '未找到' or '无信息' etc. to prevent
        LLM from treating "no data found" as factual information.
        """
        if not text:
            return text
        negative_patterns = [
            "未找到", "无信息", "无相关", "没有信息", "找不到",
            "暂无信息", "nothing", "no results", "not found",
        ]
        lines = text.split("\n")
        filtered = [l for l in lines if not any(p in l for p in negative_patterns)]
        result = "\n".join(filtered).strip()
        return result if result else ""

    @staticmethod
    def get_context(user_id: str, query: str) -> dict:
        """Get unified memory context from all layers.

        Returns a dict suitable for injecting into the system prompt:
        {
            "session": str,        # Current session history (always on)
            "short_term": str,     # Recent conversations (always on)
            "long_term": str,      # Semantic long-term summaries (on relevance)
            "facts": str,          # Extracted user facts
            "preferences": str,    # Legacy preferences
        }

        Negative results ("未找到" etc.) are filtered from all fields.
        """
        result = {
            "session": MemoryService.get_session_context(user_id, n=8) or "",
            "short_term": MemoryService._filter_negative_context(
                MemoryService.get_short_term_history(user_id, n=5)
            ) or "",
            "long_term": MemoryService._filter_negative_context(
                MemoryService.retrieve_long_term(user_id, query, k=3)
            ) or "",
            "facts": MemoryService.get_user_facts(user_id) or "",
            "preferences": "",
        }

        # Legacy preferences
        prefs = MemoryService.get_preferences(user_id)
        if prefs:
            result["preferences"] = "\n".join(
                f"{k}: {v}" for k, v in prefs.items()
            )

        return result

    # ===== Fact Extraction =====

    @staticmethod
    async def extract_and_save_facts(user_id: str, text: str) -> dict:
        """Extract and save facts from user input. Always attempts extraction."""
        from agent.memory.preference import async_extract_and_save_facts
        return await async_extract_and_save_facts(user_id, text)

    # ===== Record Full Exchange =====

    @staticmethod
    def record_full_exchange(
        user_id: str,
        user_input: str,
        assistant_response: str,
    ) -> None:
        """Record a complete exchange in all memory layers.

        Called after each successful round-trip.
        """
        # Layer 1: Session
        MemoryService.record_user_message(user_id, user_input)
        MemoryService.record_assistant_message(user_id, assistant_response)

        # Layer 2: Short-term (persistent, triggers auto-compression)
        MemoryService.add_exchange(user_id, user_input, assistant_response)

        # Legacy: semantic memory + conversation log (maintain compatibility)
        MemoryService.add_conversation(user_id, user_input, assistant_response)
        MemoryService.record_conversation(user_id, user_input, assistant_response)