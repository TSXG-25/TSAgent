"""Executor contract — 统一执行器契约。

所有执行器遵循 execute(target, context) -> ExecutionResult。
target 类型由 ExecutionTarget 联合类型约束：
- Task（Tool / LLM / React 执行器消费单个任务）
- Workflow（Workflow 执行器消费整个工作流 DAG）

ExecutorFactory 按 ExecutionPlan.executor_type 解析执行器实例，
使用方（orchestrator / pipeline）只调用 factory.get(type)，
不做 if/elif 选择逻辑——避免 Dispatcher 换个地方复活。
"""
from typing import Protocol, Union, runtime_checkable

from agent.task import Task
from agent.workflow import Workflow, ExecutionContext, ExecutionResult

# 执行目标：单个 Task 或整个 Workflow
ExecutionTarget = Union[Task, Workflow]


@runtime_checkable
class Executor(Protocol):
    """所有执行器的统一接口。

    async execute(target, context) -> ExecutionResult
    """

    async def execute(
        self,
        target: ExecutionTarget,
        context: ExecutionContext,
    ) -> ExecutionResult:
        """执行目标，返回统一 ExecutionResult。"""


class ExecutorFactory:
    """执行器工厂 — 按 executor_type 解析执行器实例。

    注册方式：
        ExecutorFactory.register("tool", ToolExecutor)
        ExecutorFactory.register("llm", LLMExecutor)
        ExecutorFactory.register("react", ReactExecutor)
        ExecutorFactory.register("workflow", WorkflowExecutor)

    使用方式：
        executor = ExecutorFactory.get(plan.executor_type)
        result = await executor.execute(task, context)
    """

    _registry: dict[str, type] = {}

    @classmethod
    def register(cls, executor_type: str, executor_cls: type) -> None:
        """注册执行器类。"""
        cls._registry[executor_type] = executor_cls

    @classmethod
    def get(cls, executor_type: str) -> "Executor":
        """按类型获取执行器实例（每次返回新实例）。"""
        if executor_type not in cls._registry:
            raise KeyError(
                f"未注册执行器: {executor_type}（已注册: {list(cls._registry)}）"
            )
        return cls._registry[executor_type]()

    @classmethod
    def registered_types(cls) -> list[str]:
        """返回已注册的执行器类型列表。"""
        return sorted(cls._registry)


# 全局工厂单例
executor_factory = ExecutorFactory()
