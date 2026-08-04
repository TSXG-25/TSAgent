# agent/workflow/__init__.py
"""TSAgent 2.0 canonical Workflow DSL。"""
from .executor_type import ExecutorType
from .tool_policy import ToolPolicy
from .artifact import Artifact, InputArtifact, OutputArtifact
from .execution import ExecutionSpec
from .stage import Stage
from .workflow import Workflow
from .context import ExecutionContext
from .argument import ToolArgument
from .tool_result import ToolResult
from .result import ExecutionResult
from .budget import BudgetSpec, BudgetState, BudgetManager

__all__ = [
    "ExecutorType", "ToolPolicy", "Artifact", "InputArtifact", "OutputArtifact",
    "ExecutionSpec", "Stage", "Workflow", "ExecutionContext",
    "ToolArgument", "ToolResult", "ExecutionResult",
    "BudgetSpec", "BudgetState", "BudgetManager",
]
