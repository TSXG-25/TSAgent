"""executors — 并列执行器子包。

每个执行器实现统一契约 execute(target, context) -> ExecutionResult：
- tool:     ToolExecutor（确定性 ExecutionPlan 步骤序列）
- llm:      LLMExecutor（纯 LLM 推理，无工具调用）
- react:    ReactExecutor（ReAct 循环，开放式任务，Phase B.3 迁移）
- workflow: WorkflowExecutor（消费整个 Workflow，Phase B.4 迁移）

选择执行器只通过 ExecutorFactory（agent/executor/contract.py），
不在 orchestrator / pipeline 中写 if/elif。
"""
