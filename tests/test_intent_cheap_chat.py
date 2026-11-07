"""Cheap-chat intent shortcut: greetings bypass the intent LLM call."""
import pytest
from agent.cognition.intent_engine import IntentEngine, _cheap_chat_intent


class _Ctx:
    def __init__(self, query):
        self.query = query
        self.current_file = ""
        self.resolved_references = ()
        self.run_results = ()


@pytest.mark.parametrize("text", ["你好", "hi", "谢谢", "在吗", "who are you", "拜拜"])
def test_cheap_chat_matches_greetings(text):
    r = _cheap_chat_intent(_Ctx(text))
    assert r is not None and r.domain == "chat" and not r.requires_execution


@pytest.mark.parametrize("text", [
    "帮我写个脚本", "你好帮我改下文件", "分析 output/x.py",
    "你好" * 20, "hi, can you refactor this",
])
def test_cheap_chat_rejects_real_requests(text):
    assert _cheap_chat_intent(_Ctx(text)) is None


@pytest.mark.asyncio
async def test_analyze_async_short_circuits_llm(monkeypatch):
    engine = IntentEngine()

    async def _boom(*a, **k):
        raise AssertionError("LLM must not be called for greetings")

    monkeypatch.setattr(engine, "_llm_analyze_async", _boom)
    ctx = _Ctx("你好")
    intent = await engine.analyze_async(ctx)
    assert intent.domain == "chat" and not intent.requires_execution


def test_intent_llm_defaults_to_router(monkeypatch):
    from agent.cognition import intent_engine as ie
    monkeypatch.delenv(ie.INTENT_MODEL_ENV, raising=False)
    assert ie._build_intent_llm() is ie.default_llm


def test_intent_llm_env_builds_dedicated_with_fallback(monkeypatch):
    """TSAGENT_INTENT_MODEL 设置时构建专用 provider，失败回落 router。"""
    from agent.cognition import intent_engine as ie

    calls = []

    class _FakeChat:
        def __init__(self, **kw):
            calls.append(("init", kw.get("model")))
        def invoke(self, msgs, **kw):
            raise RuntimeError("dedicated down")
        async def ainvoke(self, msgs, **kw):
            raise RuntimeError("dedicated down")

    class _Router:
        def invoke(self, msgs, **kw): return "router-answer"
        async def ainvoke(self, msgs, **kw): return "router-answer"

    monkeypatch.setenv(ie.INTENT_MODEL_ENV, "cheap-model")
    monkeypatch.setattr(
        "langchain_openai.ChatOpenAI", _FakeChat, raising=False
    ) if False else None
    # patch the import inside _build_intent_llm via module attribute
    import langchain_openai
    monkeypatch.setattr(langchain_openai, "ChatOpenAI", _FakeChat)
    monkeypatch.setattr(ie, "default_llm", _Router())

    llm = ie._build_intent_llm()
    assert calls == [("init", "cheap-model")]
    assert llm.invoke([]) == "router-answer"


def test_set_and_reset_llm():
    engine = IntentEngine()
    sentinel = object()
    engine.set_llm(sentinel)
    assert engine._llm is sentinel
    engine.reset_llm()
    assert engine._llm is not sentinel
