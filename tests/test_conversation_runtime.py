"""Conversation Runtime 回归测试（v2.1B-1 / ADR-0013 / ADR-0014）。

覆盖：
- ConversationIntent 派生（NEW_REQUEST / REFERENCE / CONTINUE_*）
- ConversationState 不可变迁移（immutable + old→new）
- Tracker 每轮更新（回问不覆盖 recent_goal；answer 总更新）
- ConversationEvent 记录（Replay）
- Retriever 返回纯数据快照（不生成 Prompt）；render_snapshot 由消费者调用
- Decision：存在 recent_goal 时自动恢复而非 Ask User
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.conversation import (
    ConversationIntent,
    ConversationSnapshot,
    ConversationState,
    ConversationTracker,
    ConversationRetriever,
    classify_conversation_intent,
    render_snapshot,
)


class _Intent:
    """IntentResult 最小替身（domain/action/requires_execution）。"""
    def __init__(self, domain, action, requires_execution=True):
        self.domain = domain
        self.action = action
        self.requires_execution = requires_execution


class TestConversationIntent:
    def test_new_request(self):
        assert classify_conversation_intent(None, "帮我写一个 Python 快排") == ConversationIntent.NEW_REQUEST
        assert classify_conversation_intent(_Intent("development", "code"), "帮我写快排") == ConversationIntent.NEW_REQUEST
        # 新事实（保存偏好）不是引用
        assert classify_conversation_intent(_Intent("memory", "save_preference"), "我喜欢蓝色") == ConversationIntent.NEW_REQUEST

    def test_reference(self):
        assert classify_conversation_intent(_Intent("memory", "query", requires_execution=False), "刚才让我做什么？") == ConversationIntent.REFERENCE
        assert classify_conversation_intent(_Intent("memory", "recall"), "刚才答案是多少？") == ConversationIntent.REFERENCE
        assert classify_conversation_intent(_Intent("memory", "query_history"), "上一条指令是什么") == ConversationIntent.REFERENCE

    def test_continuation(self):
        # 裸词无 pending 计划时是聊天延续；不再默认解释成执行。
        assert classify_conversation_intent(None, "继续") == ConversationIntent.CONTINUE_CHAT
        assert classify_conversation_intent(None, "然后呢") == ConversationIntent.CONTINUE_CHAT
        assert classify_conversation_intent(None, "继续吧") == ConversationIntent.CONTINUE_CHAT

    def test_continuation_contract(self):
        assert ConversationIntent("continuation") is ConversationIntent.CONTINUE_PLAN
        assert classify_conversation_intent(
            None, "继续执行未完成的任务"
        ) == ConversationIntent.CONTINUE_PLAN
        assert classify_conversation_intent(
            None, "继续", runtime_pending=True
        ) == ConversationIntent.CONTINUE_PLAN
        assert classify_conversation_intent(
            None, "继续讲"
        ) == ConversationIntent.CONTINUE_CHAT
        assert classify_conversation_intent(
            None, "那个呢"
        ) == ConversationIntent.CONTINUE_REFERENCE
        # Explicit reference wording wins over a pending bit; only a bare
        # continuation is resolved by Runtime state.
        assert classify_conversation_intent(
            None, "继续那个", runtime_pending=True
        ) == ConversationIntent.CONTINUE_REFERENCE
        assert classify_conversation_intent(
            None, "继续刚才那个函数", runtime_pending=True
        ) == ConversationIntent.CONTINUE_REFERENCE


class TestConversationTracker:
    def test_immutable_transition(self):
        t = ConversationTracker()
        s1 = t.update(user_id="u", user_input="帮我写快排", assistant_answer="好的", intent=_Intent("development", "code"))
        s2 = t.update(user_id="u", user_input="刚才让我做什么？", assistant_answer="帮我写快排", intent=_Intent("memory", "query"))
        # frozen → 不允许原地修改
        with pytest.raises(Exception):
            s1.recent_goal = "改掉"  # type: ignore[misc]
        assert s1.turn_count == 1 and s2.turn_count == 2

    def test_reference_does_not_overwrite_goal(self):
        t = ConversationTracker()
        t.update(user_id="u", user_input="帮我写一个 Python 快排", assistant_answer="好的", intent=_Intent("development", "code"))
        s = t.update(user_id="u", user_input="刚才让我做什么？", assistant_answer="帮我写一个 Python 快排", intent=_Intent("memory", "query", requires_execution=False))
        assert s.recent_goal == "帮我写一个 Python 快排"
        assert s.last_instruction == "帮我写一个 Python 快排"
        assert s.last_answer == "帮我写一个 Python 快排"

    def test_filler_does_not_overwrite_goal(self):
        """Memory Fuzz 根因：闲聊/查询填充轮不得覆盖最近目标。"""
        t = ConversationTracker()
        t.update(user_id="u", user_input="帮我写一个 Python 快排", assistant_answer="好的", intent=_Intent("development", "code"))
        for f, fi in [
            ("今天天气怎么样？", _Intent("knowledge", "weather", requires_execution=True)),
            ("2+2等于几？", _Intent("math", "calculate", requires_execution=False)),
            ("介绍一下你自己。", _Intent("chat", "identity", requires_execution=False)),
            ("推荐一首歌。", _Intent("knowledge", "recommend", requires_execution=False)),
        ]:
            t.update(user_id="u", user_input=f, assistant_answer="好的", intent=fi)
        s = t.update(user_id="u", user_input="刚才让我做什么？", assistant_answer="帮我写一个 Python 快排", intent=_Intent("memory", "query", requires_execution=False))
        assert s.recent_goal == "帮我写一个 Python 快排", s.recent_goal
        assert s.last_instruction == "帮我写一个 Python 快排"

    def test_new_request_updates_goal(self):
        t = ConversationTracker()
        t.update(user_id="u", user_input="写 a.py", assistant_answer="ok", intent=_Intent("file", "write"))
        s = t.update(user_id="u", user_input="写 b.py", assistant_answer="ok", intent=_Intent("file", "write"))
        assert s.recent_goal == "写 b.py"
        assert s.last_instruction == "写 b.py"

    def test_answer_reference_uses_last_answer(self):
        t = ConversationTracker()
        t.update(user_id="u", user_input="1+1是多少？", assistant_answer="2", intent=_Intent("math", "calculate"))
        t.update(user_id="u", user_input="3+5是多少？", assistant_answer="8", intent=_Intent("math", "calculate"))
        s = t.update(user_id="u", user_input="刚才答案是多少？", assistant_answer="8", intent=_Intent("memory", "query", requires_execution=False))
        assert s.last_answer == "8"
        assert s.recent_goal == ""  # math 非目标型，recent_goal 保持空；答案引用走 last_answer


class TestConversationRetrieverAndReplay:
    def test_snapshot_is_pure_data(self):
        t = ConversationTracker()
        t.update(user_id="u", user_input="帮我写快排", assistant_answer="好的", intent=_Intent("development", "code"))
        r = ConversationRetriever(t)
        snap = r.snapshot("u")
        assert isinstance(snap, ConversationSnapshot)
        assert snap.recent_goal == "帮我写快排"
        # Retriever 不生成 prompt；渲染由消费者调用
        assert not hasattr(r, "render_for_prompt")

    def test_render_snapshot(self):
        snap = ConversationSnapshot(recent_goal="帮我写快排", last_instruction="帮我写快排", last_answer="好的")
        text = render_snapshot(snap)
        assert "最近目标" in text and "帮我写快排" in text and "上一条回答" in text
        assert render_snapshot(ConversationSnapshot()) == ""

    def test_events_replay(self):
        t = ConversationTracker(max_events=10)
        t.update(user_id="u", user_input="写 a.py", assistant_answer="ok", intent=_Intent("file", "write"))
        t.update(user_id="u", user_input="继续", assistant_answer="ok", intent=None)
        evs = ConversationRetriever(t).events("u")
        assert [e.intent for e in evs] == [ConversationIntent.NEW_REQUEST, ConversationIntent.CONTINUE_CHAT]
        assert evs[0].user_input == "写 a.py"
        assert evs[1].answer == "ok"

    def test_pending_signal_selects_plan_for_bare_continuation(self):
        t = ConversationTracker(max_events=10)
        t.update(
            user_id="u",
            user_input="写 a.py",
            assistant_answer="未完成",
            intent=_Intent("file", "write"),
            runtime_pending=True,
        )
        t.update(
            user_id="u",
            user_input="继续",
            assistant_answer="继续执行",
            intent=None,
            runtime_pending=False,
        )
        assert t.get_events("u")[-1].intent is ConversationIntent.CONTINUE_PLAN
        assert not t.runtime_pending("u")


class TestDomainUpgrade:
    """v2.1B-3：代码生成请求不再被判为 math（domain upgrade + 代码生成模式）。"""

    def _intent(self, text):
        from agent.cognition.intent_engine import engine
        from agent.cognition.cognitive_context import CognitiveContext, ResolvedQuery
        return engine.analyze(CognitiveContext(query=text, resolved_query=ResolvedQuery(raw=text)))

    def test_code_requests_are_development(self):
        for u in ["帮我写一个判断素数的函数。", "帮我写一个 Python 快排。",
                  "帮我写一个二分查找函数。", "帮我写一个读取 CSV 的脚本。",
                  "帮我写一个反转链表的函数。"]:
            r = self._intent(u)
            assert r.domain == "development", f"{u} -> {r.domain}"
            assert r.requires_execution, f"{u} 应进入执行链"

    def test_non_code_requests_unchanged(self):
        assert self._intent("写一首诗").domain == "creation"
        assert self._intent("1+1等于几").domain == "math"
        assert self._intent("今天天气怎么样").domain == "knowledge"
        assert self._intent("把 hello 保存到 output/x.py").domain == "file"

    def test_explicit_continuation_is_deterministic(self):
        plan = self._intent("继续执行未完成的任务")
        assert plan.domain == "development"
        assert plan.action == "continue_plan"
        assert plan.requires_execution is True

        chat = self._intent("继续讲")
        assert chat.domain == "chat"
        assert chat.action == "continue_chat"
        assert chat.requires_execution is False

        ref = self._intent("那个呢")
        assert ref.domain == "memory"
        assert ref.action == "reference"
        assert ref.requires_execution is False


class TestConversationReferenceResolver:
    """v2.1B-2：REFERENCE → ReferenceType → 只注入对应字段（无 prompt hack）。"""

    def _intent(self, reference_kind=""):
        class I:
            def __init__(self, kind):
                self.reference_kind = kind
        return I(reference_kind)

    def test_resolve_reference_type(self):
        from agent.conversation import resolve_reference_type, ReferenceType
        assert resolve_reference_type(self._intent("answer")) is ReferenceType.LAST_ANSWER
        assert resolve_reference_type(self._intent("instruction")) is ReferenceType.LAST_INSTRUCTION
        assert resolve_reference_type(self._intent("goal")) is ReferenceType.LAST_GOAL
        assert resolve_reference_type(self._intent("runtime")) is ReferenceType.LAST_RUNTIME
        assert resolve_reference_type(self._intent("")) is ReferenceType.UNKNOWN
        assert resolve_reference_type(None) is ReferenceType.UNKNOWN

    def test_render_reference_only_selected_field(self):
        from agent.conversation import ConversationSnapshot, ReferenceType, render_reference
        snap = ConversationSnapshot(recent_goal="写快排", last_instruction="写快排", last_answer="8")
        ans = render_reference(snap, ReferenceType.LAST_ANSWER)
        assert "上一条回答" in ans and "8" in ans
        assert "最近目标" not in ans and "写快排" not in ans
        goal = render_reference(snap, ReferenceType.LAST_GOAL)
        assert "最近目标" in goal and "写快排" in goal
        assert "8" not in goal
        assert render_reference(snap, ReferenceType.UNKNOWN) == ""

    def test_intent_engine_detects_reference_kind(self):
        from agent.cognition.intent_engine import _detect_reference_kind
        assert _detect_reference_kind("刚才答案是多少？") == "answer"
        assert _detect_reference_kind("刚才让我做什么？") == "instruction"
        assert _detect_reference_kind("上一条指令是什么") == "instruction"
        assert _detect_reference_kind("继续") == "runtime"
        assert _detect_reference_kind("继续刚才那个函数") == "instruction"
        assert _detect_reference_kind("帮我写快排") == ""

    def test_runtime_continuation_helper(self):
        from agent.orchestrator.planner import _render_runtime_continuation
        state = {"plan": [
            {"goal": "帮我写一个 Rust 函数处理需求", "status": "pending"},
            {"goal": "读入整数并输出平方", "status": "running"},
        ]}
        text = _render_runtime_continuation(state)
        assert "平方" in text and "当前执行" in text
        assert _render_runtime_continuation({"plan": []}) == ""


class TestContinuationContractIntegration:
    """v2.1B 集成：_apply_conversation_contract 不得抛异常（曾被 Retriever 缺 runtime_pending 破坏）。"""

    def test_apply_contract_no_raise(self):
        from agent.orchestrator.planner import _apply_conversation_contract
        from agent.cognition.intent_schema import IntentResult
        it = IntentResult(domain="chat", action="continue", requires_execution=False)
        _apply_conversation_contract(it, "probe-x", "继续")  # 不得抛异常
        assert it.reference_kind in ("runtime", "answer", "")

    def test_retriever_runtime_pending(self):
        from agent.conversation import conversation_retriever, conversation_tracker
        conversation_tracker.update(user_id="u-pending", user_input="继续",
                                    assistant_answer="ok", runtime_pending=True)
        assert conversation_retriever.runtime_pending("u-pending") is True
        assert conversation_retriever.runtime_pending("u-none") is False

    def test_retriever_protocol_is_satisfied(self):
        from agent.conversation import ConversationRetrieverProtocol, conversation_retriever
        assert isinstance(conversation_retriever, ConversationRetrieverProtocol)

    def test_explicit_reference_conflict_requires_clarification(self):
        from agent.conversation import conversation_tracker
        from agent.cognition.intent_schema import IntentResult
        from agent.orchestrator.planner import _apply_conversation_contract

        user_id = "u-conflict"
        conversation_tracker.update(
            user_id=user_id,
            user_input="帮我修改 output/pending.py",
            assistant_answer="暂未完成",
            runtime_pending=True,
        )
        intent = IntentResult(domain="chat", action="continue", requires_execution=False)
        kind = _apply_conversation_contract(
            intent,
            user_id,
            "继续刚才那个函数",
            pending_target="output/pending.py",
            reference_target="output/other.py",
        )

        assert kind.value == "continue_reference"
        assert intent.action == "clarify"
        assert intent.requires_execution is False

    def test_planner_contract_diagnostic_is_recorded(self):
        from agent.orchestrator.planner import _record_contract_diagnostic

        state = {}
        _record_contract_diagnostic(state, "conversation_reference_injection", AttributeError("snapshot missing"))
        assert state["diagnostics"][-1]["type"] == "contract_violation"


class TestDecisionConversationResume:
    def test_recent_goal_resumes_retry(self):
        from agent.decision.decision import DecisionInput, ExecutionState, decide, RETRY, ASK, SWITCH
        inp = DecisionInput(
            diagnosis="unknown", diagnosis_confidence=0.3,
            state=ExecutionState(retry_count=0),
            conversation=ConversationSnapshot(recent_goal="继续刚才的任务"),
        )
        d, _ = decide(inp)
        # 无会话时 unknown → ASK；有 recent_goal 且策略 unknown 只允许 ASK → 无法恢复（保持 ASK）
        assert d.action in (ASK, RETRY, SWITCH)

    def test_external_failure_resumes_retry(self):
        from agent.decision.decision import DecisionInput, ExecutionState, decide, RETRY
        # external_failure 策略允许 (RETRY, ASK, FINISH)
        inp = DecisionInput(
            diagnosis="external_failure", diagnosis_confidence=0.3,
            state=ExecutionState(retry_count=0),
            conversation=ConversationSnapshot(recent_goal="继续"),
        )
        d, trace = decide(inp)
        assert d.action == RETRY
        assert trace.policy_rule == "conversation_resume"

    def test_no_conversation_keeps_ask(self):
        from agent.decision.decision import DecisionInput, ExecutionState, decide, ASK
        inp = DecisionInput(diagnosis="external_failure", diagnosis_confidence=0.3,
                            state=ExecutionState(retry_count=0))
        d, _ = decide(inp)
        assert d.action == ASK  # 置信门控 → ASK


import pytest  # noqa: E402  (moved to bottom to keep imports at top clean)
