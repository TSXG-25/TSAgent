# tests/test_workflow_contract.py
"""Contract tests for the unified execution chain.

Phase A: Stage -> Task projection (to_task).
Phase B: Executor execute(target, context) contract.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.workflow import Stage, ExecutionSpec, ExecutorType, ToolPolicy, ToolArgument
from agent.task import Task, Verb
from agent.executor.contract import ExecutorFactory, executor_factory
from agent.registry.tool_registry import registry as tool_registry


class TestExecutorContract:
    """ExecutorFactory 契约：注册、解析、未注册报错。"""

    def setup_method(self):
        # 隔离测试：清空工厂注册
        executor_factory._registry.clear()

    def test_factory_register_and_get(self):
        from agent.executor.llm_executor import LLMExecutor

        executor_factory.register("llm", LLMExecutor)
        assert "llm" in executor_factory.registered_types()
        inst = executor_factory.get("llm")
        assert isinstance(inst, LLMExecutor)

    def test_factory_unknown_type_raises(self):
        import pytest

        with pytest.raises(KeyError):
            executor_factory.get("nonexistent")


class TestToolExecutor:
    """ToolExecutor（经 ExecutorFactory）执行确定性 ExecutionPlan。"""

    def setup_method(self):
        from agent.executor.contract import executor_factory
        from agent.registry.tool_registry import registry as tr

        executor_factory._registry.clear()
        tr._tools.clear()
        tr._categories.clear()
        tr._tags.clear()

        def test_echo(msg: str) -> str:
            """Echo a message."""
            return f"echo: {msg}"

        tr.register(test_echo, name="test_echo", category="test", tags=["test"])

        # 重新注册 factory（测试隔离导致 factory 被清空）
        from agent.executor.executors.tool import ToolExecutor
        from agent.executor.llm_executor import LLMExecutor
        executor_factory.register("tool", ToolExecutor)
        executor_factory.register("llm", LLMExecutor)

    def test_tool_executor_executes_plan(self):
        import asyncio

        from agent.executor.contract import executor_factory
        from agent.workflow import ExecutionContext
        from agent.task import Task, Verb, ExecutionPlan, ExecutionStep

        task = Task(id="t1", verb=Verb.READ, target="", goal="echo hello")
        plan = ExecutionPlan(
            task=task,
            steps=[ExecutionStep(tool="test_echo", args={"msg": "hello"}, outputs=["out"])],
        )
        ctx = ExecutionContext(task=task)
        ctx.set_var("execution_plan", plan)

        executor = executor_factory.get("tool")
        result = asyncio.run(executor.execute(task, ctx))

        assert result.success, result.error
        assert "echo: hello" in result.text
        assert "out" in result.metadata["variables"]

    def test_tool_executor_missing_plan(self):
        import asyncio

        from agent.executor.contract import executor_factory
        from agent.workflow import ExecutionContext
        from agent.task import Task, Verb

        task = Task(id="t2", verb=Verb.READ, target="", goal="x")
        ctx = ExecutionContext(task=task)  # 无 execution_plan

        executor = executor_factory.get("tool")
        result = asyncio.run(executor.execute(task, ctx))

        assert not result.success
        assert "execution_plan" in result.error


class TestStageToTask:
    """Stage.to_task() 投影到统一 Task 模型。"""

    def test_tool_stage_projection(self):
        stage = Stage(
            id="s1",
            execution=ExecutionSpec(
                executor=ExecutorType.TOOL,
                max_retries=2,
                tool_policy=ToolPolicy(allow=["read_file"]),
            ),
            description="读取 solution.py 文件",
            depends=["s0"],
            outputs=[],
        )
        task = stage.to_task(goal="读取 solution.py 文件")

        assert isinstance(task, Task)
        assert task.id == "s1"
        assert task.verb == Verb.READ
        assert task.dependencies == ["s0"]
        assert task.policy.max_retries == 2
        assert task.policy.tool_policy == {"allow": ["read_file"]}
        assert task.policy.executor == "tool"

    def test_llm_stage_projection(self):
        stage = Stage(
            id="s2",
            execution=ExecutionSpec(executor=ExecutorType.LLM),
            description="分析需求",
        )
        task = stage.to_task(goal="分析需求")

        # llm stage → plan executor "llm"，verb 推断为 EXPLAIN
        assert task.policy.executor == "llm"
        assert task.verb == Verb.EXPLAIN

    def test_defaults(self):
        stage = Stage(id="s4", execution=ExecutionSpec(executor=ExecutorType.TOOL))
        task = stage.to_task()
        assert task.id == "s4"
        assert task.goal == "s4"  # fallback 到 stage.id
        assert task.policy.max_retries == 0
        assert task.policy.validators == []

    def test_argument_bindings_are_projected_to_task(self):
        stage = Stage(
            id="write",
            execution=ExecutionSpec(
                executor=ExecutorType.TOOL,
                tool_policy=ToolPolicy(allow=["write_file"]),
            ),
            arguments=[
                ToolArgument(param="path", constant="output/example.py"),
                ToolArgument(param="content", artifact="verified_code"),
            ],
        )

        task = stage.to_task(goal="写入输出文件")

        assert task.inputs == {
            "path": {"constant": "output/example.py"},
            "content": {"artifact": "verified_code"},
        }

    def test_append_intent_is_lowered_to_append_mode(self):
        from agent.compiler.rules.write_rule import WriteRule

        stage = Stage(
            id="append",
            execution=ExecutionSpec(executor=ExecutorType.TOOL),
            description="追加内容到 notes.txt",
        )
        task = stage.to_task(goal="追加内容到 notes.txt")
        task = task.model_copy(update={
            "target": "notes.txt",
            "target_type": "file",
            "inputs": {"content": "new line"},
        })

        plan = WriteRule().build(task)

        assert plan.steps[-1].args["mode"] == "append"
