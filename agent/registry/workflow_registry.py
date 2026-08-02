"""WorkflowRegistry — 只负责 Workflow 注册、查询和列表。不负责匹配/路由。"""
from typing import Any, List, Optional


class WorkflowRegistry:
    def __init__(self):
        self._workflows: dict = {}

    def register(self, name: str, workflow: Any):
        self._workflows[name] = workflow

    def get(self, name: str) -> Optional[Any]:
        return self._workflows.get(name)

    def list(self) -> List[str]:
        return list(self._workflows.keys())


workflow_registry = WorkflowRegistry()