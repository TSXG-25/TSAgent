"""Cognition Layer — 认知层。

包含：
- CognitiveContext: 统一上下文数据模型
- ConversationState: 跨轮对话状态
- ResolvedQuery: 消歧后的查询
- ReferenceResolver: 上下文引用消歧引擎
- IntentEngine: 意图理解引擎
- IntentResult: 结构化意图结果
"""
from .cognitive_context import CognitiveContext, ConversationState, ResolvedQuery
from .intent_schema import IntentResult, ALL_DOMAINS, DOMAIN_DESCRIPTIONS
from .intent_engine import IntentEngine
from .reference_resolver import ReferenceResolver

__all__ = [
    "CognitiveContext", "ConversationState", "ResolvedQuery",
    "IntentResult", "ALL_DOMAINS", "DOMAIN_DESCRIPTIONS",
    "IntentEngine", "ReferenceResolver",
]
