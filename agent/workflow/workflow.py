# agent/workflow/workflow.py
"""Workflow — 工作流定义。

Workflow 是一组 Stage 的有向无环图（DAG）。
WorkflowExecutor 根据 Workflow 定义执行整个流程。
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from .stage import Stage


@dataclass
class Workflow:
    """工作流。
    
    一个 Workflow 包含多个 Stage，按 DAG 依赖关系执行。
    每个 Stage 有自己的 ExecutionSpec（怎么执行）和输入输出声明。
    
    Attributes:
        id: 工作流标识（如 "code_generation", "research"）
        stages: 所有 Stage 的列表（按 id 索引）
        description: 人类可读描述
        version: 版本号
        metadata: 额外信息（如默认温度、超时等）
    """
    id: str
    stages: List[Stage]
    description: str = ""
    version: str = "1.0"
    metadata: Optional[Dict] = None
    
    def _build_dag(self) -> Dict[str, List[str]]:
        """构建 DAG 依赖关系。
        
        Returns:
            {stage_id: [依赖的 stage_id]}
        """
        dag = {}
        stage_ids = [s.id for s in self.stages]
        
        for i, stage in enumerate(self.stages):
            if stage.depends:
                dag[stage.id] = stage.depends
            elif i > 0:
                # 缺省依赖前一个 stage
                dag[stage.id] = [stage_ids[i - 1]]
            else:
                dag[stage.id] = []
        
        return dag
    
    def get_stage(self, stage_id: str) -> Optional[Stage]:
        """按 ID 查找 Stage。"""
        for s in self.stages:
            if s.id == stage_id:
                return s
        return None
    
    def topological_sort(self) -> List[Stage]:
        """拓扑排序，返回执行顺序。"""
        dag = self._build_dag()
        visited = set()
        result = []
        
        def dfs(stage_id: str, path: set):
            if stage_id in visited:
                return
            if stage_id in path:
                raise ValueError(f"Cycle detected in workflow {self.id}: {stage_id}")
            
            path.add(stage_id)
            for dep in dag.get(stage_id, []):
                dfs(dep, path)
            path.remove(stage_id)
            
            visited.add(stage_id)
            stage = self.get_stage(stage_id)
            if stage:
                result.append(stage)
        
        for stage in self.stages:
            if stage.id not in visited:
                dfs(stage.id, set())
        
        return result