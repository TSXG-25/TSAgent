"""Contract tests for the stable TSAgent facade."""
import asyncio

import agent.api as api


class _FakeRuntime:
    def __init__(self, user_id):
        self.user_id = user_id

    async def run(self, user_input):
        return f"handled:{user_input}"


def test_tsagent_public_run_contract(monkeypatch):
    monkeypatch.setattr(api, "UniversalAgent", _FakeRuntime)

    from agent import TSAgent

    agent = TSAgent("public-api")
    assert agent.user_id == "public-api"
    assert asyncio.run(agent.run("hello")) == "handled:hello"


def test_tsagent_rejects_empty_input(monkeypatch):
    monkeypatch.setattr(api, "UniversalAgent", _FakeRuntime)

    from agent import TSAgent

    agent = TSAgent()
    try:
        asyncio.run(agent.run("  "))
    except ValueError as exc:
        assert "non-empty" in str(exc)
    else:
        raise AssertionError("empty input should be rejected")
