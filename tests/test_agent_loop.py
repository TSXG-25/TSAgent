"""Focused contracts for the native tool-calling AgentLoop."""
import asyncio
from types import SimpleNamespace

import pytest

from agent.cognition.effect_authorization import EffectAuthorization
from agent.cognition.execution_need import RequestedOutcome
from agent.goal import GoalState
from agent.llm import ChatTurn, ToolCall
from agent.loop.runner import AgentLoop
from agent.loop.toolset import build_toolset


def _turn(*tool_calls, content=""):
    return ChatTurn(
        content=content,
        tool_calls=tuple(
            ToolCall(id=f"call-{index}", name=name, arguments=dict(arguments))
            for index, (name, arguments) in enumerate(tool_calls)
        ),
    )


def _scripted(turns):
    """Stub achat returning queued ChatTurns."""
    queue = list(turns)
    calls = []

    async def achat(messages, tools=None, **_kwargs):
        calls.append(list(messages))
        assert queue, "scripted achat exhausted"
        return queue.pop(0)

    return achat, calls


def _orchestrator(tmp_path):
    from agent.event_bus import EventBus
    from agent.executor.contract import executor_factory
    from agent.orchestrator.executor import ExecutionStage
    from agent.services.workspace_service import WorkspaceService

    workspace = WorkspaceService.scoped(
        tmp_path, event_bus=EventBus(), build_index=False,
    )
    orch = SimpleNamespace(
        _timings={},
        run_budget=None,
        run_context=SimpleNamespace(
            workspace=workspace,
            cancellation_view=None,
            event_bus=EventBus(),
            artifacts=SimpleNamespace(),
            repository_indexer=None,
        ),
    )
    orch._executor = ExecutionStage(orch)
    orch.verify_goal = lambda state, answer: SimpleNamespace(
        missing=list(state.get("_scripted_gaps", [])),
    )
    return orch


def _mutation_state(**extra):
    state = {
        "requested_outcomes": ["USER_VISIBLE_OUTPUT", "FILE_MUTATION"],
        "artifact_required": True,
        "goal_state": GoalState(objective="写个文件", max_rounds=3).to_dict(),
        "inbox": {"next_step": [], "next_turn": []},
        "resolved_request": "写个文件",
    }
    state.update(extra)
    return state


def test_write_file_then_final_answer_records_task_and_inbox(
    monkeypatch, tmp_path,
):
    orch = _orchestrator(tmp_path)
    achat, _calls = _scripted([
        _turn(("write_file", {"path": "output/a.txt", "content": "hello"})),
        _turn(content="已写入 output/a.txt"),
    ])
    monkeypatch.setattr("agent.llm.llm.achat", achat)
    state = _mutation_state()

    answer = asyncio.run(
        AgentLoop(orch).run(state, user_input="写个文件", user_id="u")
    )

    assert answer == "已写入 output/a.txt"
    assert (tmp_path / "output/a.txt").read_text() == "hello"
    assert state["plan"][0]["status"] == "succeeded"
    assert state["plan"][0]["id"] == "loop-1"
    assert state["inbox"]["next_step"][0]["result"]["ok"] is True


def test_unauthorized_tool_is_never_exposed_or_executed(monkeypatch, tmp_path):
    orch = _orchestrator(tmp_path)
    # Empty outcomes → FILE_MUTATION not authorized → mutation tools hidden.
    authorization = EffectAuthorization(
        requested_outcomes=(RequestedOutcome.USER_VISIBLE_OUTPUT,),
        write_scopes=(),
    )
    names = set(build_toolset(authorization))
    assert "write_file" not in names
    assert "read_file" in names

    # Even if the model calls it anyway, the effect is denied per call:
    # UNKNOWN_TOOL for a hidden tool, EFFECT_SCOPE_VIOLATION for an
    # in-catalog but out-of-scope action.  No side effect happens either way.
    state = {
        "requested_outcomes": ["USER_VISIBLE_OUTPUT"],
        "goal_state": GoalState(objective="聊聊", max_rounds=1).to_dict(),
        "inbox": {"next_step": [], "next_turn": []},
    }
    seen_tool_errors = []

    turns = iter([
        _turn(("write_file", {"path": "output/x.txt", "content": "x"})),
        _turn(content="聊聊天气"),
    ])

    async def achat(messages, tools=None, **_kwargs):
        seen_tool_errors.extend(
            m["content"] for m in messages if m.get("role") == "tool"
        )
        return next(turns)

    import agent.llm as llm_module
    monkeypatch.setattr(llm_module.llm, "achat", achat)

    asyncio.run(
        AgentLoop(orch).run(state, user_input="聊", user_id="u")
    )
    assert any("UNKNOWN_TOOL" in item for item in seen_tool_errors)
    assert not (tmp_path / "output/x.txt").exists()
    assert all(
        task["status"] != "succeeded" for task in state.get("plan", [])
    )


def test_tool_call_errors_are_reported_as_tool_messages(tmp_path, monkeypatch):
    orch = _orchestrator(tmp_path)
    seen = []

    async def achat(messages, tools=None, **_kwargs):
        seen.extend(
            message["content"]
            for message in messages
            if message.get("role") == "tool"
        )
        return _turn(content="done")

    monkeypatch.setattr("agent.llm.llm.achat", achat)
    state = _mutation_state()

    async def scenario():
        loop = AgentLoop(orch)
        # Directly exercise _dispatch for the error paths.
        call = ToolCall(id="c1", name="no_such_tool", arguments={})
        msg = await loop._dispatch(
            call, build_toolset(
                EffectAuthorization.from_state(state, "写"),
            ), state, index=1,
            run_context=orch.run_context, user_id="u", user_input="写",
        )
        assert "ERROR[UNKNOWN_TOOL]" in msg["content"]

        call = ToolCall(id="c2", name="write_file", arguments={"path": "p"})
        msg = await loop._dispatch(
            call, build_toolset(
                EffectAuthorization.from_state(state, "写"),
            ), state, index=2,
            run_context=orch.run_context, user_id="u", user_input="写",
        )
        assert "ERROR[INVALID_TOOL_ARGUMENT]" in msg["content"]

        call = ToolCall(
            id="c3", name="write_file",
            arguments={"__invalid_arguments__": "{bad json"},
        )
        msg = await loop._dispatch(
            call, build_toolset(
                EffectAuthorization.from_state(state, "写"),
            ), state, index=3,
            run_context=orch.run_context, user_id="u", user_input="写",
        )
        assert "ERROR[INVALID_TOOL_ARGUMENT]" in msg["content"]

    asyncio.run(scenario())


def test_edit_file_maps_to_scoped_edit_and_rejects_non_unique(
    monkeypatch, tmp_path,
):
    orch = _orchestrator(tmp_path)
    (tmp_path / "output").mkdir()
    (tmp_path / "output/note.txt").write_text("dup\ndup\n")
    state = _mutation_state()

    async def scenario():
        loop = AgentLoop(orch)
        tools = build_toolset(EffectAuthorization.from_state(state, "改"))

        dup = await loop._dispatch(
            ToolCall(
                id="e1", name="edit_file",
                arguments={
                    "path": "output/note.txt", "old_string": "dup",
                    "new_string": "one",
                },
            ),
            tools, state, index=1,
            run_context=orch.run_context, user_id="u", user_input="改",
        )
        assert "ERROR[" in dup["content"]

        ok = await loop._dispatch(
            ToolCall(
                id="e2", name="edit_file",
                arguments={
                    "path": "output/note.txt", "old_string": "dup\ndup",
                    "new_string": "unique",
                },
            ),
            tools, state, index=2,
            run_context=orch.run_context, user_id="u", user_input="改",
        )
        assert "ERROR[" not in ok["content"]
        assert (tmp_path / "output/note.txt").read_text() == "unique\n"

    asyncio.run(scenario())


def test_verifier_gap_feeds_back_until_goal_rounds_exhaust(monkeypatch, tmp_path):
    orch = _orchestrator(tmp_path)
    orch.verify_goal = lambda state, answer: SimpleNamespace(
        missing=["deliverable:report:1"],
    )
    calls = []

    async def achat(messages, tools=None, **_kwargs):
        calls.append(list(messages))
        return _turn(content="已完成但没有文件")

    monkeypatch.setattr("agent.llm.llm.achat", achat)
    state = _mutation_state()
    state["goal_state"] = GoalState(objective="写报告", max_rounds=2).to_dict()

    answer = asyncio.run(
        AgentLoop(orch).run(state, user_input="写报告", user_id="u")
    )

    assert answer == "已完成但没有文件"
    assert len(calls) == 3  # initial + 2 feedback rounds (max_rounds=2)
    feedback = [
        m["content"] for m in calls[-1] if m.get("role") == "user"
    ]
    assert any("[运行时校验]" in item and "deliverable:report:1" in item
               for item in feedback)
    assert state["runtime_terminal_status"] == "FAILED_TERMINAL"
    assert state["runtime_failure_code"] == "GOAL_ROUND_LIMIT"
