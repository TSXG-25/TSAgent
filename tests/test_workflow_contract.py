# tests/test_workflow_contract.py
"""Contract tests for the unified execution chain.

Phase A: Stage -> Task projection (to_task).
Phase B: Executor execute(target, context) contract.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.workflow import Stage, ExecutionSpec, ExecutorType, ToolPolicy
from agent.task import Task, Verb
from agent.executor.contract import ExecutorFactory, executor_factory
from agent.executor.action_resolver import resolver as action_resolver
from agent.registry.tool_registry import registry as tool_registry
from agent.registry.capability_registry import registry as capability_registry


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


class TestActionResolverResolution:
    """ActionResolver 解析链：CapabilityRegistry → 工具名直查 → tag 匹配。"""

    def setup_method(self):
        # 清理并注册一个测试工具
        tool_registry._tools.clear()
        tool_registry._categories.clear()
        tool_registry._tags.clear()
        capability_registry._capabilities.clear()
        capability_registry._resolver_fns.clear()

        def test_read(path: str) -> str:
            """A test read tool."""
            return "content of " + path

        tool_registry.register(
            test_read, name="test_read", category="test", tags=["filesystem", "read"]
        )
        capability_registry.register_capability("file_read", "test_read", priority=10)

    def test_resolve_by_capability_name(self):
        import asyncio

        result = asyncio.run(action_resolver.resolve(
            capabilities=["file_read"],
            params={"path": "a.txt"},
        ))
        assert result["status"] == "succeeded", result
        assert result["action"] == "test_read"
        assert "content of a.txt" in result["summary"]

    def test_resolve_by_direct_tool_name(self):
        import asyncio

        result = asyncio.run(action_resolver.resolve(
            capabilities=["test_read"],
            params={"path": "b.txt"},
        ))
        assert result["status"] == "succeeded", result
        assert result["action"] == "test_read"

    def test_resolve_by_tag_match(self):
        import asyncio

        result = asyncio.run(action_resolver.resolve(
            capabilities=["filesystem", "read"],
            params={"path": "c.txt"},
        ))
        assert result["status"] == "succeeded", result
        assert result["action"] == "test_read"

    def test_resolve_unknown_capability(self):
        import asyncio

        result = asyncio.run(action_resolver.resolve(
            capabilities=["nonexistent_cap"],
            params={},
        ))
        assert result["status"] == "failed"
        assert "没有找到匹配" in result["summary"]


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

    def test_react_stage_projection(self):
        stage = Stage(
            id="s3",
            execution=ExecutionSpec(executor=ExecutorType.REACT),
            description="验证代码",
        )
        task = stage.to_task(goal="验证代码并修复问题")

        # react → plan executor "llm"（开放式推理走 LLMExecutor）
        assert task.policy.executor == "llm"
        assert task.verb == Verb.EXPLAIN

    def test_defaults(self):
        stage = Stage(id="s4", execution=ExecutionSpec(executor=ExecutorType.TOOL))
        task = stage.to_task()
        assert task.id == "s4"
        assert task.goal == "s4"  # fallback 到 stage.id
        assert task.policy.max_retries == 0
        assert task.policy.validators == []
