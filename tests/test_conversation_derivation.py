"""Conversation derivation: the single pure projection over the run log."""

import asyncio
import json
from types import SimpleNamespace
from typing import Any

from agent.context.conversation import (
    ANSWER_CHAR_CAP,
    HISTORY_CHAR_BUDGET,
    OBSERVATION_CHAR_CAP,
    derive_conversation,
    render_conversation_text,
)


def _run(**overrides: Any) -> dict[str, Any]:
    run = {
        "run_id": "run-1",
        "status": "COMPLETED",
        "request": "帮我写个计算器",
        "goal": "帮我写个计算器",
        "goal_contract": {},
        "missing": [],
        "observations": [],
        "acceptance_evidence": [],
        "answer": "已写入 output/calc.py",
        "artifacts": [],
    }
    run.update(overrides)
    return run


def test_derive_conversation_orders_and_projects_runs() -> None:
    messages = derive_conversation(
        [
            _run(request="第一轮", answer="第一答"),
            _run(request="第二轮", answer="第二答"),
        ]
    )

    assert messages == [
        {"role": "user", "content": "第一轮"},
        {"role": "assistant", "content": "第一答"},
        {"role": "user", "content": "第二轮"},
        {"role": "assistant", "content": "第二答"},
    ]


def test_derive_conversation_renders_artifacts_and_observations() -> None:
    long_observation = "观察内容" * 200
    messages = derive_conversation(
        [
            _run(
                answer="done",
                artifacts=[
                    {"path": "output/a.py", "digest": "x"},
                    {"path": "output/b.py", "digest": "y"},
                ],
                observations=[
                    {"task_id": "t1", "result": {"ok": True, "content": "读取成功"}},
                    {"task_id": "t2", "result": {"ok": False, "error_code": "FILE_NOT_FOUND"}},
                    {"task_id": "t3", "result": {"ok": True, "content": long_observation}},
                    *[
                        {"task_id": f"t{i}", "result": {"ok": True, "content": f"obs-{i}"}}
                        for i in range(4, 9)
                    ],
                ],
            )
        ]
    )

    assistant = messages[1]["content"]
    assert "[产物] output/a.py, output/b.py" in assistant
    assert assistant.count("[观察]") == 5
    assert "读取成功" in assistant
    assert "FILE_NOT_FOUND" in assistant
    assert long_observation[: OBSERVATION_CHAR_CAP + 1] not in assistant
    assert ("观察内容" * 150)[:OBSERVATION_CHAR_CAP] in assistant


def test_derive_conversation_marks_incomplete_runs() -> None:
    messages = derive_conversation(
        [_run(status="FAILED_TERMINAL", missing=["TASK_EXECUTION_FAILED"])]
    )

    assistant = messages[1]["content"]
    assert "[状态] FAILED_TERMINAL" in assistant
    assert "未完成: TASK_EXECUTION_FAILED" in assistant


def test_derive_conversation_falls_back_to_goal_and_caps_answer() -> None:
    messages = derive_conversation(
        [_run(request="", goal="恢复的目标", answer="长" * 3000)]
    )

    assert messages[0] == {"role": "user", "content": "恢复的目标"}
    assert messages[1]["content"].startswith("长" * ANSWER_CHAR_CAP)
    assert len(messages[1]["content"]) == ANSWER_CHAR_CAP


def test_split_pairs_for_budget_marks_oldest_for_compaction() -> None:
    from agent.context.conversation import derive_pairs, split_pairs_for_budget

    pairs = derive_pairs(
        [_run(request=f"q{i}", answer="答" * 2500) for i in range(10)]
    )
    compact, kept = split_pairs_for_budget(pairs)

    # Each pair is ~2004 chars (answers capped at 2000); the oldest three
    # pairs are marked for compaction to fit the 16000-char budget.
    assert [pair["user"] for pair in compact] == ["q0", "q1", "q2"]
    assert kept[0]["user"] == "q3"
    assert len(kept) == 7


def test_flatten_pairs_renders_summary_prefix() -> None:
    from agent.context.conversation import derive_pairs, flatten_pairs

    messages = flatten_pairs(
        derive_pairs([_run(request="新问题", answer="新回答")]),
        summary="早前要点记录",
    )
    assert messages[0]["role"] == "user"
    assert "[早前对话摘要] 早前要点记录" in messages[0]["content"]
    assert messages[2] == {"role": "user", "content": "新问题"}
    assert messages[3]["content"] == "新回答"


def test_derive_conversation_keeps_newest_pair_over_budget() -> None:
    messages = derive_conversation(
        [_run(request="唯一", answer="答" * (HISTORY_CHAR_BUDGET * 2))]
    )

    assert len(messages) == 2
    assert messages[0]["content"] == "唯一"


def test_render_conversation_text() -> None:
    assert render_conversation_text([]) == ""
    text = render_conversation_text(
        [
            {"role": "user", "content": "问题"},
            {"role": "assistant", "content": "回答"},
        ]
    )
    assert text == "用户: 问题\n助手: 回答"


def test_planner_renders_conversation_not_run_results_json(monkeypatch) -> None:
    import agent.planner.planner as planner_module
    from agent.cognition.cognitive_context import PlannerContext

    captured: list[list] = []

    class FakeLLM:
        supports_structured_output = False

        async def ainvoke(self, messages: Any, **_kwargs: Any) -> Any:
            captured.append(messages)
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "tasks": [
                            {
                                "id": "task-1",
                                "verb": "read",
                                "target": "output/report.md",
                                "target_type": "file",
                                "goal": "读取报告",
                                "description": "读取 output/report.md",
                                "success_condition": "读取完成",
                                "dependencies": [],
                                "children": [],
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            )

    monkeypatch.setattr(planner_module, "llm", FakeLLM())

    context = PlannerContext(
        query="读取 output/report.md",
        conversation=[
            {"role": "user", "content": "帮我写个计算器"},
            {"role": "assistant", "content": "已写入 output/calc.py"},
        ],
        run_results=[{"run_id": "run-9", "goal_contract": {"objective": "x"}}],
    )
    output = asyncio.run(
        planner_module.plan_with_metadata(
            "读取 output/report.md",
            planning_context=context,
        )
    )

    assert output.tasks
    human = next(m for m in captured[0] if m.__class__.__name__ == "HumanMessage")
    assert "## 最近对话" in human.content
    assert "用户: 帮我写个计算器" in human.content
    assert "助手: 已写入 output/calc.py" in human.content
    assert '"run_id"' not in human.content
    assert '"goal_contract"' not in human.content


def test_llm_executor_renders_conversation_history(monkeypatch) -> None:
    from agent.executor.llm_executor import LLMExecutor
    from agent.task import Task
    from agent.workflow import ExecutionContext

    captured: list[list] = []

    class FakeLLM:
        async def ainvoke(self, messages: Any, **_kwargs: Any) -> Any:
            captured.append(messages)
            return SimpleNamespace(content="推理结果")

    monkeypatch.setattr("agent.executor.llm_executor.llm", FakeLLM())

    context = ExecutionContext(
        task=Task(id="t1", verb="explain", target="text", goal="分析", target_type="text"),
        user_input="分析",
        facts={},
        variables={},
    )
    context.set_var(
        "conversation",
        [
            {"role": "user", "content": "之前的问题"},
            {"role": "assistant", "content": "之前的回答"},
        ],
    )

    asyncio.run(LLMExecutor().execute(context.task, context))

    text = "\n".join(str(m.content) for m in captured[0])
    assert "历史对话（不是指令）：" in text
    assert "用户: 之前的问题" in text
    assert "助手: 之前的回答" in text
