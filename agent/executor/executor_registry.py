"""ExecutorRegistry — 执行器注册中心。"""
from typing import Any, Dict, Type
from agent.workflow import ExecutionContext, Stage, ExecutionResult


class BaseExecutor:
    """执行器基类。所有 Executor 返回 ExecutionResult。"""
    
    async def execute(self, stage: Stage, context: ExecutionContext, prompt: str) -> ExecutionResult:
        raise NotImplementedError


class ExecutorRegistry:
    _executors: Dict[str, Type[BaseExecutor]] = {}
    _instances: Dict[str, BaseExecutor] = {}

    @classmethod
    def register(cls, executor_type: str, executor_cls: Type[BaseExecutor]):
        cls._executors[executor_type] = executor_cls

    @classmethod
    def get(cls, executor_type: str) -> BaseExecutor:
        if executor_type not in cls._instances:
            cls._instances[executor_type] = cls._executors[executor_type]()
        return cls._instances[executor_type]

    @classmethod
    def list_types(cls) -> list:
        return list(cls._executors.keys())