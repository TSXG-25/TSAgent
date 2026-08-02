# agent/workflow/executor_type.py
"""ExecutorType — 执行器类型枚举。

定义每种 Stage 的执行方式。
WorkflowExecutor 根据 ExecutorType 选择对应的执行器。
"""
from enum import Enum


class ExecutorType(str, Enum):
    """执行器类型。
    
    LLM: 纯 LLM 推理，不调用任何工具
    TOOL: 单次工具调用
    REACT: 局部 ReAct Loop（验证→修复循环）
    PIPELINE: 子工作流（嵌套执行另一个 Workflow）
    
    # 未来扩展
    # PARALLEL = "parallel"     # 并行执行多个 stage
    # MAP_REDUCE = "map_reduce"  # 分片处理
    # BATCH = "batch"            # 批量 LLM 调用
    # CONDITIONAL = "conditional" # 条件分支
    """
    LLM = "llm"
    TOOL = "tool"
    REACT = "react"
    PIPELINE = "pipeline"