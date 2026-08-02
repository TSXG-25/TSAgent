"""Node — 统一执行图节点抽象。

统一了 Workflow Stage / Planner Task / ReAct Step 三种概念。
整个 Runtime 只认识 Node。

Node 的关键属性：
- executor_type: 指明由哪种 Executor 执行
- dependencies: DAG 依赖关系
- budget: 资源预算
- inputs / outputs: Artifact 声明
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from .executor_type import ExecutorType
from .artifact import Artifact, InputArtifact, OutputArtifact
from .execution import ExecutionSpec
from .budget import BudgetSpec


@dataclass
class Node:
    """统一的执行图节点。
    
    Workflow Stage、Planner Task、ReAct Step 都使用此模型。
    
    Attributes:
        id: 节点唯一标识
        goal: 节点目标描述（一句话）
        description: 详细说明
        executor_type: 执行器类型（llm / tool / react / pipeline）
        execution_spec: 执行配置（重试、超时、工具策略等）
        dependencies: 依赖的节点 ID 列表（DAG）
        children: 子节点列表（层级树结构）
        inputs: 输入 Artifact 声明
        outputs: 输出 Artifact 声明
        budget: 资源预算
        success_condition: 成功条件描述
        validators: 验证器列表
        metadata: 额外元数据
    """
    id: str
    goal: str
    description: str = ""
    executor_type: str = "react"  # "llm" | "tool" | "react" | "workflow"
    execution_spec: Optional[ExecutionSpec] = None
    dependencies: List[str] = field(default_factory=list)
    children: List["Node"] = field(default_factory=list)
    inputs: List[InputArtifact] = field(default_factory=list)
    outputs: List[OutputArtifact] = field(default_factory=list)
    budget: Optional[BudgetSpec] = None
    success_condition: str = ""
    validators: Optional[List[Any]] = None
    metadata: Optional[Dict] = None

    # ── 运行时状态（Executor 填充） ──
    status: str = "pending"  # pending | running | succeeded | failed | skipped
    observations: List[Dict] = field(default_factory=list)
    error: str = ""
    facts: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        """转换为与现有 Task dict 兼容的格式。"""
        return {
            "id": self.id,
            "goal": self.goal,
            "description": self.description,
            "success_condition": self.success_condition,
            "dependencies": list(self.dependencies),
            "children": [c.to_dict() for c in self.children],
            "status": self.status,
            "observations": list(self.observations),
            "error": self.error,
            "facts": dict(self.facts),
        }

    @classmethod
    def from_dict(cls, d: Dict) -> "Node":
        """从 Task dict 创建 Node。"""
        children = [cls.from_dict(c) for c in d.get("children", [])]
        return cls(
            id=d.get("id", "unknown"),
            goal=d.get("goal", ""),
            description=d.get("description", ""),
            success_condition=d.get("success_condition", ""),
            dependencies=d.get("dependencies", []),
            children=children,
            status=d.get("status", "pending"),
            observations=d.get("observations", []),
            error=d.get("error", ""),
            facts=d.get("facts", {}),
        )

    @classmethod
    def from_stage(cls, stage) -> "Node":
        """从 Workflow Stage 创建 Node。"""
        return cls(
            id=stage.id,
            goal=stage.description or stage.id,
            description=stage.description or "",
            executor_type=stage.execution.executor.value if hasattr(stage.execution, 'executor') else "react",
            execution_spec=stage.execution,
            dependencies=stage.depends or [],
            inputs=stage.inputs or [],
            outputs=stage.outputs or [],
            validators=stage.validators,
        )


@dataclass
class NodeGraph:
    """节点图（DAG 容器）。
    
    包含所有 Node 以及它们的依赖关系。
    """
    nodes: List[Node] = field(default_factory=list)

    def add(self, node: Node):
        self.nodes.append(node)

    def get(self, node_id: str) -> Optional[Node]:
        for n in self.nodes:
            if n.id == node_id:
                return n
            for child in n.children:
                if child.id == node_id:
                    return child
        return None

    def topological_sort(self) -> List[Node]:
        """拓扑排序。"""
        visited = set()
        result = []

        def dfs(node: Node, path: set):
            if node.id in visited:
                return
            if node.id in path:
                raise ValueError(f"Cycle detected: {node.id}")
            path.add(node.id)
            for dep_id in node.dependencies:
                dep = self.get(dep_id)
                if dep:
                    dfs(dep, path)
            path.remove(node.id)
            visited.add(node.id)
            result.append(node)

        for node in self.nodes:
            if node.id not in visited:
                dfs(node, set())

        return result

    def flatten(self) -> List[Node]:
        """展平所有节点（含 children）。"""
        flat = []

        def _flatten(nodes: List[Node]):
            for n in nodes:
                flat.append(n)
                if n.children:
                    _flatten(n.children)

        _flatten(self.nodes)
        return flat