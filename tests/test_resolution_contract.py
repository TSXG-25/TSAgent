"""test_resolution_contract — v1.2B Resolver Contract 单测。

ResolutionContract（v1.2B 冻结目标）：
- ResolutionResult（极简，不携带 Intent 的 domain/action）
- ResolutionTimeline（Storage：latest/history/iter_reverse；无 kind 语义）
- ConversationState（State = Cache，record() 唯一写入入口）
"""
import pytest

from agent.cognition.cognitive_context import (
    ConversationState,
    ResolutionResult,
    ResolutionTimeline,
    ResolvedQuery,
)


def _result(kind="topic", target="上海", symbol="", confidence=0.8, trace="主题延续"):
    return ResolutionResult(
        kind=kind,
        target=target,
        symbol=symbol,
        confidence=confidence,
        trace=trace,
        raw="上海呢",
    )


class TestResolutionResult:
    def test_extreme_simple(self):
        """ResolutionResult 极简：不携带 Intent 的 domain/action。"""
        r = _result()
        assert not hasattr(r, "domain")
        assert not hasattr(r, "action")
        assert r.kind == "topic"
        assert r.target == "上海"

    def test_to_json_determinism_input(self):
        """to_json 是 Determinism Hash 的稳定输入（结果 + 推理路径）。"""
        r = _result(trace="符号引用: last_symbol=max_active")
        j = r.to_json()
        assert j["kind"] == "topic"
        assert j["target"] == "上海"
        assert j["symbol"] == ""
        assert j["trace"] == "符号引用: last_symbol=max_active"
        # 确定性：两次序列化一致
        assert r.to_json() == j

    def test_to_resolved_query(self):
        """兼容视图：ResolvedQuery（intent_engine 消费）。"""
        r = _result()
        rq = r.to_resolved_query()
        assert isinstance(rq, ResolvedQuery)
        assert rq.target == "上海"
        assert rq.kind == "topic"

    def test_entities_delegate(self):
        """entities 只读委托（直接消费 ResolutionResult 时兼容）。"""
        r = _result()
        assert r.entities == []
        r.resolved_query = ResolvedQuery(target="上海", entities=["weather"])
        assert r.entities == ["weather"]


class TestResolutionTimeline:
    def test_storage_only_no_kind_semantics(self):
        """Timeline = Storage：无 kind 语义方法（find_symbol 等在 Resolver）。"""
        t = ResolutionTimeline()
        assert not hasattr(t, "find_symbol")
        assert not hasattr(t, "nth")

    def test_push_latest_history(self):
        t = ResolutionTimeline()
        t.push(_result(target="杭州"))
        t.push(_result(target="上海"))
        assert t.latest().target == "上海"
        assert [r.target for r in t.history()] == ["杭州", "上海"]
        assert len(t) == 2

    def test_iter_reverse_latest_first(self):
        t = ResolutionTimeline()
        t.push(_result(target="杭州"))
        t.push(_result(target="上海"))
        assert [r.target for r in t.iter_reverse()] == ["上海", "杭州"]

    def test_window_cap(self):
        t = ResolutionTimeline(maxlen=3)
        for i in range(5):
            t.push(_result(target=str(i)))
        assert len(t) == 3
        assert [r.target for r in t.history()] == ["2", "3", "4"]

    def test_empty(self):
        t = ResolutionTimeline()
        assert t.latest() is None
        assert t.history() == []
        t.clear()
        assert len(t) == 0

    def test_record_via_conversation_state(self):
        """State = Cache：ConversationState.record 是唯一写入入口。"""
        state = ConversationState()
        state.record(_result(target="广州"))
        assert state.timeline.latest().target == "广州"


class TestConversationStateThin:
    def test_timeline_is_only_semantic_field(self):
        """State 极薄：timeline 是唯一语义字段（last_* 为 Deprecated 迁移期）。"""
        state = ConversationState()
        assert state.timeline is not None
        assert len(state.timeline) == 0
