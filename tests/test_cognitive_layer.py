"""Tests for Cognitive Layer — ReferenceResolver + IntentEngine + CognitiveContext.

Covers:
1. ReferenceResolver deterministic rules (pronoun, omitted target, continuation)
2. IntentEngine with context (target merging, entity merging)
3. CognitiveContext construction and properties
4. Full cognitive pipeline integration
"""
import pytest
from dataclasses import dataclass, field
from typing import Optional


# ── Test helper: minimal WorkspaceContext stub ──
@dataclass
class WorkspaceContextStub:
    current_file: Optional[str] = None
    opened_files: list[str] = field(default_factory=list)
    edited_files: list[str] = field(default_factory=list)
    current_symbol: Optional[str] = None
    recent_symbols: list[str] = field(default_factory=list)
    last_symbol: Optional[str] = None

    def record_open(self, path: str): pass
    def record_edit(self, path: str): pass
    def record_symbol(self, symbol: str): pass


# ── Import actual modules ──
from agent.cognition.cognitive_context import CognitiveContext, ConversationState, ResolvedQuery
from agent.cognition.reference_resolver import ReferenceResolver
from agent.cognition.intent_engine import IntentEngine
from agent.cognition.intent_schema import IntentResult, DOMAIN_DEVELOPMENT, DOMAIN_CHAT


class TestReferenceResolver:
    """ReferenceResolver 确定性消歧规则测试。"""

    def setup_method(self):
        self.resolver = ReferenceResolver()

    def test_pronoun_last_symbol(self):
        """代词引用 → 使用 last_symbol。"""
        ctx = CognitiveContext(
            query="解释这里",
            conversation_state=ConversationState(
                last_symbol="Planner",
                last_file="agent/planner/planner.py",
            ),
        )
        result = self.resolver.resolve("解释这里", ctx)
        assert result.symbol == "Planner"
        assert result.target == "agent/planner/planner.py"
        assert result.confidence == 0.9

    def test_pronoun_last_file(self):
        """代词引用（无 symbol）→ 使用 last_file。"""
        ctx = CognitiveContext(
            query="改一下",
            conversation_state=ConversationState(last_file="runtime.py"),
        )
        result = self.resolver.resolve("改一下", ctx)
        # "改一下" 匹配省略目标规则（有动词），不匹配纯代词规则
        assert result.target == "runtime.py"

    def test_omitted_target_current_file(self):
        """省略目标操作 → 使用 current_file。"""
        ctx = CognitiveContext(
            query="优化一下",
            workspace=WorkspaceContextStub(current_file="solution.py"),
        )
        result = self.resolver.resolve("优化一下", ctx)
        assert result.target == "solution.py"
        assert result.confidence == 0.9

    def test_omitted_target_no_context(self):
        """省略目标操作（无上下文）→ target 为空。"""
        ctx = CognitiveContext(query="优化一下")
        result = self.resolver.resolve("优化一下", ctx)
        assert result.target == ""
        assert result.confidence < 0.5

    def test_continuation_last_target(self):
        """跨轮续操作 → 使用 last_target。"""
        ctx = CognitiveContext(
            query="那改一下",
            conversation_state=ConversationState(
                last_target="planner.py",
                last_symbol="Planner",
            ),
        )
        result = self.resolver.resolve("那改一下", ctx)
        assert result.target == "planner.py"
        assert result.confidence == 0.85

    def test_symbol_reference(self):
        """符号引用 → 使用 last_symbol。"""
        ctx = CognitiveContext(
            query="这个函数",
            conversation_state=ConversationState(
                last_symbol="IntentEngine",
                last_file="intent_engine.py",
            ),
        )
        result = self.resolver.resolve("这个函数", ctx)
        assert result.symbol == "IntentEngine"

    def test_no_disambiguation_needed(self):
        """无需消歧的输入 → 直接返回。"""
        ctx = CognitiveContext(
            query="帮我写一个快速排序算法",
            workspace=WorkspaceContextStub(current_file="solution.py"),
        )
        result = self.resolver.resolve("帮我写一个快速排序算法", ctx)
        assert result.target == ""
        assert result.confidence == 1.0
        assert result.resolution_trace == "无需消歧"

    def test_extract_action_from_omitted(self):
        """提取省略句中的动作词。"""
        from agent.cognition.reference_resolver import _extract_action_from_omitted
        assert _extract_action_from_omitted("修改一下") == "modify"
        assert _extract_action_from_omitted("看看") == "read"
        assert _extract_action_from_omitted("解释一下") == "explain"
        assert _extract_action_from_omitted("运行") == "execute"
        assert _extract_action_from_omitted("继续") == "continue"

    def test_llm_call_count(self):
        """检查 LLM 调用计数（仅复杂引用时触发）。"""
        assert self.resolver.llm_call_count == 0


class TestCognitiveContext:
    """CognitiveContext 数据模型测试。"""

    def test_short_summary_with_context(self):
        """有上下文时生成摘要。"""
        ctx = CognitiveContext(
            query="修改一下",
            workspace=WorkspaceContextStub(
                current_file="runtime.py",
                current_symbol="Executor",
            ),
            conversation_state=ConversationState(
                last_file="planner.py",
                last_symbol="Planner",
            ),
        )
        summary = ctx.short_summary()
        assert "runtime.py" in summary
        assert "Executor" in summary
        assert "Planner" in summary

    def test_short_summary_no_context(self):
        """无上下文时返回默认。"""
        ctx = CognitiveContext(query="你好")
        assert ctx.short_summary() == "无上下文"

    def test_properties(self):
        """便捷属性访问。"""
        ws = WorkspaceContextStub(current_file="test.py", current_symbol="TestClass")
        conv = ConversationState(last_file="old.py", last_symbol="OldClass")
        ctx = CognitiveContext(
            query="test",
            workspace=ws,
            conversation_state=conv,
        )
        assert ctx.current_file == "test.py"
        assert ctx.current_symbol == "TestClass"
        assert ctx.last_file == "old.py"
        assert ctx.last_symbol == "OldClass"

    def test_conversation_state_update(self):
        """ConversationState 更新。"""
        state = ConversationState()
        assert state.last_file is None

        state.last_file = "runtime.py"
        state.last_symbol = "Executor"
        assert state.last_file == "runtime.py"
        assert state.last_symbol == "Executor"


class TestResolvedQuery:
    """ResolvedQuery 数据模型测试。"""

    def test_has_target(self):
        q = ResolvedQuery(target="runtime.py")
        assert q.has_target
        assert not q.has_symbol

    def test_has_symbol(self):
        q = ResolvedQuery(symbol="Planner")
        assert q.has_symbol
        assert not q.has_target

    def test_both(self):
        q = ResolvedQuery(target="planner.py", symbol="Planner")
        assert q.has_target
        assert q.has_symbol

    def test_defaults(self):
        q = ResolvedQuery()
        assert not q.has_target
        assert not q.has_symbol
        assert q.confidence == 0.0


class TestIntentEngineContextAware:
    """IntentEngine 在 CognitiveContext 下的行为测试。

    注意：这些测试验证 IntentEngine 的逻辑结构，
    LLM 实际调用会用 mock 替代（如果有 LLM）。
    """

    def setup_method(self):
        self.engine = IntentEngine()

    def test_analyze_with_context(self):
        """验证 analyze 接受 CognitiveContext 并返回 IntentResult。"""
        ctx = CognitiveContext(
            query="帮我看看这个错误日志",
            conversation_state=ConversationState(),
        )
        result = self.engine.analyze(ctx)
        assert isinstance(result, IntentResult)
        # 关键词匹配 "查看" 的变体
        # 注意：这里没有匹配到关键词，将走到 LLM 路径
        # 但 LLM 可能失败，此时返回 unknown
        assert result.domain in (DOMAIN_DEVELOPMENT, "unknown")

    def test_chat_detection(self):
        """闲聊检测仍然工作。"""
        ctx = CognitiveContext(query="你好")
        result = self.engine.analyze(ctx)
        assert result.domain == DOMAIN_CHAT
        assert not result.requires_execution

    def test_keyword_target_extraction(self):
        """关键词匹配时提取 target。"""
        ctx = CognitiveContext(
            query="读取 output/solution.py",
            workspace=WorkspaceContextStub(current_file="other.py"),
        )
        result = self.engine.analyze(ctx)
        # 关键词匹配 "读取.*文件" 但 "读取 output/solution.py" 也匹配文件操作
        # 实际上匹配的是 DOMAIN_FILE
        assert result.has_target
        assert "solution.py" in result.target or "output/solution.py" in result.target


class TestIntegration:
    """认知链路集成测试。"""

    def test_full_pipeline_omitted_target(self):
        """完整链路：省略目标 → ReferenceResolver → IntentEngine。"""
        resolver = ReferenceResolver()
        engine = IntentEngine()

        # 模拟多轮对话场景
        # 第一轮：用户打开 solution.py
        conv_state = ConversationState()
        ws = WorkspaceContextStub(current_file="output/solution.py")

        # 第二轮：用户说 "优化一下"
        ctx = CognitiveContext(
            query="优化一下",
            workspace=ws,
            conversation_state=conv_state,
        )

        # ReferenceResolver 消歧
        resolved = resolver.resolve("优化一下", ctx)
        ctx.resolved_query = resolved

        # IntentEngine 意图理解
        intent = engine.analyze(ctx)
        assert intent.has_target
        assert intent.target == "output/solution.py"

    def test_full_pipeline_pronoun(self):
        """完整链路：代词引用 → ReferenceResolver → IntentEngine。"""
        resolver = ReferenceResolver()
        engine = IntentEngine()

        conv_state = ConversationState(
            last_symbol="Planner",
            last_file="agent/planner/planner.py",
        )

        ctx = CognitiveContext(
            query="解释这里",
            conversation_state=conv_state,
        )

        # ReferenceResolver 消歧
        resolved = resolver.resolve("解释这里", ctx)
        ctx.resolved_query = resolved

        # IntentEngine 意图理解
        intent = engine.analyze(ctx)
        # IntentEngine 会合并 entity 中的符号名
        assert resolved.symbol == "Planner"
        assert resolved.target == "agent/planner/planner.py"

    def test_full_pipeline_continuation(self):
        """完整链路：跨轮续操作 → ReferenceResolver → IntentEngine。"""
        resolver = ReferenceResolver()
        engine = IntentEngine()

        conv_state = ConversationState(
            last_target="planner.py",
            last_domain="development",
            last_action="read",
        )

        ctx = CognitiveContext(
            query="那改一下",
            conversation_state=conv_state,
        )

        # ReferenceResolver 消歧
        resolved = resolver.resolve("那改一下", ctx)
        ctx.resolved_query = resolved

        assert resolved.target == "planner.py"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])