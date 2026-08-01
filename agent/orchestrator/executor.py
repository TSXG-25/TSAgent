"""ExecutionStage — EXECUTE 阶段编排。

Compiler 已决定执行器（plan.executor），通过 ExecutorFactory 分发：
- plan.executor == "tool" → ToolExecutor（确定性 ExecutionPlan 步骤序列）
- 其他（"llm"）→ ReactExecutor（开放式任务，Phase B.3 迁移完成前保持现状）

Phase C.1：从 orchestrator.py 的 execute() 迁移。
"""
import time
from typing import Tuple

from agent.state import AgentState
from agent.executor.executors.react import ReactExecutor
from agent.executor.contract import executor_factory
from agent.workflow import ExecutionContext
from agent.services.workspace_service import get_workspace_service


class ExecutionStage:
    """EXECUTE 阶段执行器。

    持有对 ExecutionOrchestrator 容器的反向引用（访问 _timings）。
    """

    def __init__(self, orchestrator):
        self._orch = orchestrator

    async def run(self, state: AgentState) -> Tuple[AgentState, str]:
        """EXECUTE 阶段：通过 ExecutorFactory 分发执行。

        对于每个 Task：
        - Compiler 编译的 plan.executor == "tool" → ToolExecutor
        - 其他 → ReactExecutor（开放式 ReAct）
        """
        t_exec = time.perf_counter()
        tasks = state.get("plan", [])
        execution_plans = state.get("execution_plans", [])

        if not execution_plans:
            # 无 ExecutionPlan（旧 Workflow 路径），走老 ReAct
            executor = ReactExecutor()
            state = await executor.execute(state, tasks)
        else:
            # 新路径：Compiler 已决定执行器（plan.executor），ExecutorFactory 分发
            for idx, task_dict in enumerate(tasks):
                task_obj = self._orch._planner._dict_to_task(task_dict) if idx < len(tasks) else None
                plan = execution_plans[idx] if idx < len(execution_plans) else None

                if plan is not None and plan.executor == "tool":
                    # ToolExecutor（确定性 ExecutionPlan 步骤序列）
                    print(f"  🔀 Compiler: {task_dict.get('id', '?')} → tool_executor")
                    ws_service = None
                    try:
                        ws_service = get_workspace_service()
                    except Exception:
                        pass
                    context = ExecutionContext(task=task_obj, variables={})
                    if ws_service:
                        context.set_var("workspace", ws_service)
                    context.set_var("execution_plan", plan)

                    tool_executor = executor_factory.get("tool")
                    exec_result = await tool_executor.execute(task_obj, context)

                    if not exec_result.success:
                        task_dict["status"] = "failed"
                        task_dict["error"] = exec_result.error
                    else:
                        task_dict["status"] = "succeeded"
                        exec_meta = exec_result.metadata or {}
                        task_dict["observations"].append({
                            "action": "tool_executor",
                            "tool": "tool_executor",
                            "status": "succeeded",
                            "summary": exec_result.text[:300],
                            "artifact_ids": [],
                            "time_s": round(exec_meta.get("time_s", 0), 2),
                        })
                        # 保存变量供后续任务使用
                        task_dict["facts"] = exec_meta.get("variables", {})
                else:
                    # ReactExecutor 执行开放式任务（Phase B.3 迁移到统一契约后接入 factory）
                    print(f"  🔀 Compiler: {task_dict.get('id', '?')} → react_executor")
                    executor = ReactExecutor()
                    sub_state = await executor.execute(state, [task_dict])
                    # 合并回主 state
                    updated = sub_state.get("plan", [])
                    if updated:
                        task_dict["status"] = updated[0].get("status", "failed")
                        task_dict["observations"] = updated[0].get("observations", [])
                        task_dict["error"] = updated[0].get("error", "")
                        task_dict["facts"] = updated[0].get("facts", {})

        self._orch._timings["executor"] = round(time.perf_counter() - t_exec, 3)

        # 检查结果
        failed = [t for t in state.get("plan", []) if t.get("status") == "failed"]
        if not failed:
            return state, "NEXT_TASK"
        return state, "RECOVER"
