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
