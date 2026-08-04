# agent/workflow/context.py
"""ExecutionContext — 统一运行时上下文。

ExecutionContext 是 Agent 执行过程中所有数据的统一容器。
Executor、WorkflowExecutor 都共享同一个 Context。
合并了旧 ContextService 的 Prompt 组装能力。

包含:
- artifacts: 产物（带 parents 链）
- messages: 消息历史
- budget: 资源预算（BudgetManager）
- memory: 三层记忆引用
- variables: 运行时变量（workspace, env 等）
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from langchain_core.messages import BaseMessage
from .artifact import Artifact
from .budget import BudgetManager, BudgetSpec
from agent.task import Task


@dataclass
class ExecutionContext:
    """统一运行时上下文。
    
    Executor 和 Workflow 都只操作 ExecutionContext。
    不再直接拼接多个来源的数据。
    
    Attributes:
        artifacts: 已产出的 Artifact（按 type 索引）
        messages: 消息历史
        workflow_id: 当前 Workflow 标识
        stage_id: 当前 Stage 标识
        facts: 当前执行 Facts
        action_history: 操作历史（紧凑格式）
        failure_history: 失败记录（含 signature 去重）
        user_input: 用户原始输入
        task: 当前 Task
        memory: 附加运行时变量
        budget: 资源预算管理器
        variables: 运行环境变量（workspace, working_directory, env）
    """
    artifacts: Dict[str, Artifact] = field(default_factory=dict)
    messages: List[BaseMessage] = field(default_factory=list)
    workflow_id: str = ""
    stage_id: str = ""
    facts: Dict[str, Any] = field(default_factory=dict)
    action_history: List[Dict] = field(default_factory=list)
    failure_history: List[Dict] = field(default_factory=list)
    user_input: str = ""
    task: Optional[Task] = None
    memory: Dict[str, Any] = field(default_factory=dict)
    budget: Optional[BudgetManager] = None
    variables: Dict[str, Any] = field(default_factory=dict)
    
    # ── Artifact 操作 ──
    
    def get_artifact(self, type_name: str) -> Optional[Artifact]:
        """按类型查找最新的 Artifact。"""
        return self.artifacts.get(type_name)
    
    def set_artifact(self, artifact: Artifact):
        """存入 Artifact，自动建立 parent 链。"""
        existing = self.artifacts.get(artifact.type)
        if existing and existing.id != artifact.id:
            artifact.add_parent(existing)
        self.artifacts[artifact.type] = artifact

    def get_artifact_by_id(self, artifact_id: str) -> Optional[Artifact]:
        """按 ID 查找 Artifact。"""
        for art in self.artifacts.values():
            if art.id == artifact_id:
                return art
        return None
    
    def trace_artifact(self, type_name: str) -> List[Artifact]:
        """追踪某类型 Artifact 的完整溯源链。"""
        art = self.artifacts.get(type_name)
        if art:
            return art.trace()
        return []
    
    # ── 操作记录 ──
    
    def record_action(self, action: Dict):
        """记录操作到 action_history。"""
        self.action_history.append(action)
        if len(self.action_history) > 10:
            self.action_history = self.action_history[-10:]
    
    def record_failure(self, failure: Dict):
        """记录失败到 failure_history（自动去重）。"""
        sig = failure.get("signature", "")
        if sig:
            exists = any(f.get("signature") == sig for f in self.failure_history)
            if exists:
                return
        self.failure_history.append(failure)
        if len(self.failure_history) > 5:
            self.failure_history = self.failure_history[-5:]
    
    # ── Budget 操作 ──
    
    def ensure_budget(self, spec: Optional[BudgetSpec] = None):
        """确保有 BudgetManager。"""
        if self.budget is None:
            self.budget = BudgetManager(spec)
    
    def check_budget(self) -> bool:
        """检查是否在预算内。"""
        if self.budget is None:
            return True
        return self.budget.within_budget()
    
    def count_step(self):
        """计数一步。"""
        if self.budget:
            self.budget.count_step()
    
    def budget_prompt(self) -> str:
        """生成预算信息提示。"""
        if self.budget:
            return self.budget.to_prompt()
        return ""
    
    # ── 变量操作 ──
    
    def set_var(self, key: str, value: Any):
        """设置运行时变量。"""
        self.variables[key] = value
    
    def get_var(self, key: str, default: Any = None) -> Any:
        """获取运行时变量。"""
        return self.variables.get(key, default)
    
    def get_working_directory(self) -> str:
        """获取工作目录。"""
        return self.variables.get("working_directory", ".")
    
    # ── 序列化 ──
    
    def to_state_dict(self) -> Dict:
        """转换为状态字典（用于 AgentState）。"""
        return {
            "artifacts": {k: v.summary[:200] for k, v in self.artifacts.items()},
            "facts": dict(self.facts),
            "action_history": list(self.action_history[-5:]),
            "user_input": self.user_input,
        }

    def runtime_view(self, user_id: str = "", request_id: str = ""):
        """返回共享 RuntimeContext 快照，不暴露可变执行容器。"""
        from agent.context.contracts import RuntimeContext

        return RuntimeContext(
            request_id=request_id,
            user_id=user_id,
            query=self.user_input,
            metadata={
                "workflow_id": self.workflow_id,
                "stage_id": self.stage_id,
            },
        )

    def executor_view(self, user_id: str = "", request_id: str = ""):
        """返回 ExecutorContext 的只读投影。"""
        from agent.context.contracts import ExecutorContext

        if self.task is None:
            raise ValueError("ExecutionContext.task 为空，无法创建 ExecutorContext")
        return ExecutorContext(
            runtime=self.runtime_view(user_id=user_id, request_id=request_id),
            task=self.task,
            artifacts=self.artifacts,
            facts=self.facts,
            action_history=self.action_history,
            failure_history=self.failure_history,
            variables=self.variables,
        )

    def reflection_view(
        self,
        task_id: str = "",
        failure: str = "",
        evidence: Optional[list] = None,
        symptom: str = "",
    ):
        """返回 ReflectionContext，仅携带失败证据而非整个 Runtime。"""
        from agent.context.contracts import ReflectionContext

        return ReflectionContext(
            runtime=self.runtime_view(),
            task_id=task_id or (self.task.id if self.task else ""),
            failure=failure,
            evidence=evidence or [],
            symptom=symptom,
            retry_count=len(self.failure_history),
            last_action=(self.action_history[-1].get("task", "")
                          if self.action_history else ""),
        )
