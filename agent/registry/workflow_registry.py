"""WorkflowRegistry — 只负责 canonical Workflow 注册、查询和列表。"""
from typing import List, Optional

from agent.workflow import Workflow


class WorkflowRegistry:
    def __init__(self):
        self._workflows: dict = {}

    def register(self, name: str, workflow: Workflow) -> None:
        if not isinstance(workflow, Workflow):
            raise TypeError(
                f"WorkflowRegistry 只接受 agent.workflow.Workflow，收到 {type(workflow).__name__}"
            )
        self._workflows[name] = workflow

    def get(self, name: str) -> Optional[Workflow]:
        return self._workflows.get(name)

    def list(self) -> List[str]:
        return list(self._workflows.keys())


workflow_registry = WorkflowRegistry()
