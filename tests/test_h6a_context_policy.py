"""H6a regressions for the simple-chat context fast path."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from agent.context_policy import ContextMode, ContextPolicy
from agent.cognition.cognitive_context import CognitiveContext, ConversationState
from agent.cognition.intent_engine import IntentEngine
from agent.cognition.reference_resolver import ReferenceResolver
from agent.conversation import ConversationRetriever, ConversationTracker
from agent.orchestrator.context_builder import ContextBuilder
from agent.orchestrator.planner import PlannerStage
from agent.runtime import UniversalAgent, _normalize_fact_capture_answer


def test_simple_chat_policy_disables_expensive_context_work() -> None:
    policy = ContextPolicy.for_request(
        "请用三句话简单介绍一下你自己和你能做什么。"
    )

    assert policy.mode is ContextMode.SIMPLE_CHAT
    assert policy.memory_retrieval is False
    assert policy.repository_retrieval is False
    assert policy.pre_answer_fact_extraction is False
    assert policy.post_answer_fact_extraction is False
    assert policy.semantic_skill_selection is False
    assert ContextPolicy.for_request("测试").mode is ContextMode.SIMPLE_CHAT


def test_non_chat_policy_preserves_existing_context_path() -> None:
    policy = ContextPolicy.for_request("读取 agent/runtime.py 并总结")

    assert policy.mode is ContextMode.REPOSITORY
    assert policy.memory_retrieval is False
    assert policy.repository_retrieval is True
    assert policy.pre_answer_fact_extraction is False
    assert policy.semantic_skill_selection is True


def test_context_policy_selects_only_relevant_context_sources() -> None:
    memory = ContextPolicy.for_request("我最喜欢什么编程语言")
    assert memory.mode is ContextMode.MEMORY
    assert memory.memory_retrieval is True
    assert memory.repository_retrieval is False
    assert memory.semantic_skill_selection is False

    assert ContextPolicy.for_request("我叫小明").post_answer_fact_extraction is True

    research = ContextPolicy.for_request("搜索今天有哪些财经新闻")
    assert research.mode is ContextMode.RESEARCH
    assert research.memory_retrieval is False
    assert research.repository_retrieval is False
    assert research.semantic_skill_selection is False

    repository = ContextPolicy.for_request("分析 agent/runtime.py")
    assert repository.mode is ContextMode.REPOSITORY
    assert repository.memory_retrieval is False
    assert repository.repository_retrieval is True
    assert repository.semantic_skill_selection is True


def test_explicit_execution_skips_full_context_and_skill_embedding() -> None:
    policy = ContextPolicy.for_request(
        "创建 output/h9_write.txt，内容为 H9-WRITE-OK。"
    )

    assert policy.mode is ContextMode.EXECUTION
    assert policy.memory_retrieval is False
    assert policy.repository_retrieval is False
    assert policy.pre_answer_fact_extraction is False
    assert policy.semantic_skill_selection is False

    command = ContextPolicy.for_request("执行 date 命令，并原样贴出真实命令输出。")
    assert command.mode is ContextMode.EXECUTION
    assert command.semantic_skill_selection is False

    code_file = ContextPolicy.for_request(
        "把 print('OK') 写入 output/probe.py，并实际运行它、贴出输出。"
    )
    assert code_file.mode is ContextMode.EXECUTION
    assert code_file.repository_retrieval is False
    assert code_file.semantic_skill_selection is False


def test_fact_capture_is_post_turn_only() -> None:
    policy = ContextPolicy.for_request("我叫小明")
    assert policy.mode is ContextMode.MEMORY
    assert policy.pre_answer_fact_extraction is False
    assert policy.post_answer_fact_extraction is True


def test_fact_capture_cannot_leave_a_contradictory_save_claim() -> None:
    facts = {"personal": {"name": "小刚"}}

    assert _normalize_fact_capture_answer(
        "我无法确认或保存这条信息。", facts,
    ) == "已记录你刚才提供的信息，之后可以继续询问我。"
    assert _normalize_fact_capture_answer("好的。", facts) == "好的。"
    assert _normalize_fact_capture_answer(
        "我无法确认或保存这条信息。", {},
    ) == "我无法确认或保存这条信息。"


def test_context_builder_skips_memory_and_repository_reads_for_simple_chat(
    monkeypatch,
) -> None:
    class ForbiddenMemory:
        def get_session_context(self, **_kwargs: Any) -> str:
            raise AssertionError("simple chat must not read session context")

        def get_resolutions(self, **_kwargs: Any) -> list[Any]:
            raise AssertionError("simple chat must not read resolutions")

    class ForbiddenIndexer:
        file_symbols = {"unexpected.py": ["unexpected"]}

    monkeypatch.setattr(
        "agent.repository.indexer.get_repository_indexer",
        lambda: ForbiddenIndexer(),
    )
    orchestrator = SimpleNamespace(
        session_context=SimpleNamespace(memory_view=ForbiddenMemory()),
        run_context=SimpleNamespace(workspace=None),
        _conversation_state=SimpleNamespace(),
    )
    context = ContextBuilder(orchestrator).build(
        user_input="你好",
        user_id="user-a",
        context={"session": "", "short_term": "", "facts": ""},
        repo_context="",
        state={"plan": [], "current_task_index": 0, "artifacts": {}},
        context_policy=ContextPolicy.for_request("你好"),
    )

    assert context.conversation == []
    assert context.memory == {"facts": ""}
    assert context.repository_symbols == {}


def test_context_builder_repository_request_uses_repo_without_semantic_memory(
    monkeypatch,
) -> None:
    class ForbiddenMemory:
        def get_session_context(self, **_kwargs: Any) -> str:
            raise AssertionError("repository task must not retrieve semantic memory")

        def get_resolutions(self, **_kwargs: Any) -> list[Any]:
            raise AssertionError("repository task must not retrieve resolutions")

    class Indexer:
        file_symbols = {"agent/runtime.py": ["UniversalAgent"]}

    monkeypatch.setattr(
        "agent.repository.indexer.get_repository_indexer",
        lambda: Indexer(),
    )
    orchestrator = SimpleNamespace(
        session_context=SimpleNamespace(memory_view=ForbiddenMemory()),
        run_context=SimpleNamespace(workspace=None),
        _conversation_state=SimpleNamespace(),
    )
    context = ContextBuilder(orchestrator).build(
        user_input="分析 agent/runtime.py",
        user_id="user-a",
        context={"session": "", "short_term": "", "facts": ""},
        repo_context="[agent/runtime.py]\nclass UniversalAgent:",
        state={"plan": [], "current_task_index": 0, "artifacts": {}},
        context_policy=ContextPolicy.for_request("分析 agent/runtime.py"),
    )

    assert context.conversation == []
    assert context.repository_symbols == {"agent/runtime.py": ["UniversalAgent"]}


def test_planner_does_not_select_semantic_skill_for_simple_chat(monkeypatch) -> None:
    class ContextBuilderMarker:
        def render_context(self, _context: dict, _now: Any) -> str:
            return ""

        def build(self, **_kwargs: Any) -> Any:
            raise RuntimeError("context-build-marker")

    def forbidden_skill_select(_value: str) -> Any:
        raise AssertionError("simple chat must not select a semantic skill")

    monkeypatch.setattr(
        "agent.orchestrator.planner.skill_registry.select",
        forbidden_skill_select,
    )
    stage = PlannerStage(
        SimpleNamespace(
            _context_builder=ContextBuilderMarker(),
            session_context=SimpleNamespace(memory_view=None),
            _timings={},
        )
    )

    try:
        asyncio.run(
            stage.run(
                "你好",
                "user-a",
                {},
                "",
                "",
                context_policy=ContextPolicy.for_request("你好"),
            )
        )
    except RuntimeError as exc:
        assert str(exc) == "context-build-marker"
    else:
        raise AssertionError("the context-build marker should be reached")


def test_runtime_intent_fallback_uses_async_provider() -> None:
    class Provider:
        def invoke(self, _messages: Any) -> Any:
            raise AssertionError("production intent routing must not block on invoke()")

        async def ainvoke(self, _messages: Any) -> Any:
            return SimpleNamespace(
                content=(
                    '{"domain":"闲聊","action":"answer","target":"",'
                    '"entities":[],"confidence":0.9,"summary":"answer"}'
                )
            )

    engine = IntentEngine()
    engine._llm = Provider()

    intent = asyncio.run(
        engine.analyze_async(
            CognitiveContext(query="请处理这个没有关键词的请求")
        )
    )

    assert intent.domain == "chat"
    assert intent.requires_execution is False


def test_simple_chat_policy_bypasses_intent_provider_and_calls_answer_once(
    monkeypatch,
) -> None:
    class Memory:
        def __init__(self) -> None:
            self.exchanges: list[tuple[str, str]] = []

        def record_full_exchange(self, user_input: str, answer: str) -> None:
            self.exchanges.append((user_input, answer))

        def record_resolution(self, *_args: Any, **_kwargs: Any) -> None:
            return None

        def get_user_facts(self) -> str:
            return ""

    async def forbidden_intent(_context: Any) -> Any:
        raise AssertionError("simple chat must not call the intent Provider")

    answer_calls = 0

    async def answer_once(_messages: Any, **_kwargs: Any) -> Any:
        nonlocal answer_calls
        answer_calls += 1
        return SimpleNamespace(content="我是 TSAgent，可以回答问题并执行受控任务。")

    memory = Memory()
    tracker = ConversationTracker()
    orchestrator = SimpleNamespace(
        session_context=SimpleNamespace(
            memory_view=memory,
            conversation_retriever=ConversationRetriever(tracker),
        ),
        run_context=None,
        _conversation_state=ConversationState(),
        _reference_resolver=ReferenceResolver(),
        _timings={},
    )
    orchestrator._context_builder = ContextBuilder(orchestrator)

    monkeypatch.setattr(
        "agent.orchestrator.planner.intent_engine.analyze_async",
        forbidden_intent,
    )
    monkeypatch.setattr("agent.llm.llm.ainvoke", answer_once)

    prompt = "用三句话介绍你自己以及你能做什么。"
    state, next_state, answer = asyncio.run(
        PlannerStage(orchestrator).run(
            prompt,
            "user-a",
            {},
            "",
            "",
            context_policy=ContextPolicy.for_request(prompt),
        )
    )

    assert next_state == "FINISH"
    assert state["context_policy"] == ContextMode.SIMPLE_CHAT.value
    assert answer == "我是 TSAgent，可以回答问题并执行受控任务。"
    assert answer_calls == 1
    assert orchestrator._timings["intent_route"] < 0.01
    assert "answer_llm" in orchestrator._timings


def test_runtime_simple_chat_skips_fact_memory_repo_and_skill(monkeypatch) -> None:
    class Memory:
        def __init__(self) -> None:
            self.fact_calls = 0
            self.context_calls = 0

        async def extract_and_save_facts(self, _text: str) -> dict[str, Any]:
            self.fact_calls += 1
            raise AssertionError("simple chat must not extract facts")

        def get_context(self, _query: str) -> dict[str, str]:
            self.context_calls += 1
            raise AssertionError("simple chat must not retrieve semantic memory")

        def record_user_message(self, _text: str) -> None:
            return None

    class Orchestrator:
        replan_count = 0

        def __init__(self) -> None:
            self.policy: ContextPolicy | None = None

        def reset_timings(self) -> None:
            return None

        def get_timings(self) -> dict[str, float]:
            return {}

        async def plan(self, **kwargs: Any) -> tuple[dict[str, Any], str, str]:
            self.policy = kwargs["context_policy"]
            return {
                "plan": [],
                "requested_outcomes": ["USER_VISIBLE_OUTPUT"],
                "answer_required": True,
            }, "FINISH", "chat answer"

        async def finalize(self, **kwargs: Any) -> str:
            return str(kwargs.get("best_answer") or "chat answer")

    memory = Memory()
    orchestrator = Orchestrator()
    agent = UniversalAgent.__new__(UniversalAgent)
    agent._memory_view = memory
    agent._memory_namespace = "user-a"
    agent._pending_execution_target = ""
    agent._run_context = None
    agent._timings = {}
    agent._wall_total_seconds = 0.0
    agent._session_context = SimpleNamespace(
        conversation_tracker=SimpleNamespace(update=lambda **_kwargs: None),
    )
    agent.orchestrator = orchestrator
    agent._print_timing_summary = lambda: {}

    monkeypatch.setattr(
        "agent.runtime._build_repo_context",
        lambda _value: (_ for _ in ()).throw(
            AssertionError("simple chat must not search repository")
        ),
    )
    monkeypatch.setattr(
        "agent.runtime.skill_registry.select",
        lambda _value: (_ for _ in ()).throw(
            AssertionError("simple chat must not select semantic skill")
        ),
    )

    answer = asyncio.run(agent._run_in_context("你好"))

    assert answer == "chat answer"
    assert memory.fact_calls == 0
    assert memory.context_calls == 0
    assert orchestrator.policy is not None
    assert orchestrator.policy.mode is ContextMode.SIMPLE_CHAT


def test_runtime_fact_capture_starts_after_user_answer(monkeypatch) -> None:
    events: list[str] = []

    class Memory:
        async def extract_and_save_facts(self, _text: str) -> dict[str, Any]:
            events.append("facts")
            return {"personal": {"name": "小明"}}

        def record_user_message(self, _text: str) -> None:
            events.append("user")

    class Orchestrator:
        replan_count = 0

        def reset_timings(self) -> None:
            return None

        def get_timings(self) -> dict[str, float]:
            return {}

        async def plan(self, **_kwargs: Any) -> tuple[dict[str, Any], str, str]:
            return {
                "plan": [],
                "requested_outcomes": ["USER_VISIBLE_OUTPUT"],
                "answer_required": True,
            }, "FINISH", "answer"

        async def finalize(self, **_kwargs: Any) -> str:
            events.append("answer")
            return "answer"

    agent = UniversalAgent.__new__(UniversalAgent)
    agent._memory_view = Memory()
    agent._memory_namespace = "user-a"
    agent._pending_execution_target = ""
    agent._run_context = None
    agent._timings = {}
    agent._wall_total_seconds = 0.0
    agent._session_context = SimpleNamespace(
        conversation_tracker=SimpleNamespace(update=lambda **_kwargs: None),
    )
    agent.orchestrator = Orchestrator()
    agent._print_timing_summary = lambda: {}

    monkeypatch.setattr(
        "agent.runtime._build_repo_context",
        lambda _value: (_ for _ in ()).throw(
            AssertionError("fact capture must not search repository")
        ),
    )
    monkeypatch.setattr(
        "agent.runtime.skill_registry.select",
        lambda _value: (_ for _ in ()).throw(
            AssertionError("fact capture must not select semantic skill")
        ),
    )

    assert asyncio.run(agent._run_in_context("我叫小明")) == "answer"
    assert events.index("answer") < events.index("facts")
