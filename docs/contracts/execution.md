# Execution Contract

## 唯一执行路径

静态任务的 canonical lowering 是：

    Task -> Compiler -> ExecutionPlan -> ExecutorFactory.get
         -> ToolExecutor 或 LLMExecutor
         -> PlanExecutor / registry
         -> ExecutionVerifier
         -> ActionResult、effect evidence、artifact evidence

ExecutionStage 负责 ownership、authorization、取消边界、结果投影和下一状态；它不重新解析用户输入，也不创建第二个 Planner。

## 两种 owner

- compiled owner：使用 Planner/Compiler 提供的 ExecutionPlan，通常执行第一个 pending task，result-driven 模式下根据结果继续。
- dynamic owner：使用 NextActionSelector 从当前 task、facts、history 和 available actions 中选择 ask、answer 或工具；工具动作最终仍进入 ExecutorFactory。

两者共享 ExecutionContext、scoped workspace、ArtifactStore、EventBus、CancellationView、run results 和 conversation projection。

ADR-0034：ExecutionStage 还按 Task.dependencies 从本轮 inbox 投影 dependency_results。LLMExecutor 不能用历史 Run 文案代替当前依赖结果；当前任务要求放在历史与工具数据之后。文件路径交付不能丢弃终端读取/解释任务的输出。

## Executor 与 verifier

ExecutorFactory 是按 executor type 注册和获取实现的单一路径。ToolExecutor 调用 PlanExecutor，PlanExecutor 执行有序 steps、变量替换、tool call、结果捕获和 cancellation。LLMExecutor 只负责 LLM 调用，不直接访问 tool registry。

ExecutionVerifier 对文件和 registry effect 检查 scoped workspace 中的实际结果；对不产生受管副作用的 read/explain/search 等操作只返回相应的验证状态。ToolExecutor 只有在 executor 成功并通过 verifier 后才生成 verified ActionResult。普通 LLM 文本不能自动升级为 verified file/effect。

## 成功、失败与输出

ADR-0035：Task.output_paths 声明命令预期产物，必须符合请求授权。ToolExecutor 对声明范围观察执行前后变化，ExecutionVerifier 发布实际规范路径与内容摘要。verified_files/consumed_files 来自执行效果，不由 Task.target 推测；失败任务也可以携带已验证部分文件。ExecutionStage 不再对猜测目标执行第二轮文件校验。

Execution success 只表示当前执行结果通过其 verifier；Run COMPLETED 还需要 Runtime evidence、terminal output、freshness、预算和 artifact 条件。文件写入必须能在当前 Run workspace 内解析并验证。

普通 Action failure 由 ExecutionStage observe_failure 进行有限 retry 或终止。结构性失败转入 Recovery Contract。provider/LLM exception 会形成 failure evidence，不隐式重跑另一路。

## 证据

主要实现：agent/orchestrator/executor.py、agent/executor/contract.py、agent/executor/executors/tool.py、agent/executor/llm_executor.py、agent/executor/plan_executor.py、agent/executor/verifier.py。相关边界由 execution、runtime 和 workspace 测试覆盖。
