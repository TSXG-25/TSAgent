# agent/workflow/stage.py
"""Stage — 工作流中的单个阶段。

Stage 是 Task 模板的超集：除了 Task 的 verb/target/goal，还携带
execution（ExecutorType + ToolPolicy）、validators、outputs 等元数据。

执行时通过 stage.to_task() 投影到统一 Task 模型（agent/task/__init__.py），
进入统一执行链（Compiler → ExecutionPlan → Executor）。
Stage 不被降级——它保留全部元数据，to_task() 只是投影。
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from agent.task import Task, Verb, TaskPolicy
from .execution import ExecutionSpec
from .artifact import InputArtifact, OutputArtifact
from .argument import ToolArgument


@dataclass
class Stage:
    """工作流阶段。"""
    id: str
    execution: ExecutionSpec
    arguments: List[ToolArgument] = field(default_factory=list)
    inputs: List[InputArtifact] = field(default_factory=list)
    outputs: List[OutputArtifact] = field(default_factory=list)
    depends: Optional[List[str]] = None
    description: str = ""
    validators: Optional[List[Any]] = None
    required_outputs: Optional[List[str]] = None

    def to_task(self, goal: str = "") -> Task:
        """投影到统一 Task 模型（带 policy）。

        Stage 保留超集（validators/retry/budget/tool_policy/outputs），
        to_task() 只投影执行链需要的字段。

        Args:
            goal: 任务目标描述（默认用 stage.description 或 id）
        """
        # 从 executor_type 映射到统一执行器名
        executor_name = self.execution.executor.value  # "llm" | "tool" | "react" | "pipeline"
        if executor_name in ("llm", "react"):
            plan_executor = "llm"
        else:
            plan_executor = "tool"

        tool_policy = None
        if self.execution.tool_policy is not None:
            tool_policy = {
                "allow": list(self.execution.tool_policy.allow or []),
            }

        budget_dict = None
        spec = getattr(self.execution, "budget", None)
        if spec is not None and hasattr(spec, "to_dict"):
            budget_dict = spec.to_dict()

        # 确定 verb：优先从 goal 推断，回退到 READ（确定性读取）
        verb = Verb.READ
        goal_lower = (goal or self.description or "").lower()
        verb_hints = {
            "write": ["写入", "写", "创建", "输出", "保存", "write", "create"],
            "modify": ["修改", "编辑", "更新", "更改", "重构", "优化", "modify", "edit", "update"],
            "execute": ["运行", "执行", "run", "execute"],
            "search": ["搜索", "查找", "查询", "search", "find"],
            "design": ["设计", "规划", "design", "plan", "分析", "analyze"],
            "explain": ["解释", "说明", "总结", "explain", "analyze", "summarize"],
            "verify": ["验证", "测试", "检查", "verify", "test", "check"],
        }
        for v, hints in verb_hints.items():
            if any(h in goal_lower for h in hints):
                verb = Verb(v)
                break
        if verb == Verb.READ and executor_name == "llm":
            verb = Verb.EXPLAIN

        return Task(
            id=self.id,
            verb=verb,
            target="",
            kind="",
            goal=goal or self.description or self.id,
            dependencies=list(self.depends or []),
            policy=TaskPolicy(
                executor=plan_executor,
                max_retries=self.execution.max_retries or 0,
                timeout=self.execution.timeout,
                max_tokens=self.execution.max_tokens,
                budget=budget_dict,
                validators=list(self.validators or []),
                tool_policy=tool_policy,
                required_outputs=list(self.required_outputs or []),
            ),
        )
