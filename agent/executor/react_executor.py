"""ReactExecutor — 局部 ReAct Loop 执行器。

将现有的通用 Executor 封装为 Workflow Executor 的可插拔策略。
现有 Executor（executor.py）保留不变，ReactExecutor 作为适配器。
"""
import time
from typing import Any, Dict, List
from agent.workflow import ExecutionContext, Stage, Artifact, ToolPolicy
from agent.executor.executor_registry import BaseExecutor
from agent.executor.executors.react import ReactExecutor as ReActEngine
from agent.services.context_service import ContextService


class ReactExecutor(BaseExecutor):
    """局部 ReAct Loop 执行器。
    
    在 Workflow 的一个 stage 内运行 ReAct Loop。
    受 ToolPolicy 限制（只允许指定的工具）。
    """
    
    async def execute(self, stage: Stage, context: ExecutionContext, prompt: str) -> Dict[str, Any]:
        tool_policy = stage.execution.tool_policy
        max_iterations = tool_policy.max_calls if tool_policy else 5
        
        # 构建一个最小 task 供 ReAct 执行
        task = {
            "id": stage.id,
            "goal": prompt or stage.description or stage.id,
            "description": stage.description or "",
            "success_condition": "",
            "status": "pending",
            "observations": [],
            "facts": dict(context.facts),
            "recent_failures": list(context.failure_history),
        }
        
        # 注入上下文 Artifact 作为 Facts
        for inp in (stage.inputs or []):
            art = context.get_artifact(inp.type)
            if art:
                task["facts"][f"artifact_{inp.type}"] = art.content[:500]
        
        # 使用现有 ReAct 引擎执行
        engine = ReActEngine()
        
        # 重写 execute_action 以受 ToolPolicy 限制
        original_execute = engine._execute_action
        async def restricted_execute(caps, params, reason):
            # 检查工具是否被允许
            tool_name = caps[0] if caps else ""
            if tool_policy and not tool_policy.allows(tool_name):
                return {
                    "action": tool_name,
                    "status": "failed",
                    "summary": f"工具 {tool_name} 不被当前阶段允许。允许的工具: {tool_policy.allow}",
                    "artifact_ids": [],
                    "tool_used": tool_name,
                    "time_s": 0,
                }
            return await original_execute(task, caps, params, reason)
        
        engine._execute_action = restricted_execute.__get__(engine, ReActEngine)
        
        # 限制迭代次数
        old_max = 5
        from agent.executor.executors import react as exec_module
        original_max = exec_module.MAX_THINK_ITERATIONS
        exec_module.MAX_THINK_ITERATIONS = max_iterations
        
        try:
            # 运行 ReAct
            from agent.state import AgentState
            state: AgentState = {
                "messages": [],
                "plan": [],
                "current_task_index": 0,
                "artifacts": {},
                "memory_context": "",
                "repo_context": "",
                "intent": "",
                "skill_hint": "",
                "retries": 0,
                "workflow": None,
            }
            await engine._execute_task_react(state, task)
        finally:
            exec_module.MAX_THINK_ITERATIONS = original_max
        
        # 收集输出
        outputs = {}
        for out in (stage.outputs or []):
            # 从最后一条 observation 中提取结果
            obs = task.get("observations", [])
            last_content = ""
            if obs:
                last_obs = obs[-1]
                last_content = last_obs.get("summary", "")
            
            outputs[out.type] = Artifact(
                id=f"{stage.id}-react-{int(time.time())}",
                type=out.type,
                content=last_content,
                summary=last_content[:200],
                created_by=stage.id,
                timestamp=time.time(),
            )
        
        # 更新全局 Context
        context.facts.update(task.get("facts", {}))
        for obs in task.get("observations", []):
            context.record_action(obs)
        for f in task.get("recent_failures", []):
            context.record_failure(f)
        
        return outputs