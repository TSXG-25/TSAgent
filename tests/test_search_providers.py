"""Search provider seam — order, failover, normalization, web_search wiring."""
import pytest
from agent.search import providers
from agent.search.providers import SearchOutcome


class _Provider:
    def __init__(self, pid, avail=True, outcome=None, error=None):
        self.id, self._avail, self._outcome, self._error = pid, avail, outcome, error

    def available(self):
        return self._avail

    async def search(self, query, *, max_results):
        if self._error:
            raise self._error
        return self._outcome


@pytest.mark.asyncio
async def test_failover_skips_unavailable_and_failing(monkeypatch):
    monkeypatch.setenv(providers.SEARCH_PROVIDERS_ENV, "a,b,c")
    ok = SearchOutcome(provider="c", sources=[{"url": "u"}], content="x")
    monkeypatch.setattr(providers, "_registry", lambda: {
        "a": _Provider("a", avail=False),
        "b": _Provider("b", error=RuntimeError("boom")),
        "c": _Provider("c", outcome=ok),
    })
    assert await providers.run_search("q") is ok


@pytest.mark.asyncio
async def test_all_fail_raises_unavailable(monkeypatch):
    monkeypatch.setenv(providers.SEARCH_PROVIDERS_ENV, "a")
    monkeypatch.setattr(providers, "_registry",
                        lambda: {"a": _Provider("a", avail=False)})
    with pytest.raises(RuntimeError, match="RESEARCH_TOOL_UNAVAILABLE"):
        await providers.run_search("q")


@pytest.mark.asyncio
async def test_default_order(monkeypatch):
    monkeypatch.delenv(providers.SEARCH_PROVIDERS_ENV, raising=False)
    assert providers.provider_order() == ("duckduckgo", "deepseek")


@pytest.mark.asyncio
async def test_web_search_routes_through_seam(monkeypatch):
    import tools.web as web
    outcome = SearchOutcome(
        provider="deepseek",
        sources=[{"title": "T", "url": "https://x", "snippet": "s",
                  "publishedAt": "2026"}],
        content="",
    )
    async def fake_run(query, *, max_results):
        return outcome
    monkeypatch.setattr("agent.search.providers.run_search", fake_run)
    results = await web.web_search(query="q")
    assert results[0].url == "https://x" and results[0].source == "deepseek"


@pytest.mark.asyncio
async def test_deepseek_provider_normalizes_response(monkeypatch):
    from agent.search import deepseek_provider as dp

    class _Resp:
        def raise_for_status(self): pass
        def json(self):
            return {"content": [
                {"type": "text", "text": "answer"},
                {"type": "web_search_tool_result", "content": [
                    {"type": "web_search_result", "title": "t",
                     "url": "https://u", "page_age": "1d"},
                ]},
            ]}
    class _Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, url, headers=None, json=None):
            assert url == dp.ANTHROPIC_MESSAGES_URL
            assert json["tools"][0]["type"] == "web_search_20250305"
            return _Resp()
    monkeypatch.setattr(dp.httpx, "AsyncClient", lambda **k: _Client())
    out = await dp.DeepSeekSearchProvider().search("q", max_results=5)
    assert out.content == "answer" and out.sources[0]["url"] == "https://u"
