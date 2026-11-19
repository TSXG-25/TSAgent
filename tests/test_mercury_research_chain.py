"""Mercury research-task entry — search → retrieval → cited answer, offline.

The Navigator's research-task button prefills the composer with the real
`mer.research-prefill` template; this test drives a run with that exact
text and asserts the grounding chain actually engages: the intent
declaration carries source_grounding_required, the system prompt demands
cited sources, the web_search tool executes through the real provider
seam (DDG MCP stubbed), the fetched source reaches the model, and the
final answer carries the source URL.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import tests.test_ha_r1_harness as harness
from agent.service.contracts import StartRunRequest

# The exact zh template MercuryWorkspace prefills (i18n mer.research-prefill):
#   "基于以下搜索结果继续研究：\n查询：{query}\n{sources}\n\n研究目标："
PREFILL = (
    "基于以下搜索结果继续研究：\n"
    "查询：mercury 搜索链\n"
    "- Alpha Source — https://a.example/x\n"
    "\n"
    "研究目标："
)

# Canned DuckDuckGo MCP text — the canonical format parse_search_results
# consumes; unique URL so its presence in later prompts is unambiguous.
DDG_TEXT = (
    "1. Mercury Chain Doc\n   URL: https://deep.example/page\n"
    "   Summary: the fetched source body\n"
)

CITED_ANSWER = "Mercury 链已验证 [Mercury Chain Doc](https://deep.example/page)"


async def _wait_terminal(service, tenant, session, run_id, timeout=60.0):
    for _ in range(int(timeout / 0.1)):
        head = service._store.get_run_head(
            tenant, run_id, session_id=session
        )
        status = getattr(head, "run_status", "") or ""
        if status.startswith(("COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT")):
            return head
        await asyncio.sleep(0.1)
    raise AssertionError(f"run {run_id} did not reach a terminal status")


def test_research_prefill_entry_runs_grounded_search_and_cites(
    monkeypatch, tmp_path: Path
) -> None:
    """The prefill text enters a grounded run: grounding flag -> prompt
    citation rule -> real web_search tool call -> source body reaches the
    model -> final answer cites the source URL."""
    async def scenario() -> None:
        workspace = tmp_path / "ws"
        workspace.mkdir()
        service, store = harness.service_at(tmp_path)
        try:
            store.ensure_session("t", "mercury-sess", "u")
            store.upsert_workspace("t", "ws", path=str(workspace))
            store.assign_session_workspace("t", "mercury-sess", "ws")

            run = await service.start_run(StartRunRequest(
                tenant_id="t", user_id="u", session_id="mercury-sess",
                request_id="req-research", request_text=PREFILL,
                origin_planet="mercury",
            ))
            head = await _wait_terminal(
                service, "t", "mercury-sess", run.run_id
            )
            assert head.run_status == "COMPLETED", (
                head.run_status, getattr(head, "failure_code", "")
            )

            # The grounding rule actually shipped in the system prompt.
            assert any("标注来源" in call for call in provider.calls), (
                "source_grounding_required did not produce the citation rule"
            )
            # The fetched source body reached the model after the tool call.
            assert any(
                "https://deep.example/page" in call for call in provider.calls
            ), "web_search result never reached the model"
            # The final answer carries the source — citation lands in the
            # durable output text.
            output = store.read_run_snapshot(
                "t", run.run_id, session_id="mercury-sess"
            ).output
            assert "https://deep.example/page" in output.text
        finally:
            await service.close()

    import agent.mcp.duckduckgo as duckduckgo

    async def fake_mcp_call(name: str, arguments: dict[str, object]) -> str:
        assert name == "search"
        return DDG_TEXT

    monkeypatch.setattr(duckduckgo, "unavailable_reason", lambda: "")
    monkeypatch.setattr(
        "agent.mcp.duckduckgo.call_duckduckgo_tool", fake_mcp_call
    )
    monkeypatch.setenv("TSAGENT_SEARCH_PROVIDERS", "duckduckgo")

    provider = harness.setup_provider(monkeypatch, [
        # Key on the newline-free prefix — the intent prompt embeds the
        # request as a JSON field where real newlines are escaped.
        ("基于以下搜索结果继续研究", harness.intent(
            PREFILL, artifact=False, source=True,
            search_query="mercury 搜索链",
        )),
        ("基于以下搜索结果继续研究", harness.call_tool(
            "web_search", query="mercury 搜索链"
        )),
        # Keyed on the fetched URL — only answers once the tool result is
        # really inside the model's transcript.
        ("https://deep.example/page", CITED_ANSWER),
    ])
    asyncio.run(scenario())
