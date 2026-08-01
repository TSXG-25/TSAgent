# tests/test_workflow_executor.py
"""WorkflowExecutor v2 测试 — Stage → Task → Compiler → ExecutionPlan → Executor → Artifact。

v2 不再使用 ExecutorRegistry / BaseExecutor（Stage 体系），
通过 ExecutorFactory + Compiler 走统一执行链。
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.workflow import (
    Workflow, Stage, ExecutionSpec, ExecutorType,
    ExecutionContext, ExecutionResult, OutputArtifact,
)
from agent.executor.contract import executor_factory
from agent.executor.executors.workflow import WorkflowExecutor as V2WorkflowExecutor


class FakeExecutor:
    """记录调用并返回成功结果的假执行器。"""

    def __init__(self):
        self.calls = []

    async def execute(self, target, context):
        self.calls.append(target)
        return ExecutionResult(success=True, outputs={"text": "fake output"})


class TestWorkflowExecutorV2:
    """验证 v2 编排链：Stage → Task → Compiler → Factory → Artifact。"""

    def setup_method(self):
        executor_factory._registry.clear()
        self.fake = FakeExecutor()
        # tool / llm 都接同一个 fake（验证编排层，不关心具体执行器行为）
        executor_factory.register("tool", lambda: self.fake)
        executor_factory.register("llm", lambda: self.fake)
        executor_factory.register("workflow", V2WorkflowExecutor)

    def _make_workflow(self, executor=ExecutorType.TOOL) -> Workflow:
        return Workflow(
            id="test_wf",
            stages=[
                Stage(
                    id="s1",
                    execution=ExecutionSpec(executor=executor),
                    description="生成 solution 文件",
                    outputs=[OutputArtifact(type="solution_file")],
                ),
            ],
        )

    def test_execute_single_stage(self):
        ctx = ExecutionContext(workflow_id="test_wf")
        result = asyncio.run(V2WorkflowExecutor().execute(self._make_workflow(), ctx))

        assert result.success, result.error
        assert len(self.fake.calls) == 1
        # Stage 被投影为 Task 后执行
        task = self.fake.calls[0]
        assert task.id == "s1"
        assert task.policy.executor in ("tool", "llm")

    def test_artifact_backfill(self):
        ctx = ExecutionContext(workflow_id="test_wf")
        result = asyncio.run(V2WorkflowExecutor().execute(self._make_workflow(), ctx))

        # outputs 声明的类型回填到 context.artifacts
        art = ctx.get_artifact("solution_file")
        assert art is not None
        assert art.type == "solution_file"
        assert art.content == "fake output"
        assert "_summary" in result.outputs

    def test_llm_stage_also_executes(self):
        ctx = ExecutionContext(workflow_id="test_wf")
        result = asyncio.run(V2WorkflowExecutor().execute(
            self._make_workflow(executor=ExecutorType.LLM), ctx
        ))
        assert result.success
        assert len(self.fake.calls) == 1
