"""Deterministic P1-H1 capability closeout contracts."""

import asyncio

import pytest

from agent.compiler.context import CompilerContext
from agent.compiler.rules import DEFAULT_RULES
from agent.compiler.tool_selector import Compiler
from agent.cognition.cognitive_context import CognitiveContext
from agent.cognition.intent_engine import IntentEngine
from agent.executor.plan_executor import PlanExecutor
from agent.executor.verifier import ExecutionArtifacts, ExecutionVerifier
from agent.event_bus import EventBus
from agent.services.workspace_service import WorkspaceService
from agent.orchestrator.planner import (
    _build_file_operation_task,
    _extract_literal_file_append,
    _build_modify_execution_tasks,
    _build_text_transform_execution,
    _explicit_unsupported_capability,
)
from agent.task import ExecutionPlan, Task, Verb


def _compiler() -> Compiler:
    compiler = Compiler()
    for rule in DEFAULT_RULES:
        compiler.add_rule(rule)
    return compiler


def test_file_operations_lower_to_registered_exact_primitives() -> None:
    compiler = _compiler()
    cases = [
        ("把 output/a.txt 复制到 output/b.txt", Verb.COPY, "filesystem.copy"),
        ("把 output/a.txt 移动到 output/b.txt", Verb.MOVE, "filesystem.move"),
        ("删除 output/a.txt", Verb.DELETE, "filesystem.delete"),
    ]
    for request, verb, expected_tool in cases:
        task = _build_file_operation_task(request)
        assert task is not None
        assert task.verb is verb
        plan = compiler.compile(task, context=CompilerContext())
        assert plan.steps[-1].tool == expected_tool
        assert plan.steps[-1].args.get("exact") is True


def test_copy_move_delete_have_ground_truth_verification(monkeypatch, tmp_path) -> None:
    import tools.filesystem as filesystem

    monkeypatch.setattr(filesystem, "ROOT", tmp_path)
    monkeypatch.setattr(filesystem, "_get_workspace_service", lambda: None)
    filesystem._path_cache.clear()
    source = tmp_path / "output" / "source.txt"
    source.parent.mkdir()
    source.write_text("stable content\n", encoding="utf-8")
    compiler = _compiler()
    executor = PlanExecutor()
    workspace = WorkspaceService.scoped(tmp_path, event_bus=EventBus(), build_index=True)

    copy_task = _build_file_operation_task(
        "把 output/source.txt 复制到 output/copied.txt"
    )
    assert copy_task is not None
    copy_plan = compiler.compile(copy_task, context=CompilerContext())
    copy_result = asyncio.run(executor.execute(copy_plan, workspace=workspace))
    assert copy_result["_error"] == ""
    assert (tmp_path / "output/copied.txt").read_text() == source.read_text()
    copy_verification = ExecutionVerifier().verify(
        copy_plan,
        ExecutionArtifacts(file_operations=copy_result["_file_operations"]),
        task=copy_task,
        workspace=workspace,
    )
    assert copy_verification.success is True

    move_task = _build_file_operation_task(
        "把 output/copied.txt 移动到 output/moved.txt"
    )
    assert move_task is not None
    move_result = asyncio.run(executor.execute(
        compiler.compile(move_task, context=CompilerContext()),
        workspace=workspace,
    ))
    assert move_result["_error"] == ""
    assert not (tmp_path / "output/copied.txt").exists()
    assert (tmp_path / "output/moved.txt").exists()

    delete_task = _build_file_operation_task("删除 output/moved.txt")
    assert delete_task is not None
    delete_plan = compiler.compile(delete_task, context=CompilerContext())
    delete_result = asyncio.run(executor.execute(delete_plan, workspace=workspace))
    assert delete_result["_error"] == ""
    assert not (tmp_path / "output/moved.txt").exists()
    delete_verification = ExecutionVerifier().verify(
        delete_plan,
        ExecutionArtifacts(file_operations=delete_result["_file_operations"]),
        task=delete_task,
        workspace=workspace,
    )
    assert delete_verification.success is True
    workspace.close()


def test_read_transform_write_is_deterministic_and_no_llm() -> None:
    built = _build_text_transform_execution(
        "读取 output/in.txt，把每行转成大写写入 output/out.txt"
    )
    assert built is not None
    _, plan = built
    assert "llm" not in [step.tool for step in plan.steps]
    assert [step.tool for step in plan.steps] == [
        "workspace",
        "filesystem.read",
        "text.transform_upper",
        "workspace",
        "filesystem.write",
    ]


def test_literal_append_preserves_existing_content_and_uses_append_mode() -> None:
    assert _extract_literal_file_append(
        "向 output/log.txt 追加一行 append-once"
    ) == ("output/log.txt", "append-once")


def test_modify_plan_preserves_unrequested_python_functions() -> None:
    original = (
        "def add(a, b):\n    return a - b\n\n"
        "def multiply(a, b):\n    return a * b\n"
    )
    changed = (
        "def add(a, b):\n    return a + b\n\n"
        "def multiply(a, b):\n    return a + b\n"
    )
    with pytest.raises(ValueError, match="PRESERVATION_VIOLATION"):
        PlanExecutor._validate_code_preservation(
            original,
            changed,
            "修复 add 函数的 bug",
        )

    tasks = _build_modify_execution_tasks(
        "修复 output/calc.py 的 add 函数，然后运行 output/test_calc.py 验证"
    )
    assert tasks is not None
    assert [task.verb for task in tasks] == [Verb.MODIFY, Verb.EXECUTE]
    assert tasks[1].target == "output/test_calc.py"

    inline = _build_modify_execution_tasks(
        "修改 output/str.py 的 shout 函数：返回大写+感叹号，然后运行一行 Python 验证结果"
    )
    assert inline is not None
    assert inline[1].inputs["verification_code"].startswith("from output.str import shout")


def test_unsupported_explicit_capability_is_deterministic() -> None:
    assert _explicit_unsupported_capability(
        "用 send_email 工具给 test@example.com 发送一封测试邮件"
    ) == "send_email"


def test_modify_language_cannot_be_downgraded_to_math_chat() -> None:
    intent = IntentEngine().analyze(CognitiveContext(
        query=(
            "把 output/m05_stable.py 里的 stable 函数改成返回 x * 3。"
            "注意不要依赖任何网络数据。"
        )
    ))
    assert intent.requires_execution is True
    assert intent.domain == "development"
