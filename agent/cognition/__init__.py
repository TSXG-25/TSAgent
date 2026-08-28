"""Cognition Layer — 认知层。

包含：
- CognitiveContext: 统一上下文数据模型
- ConversationState: 跨轮对话状态
- ResolvedQuery: 消歧后的查询
- ReferenceResolver: 上下文引用消歧引擎
- IntentEngine: 意图理解引擎
- IntentResult: 结构化意图结果
"""
from .cognitive_context import (
    CognitiveContext,
    PlannerContext,
    ConversationState,
    ResolvedQuery,
)
from .intent_schema import IntentResult, ALL_DOMAINS, DOMAIN_DESCRIPTIONS

# IntentEngine imports the provider stack (and therefore the heavyweight
# langchain/transformers dependency).  The package is also the namespace for
# lightweight policy modules such as execution_need and effect_authorization;
# importing those modules must not initialize the provider stack.  Keep the
# public ``from agent.cognition import IntentEngine`` API while loading the
# two runtime engines only when they are actually requested.
_LAZY_EXPORTS = {
    "IntentEngine": (".intent_engine", "IntentEngine"),
    "ReferenceResolver": (".reference_resolver", "ReferenceResolver"),
}


def __getattr__(name: str):
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    module_name, attribute_name = target
    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value

__all__ = [
    "CognitiveContext", "PlannerContext", "ConversationState", "ResolvedQuery",
    "IntentResult", "ALL_DOMAINS", "DOMAIN_DESCRIPTIONS",
    "IntentEngine", "ReferenceResolver",
]
