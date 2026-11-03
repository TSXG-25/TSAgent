# Workflow Contract

## 模型

Workflow 是带版本的 Stage DAG。Stage 是带 ExecutionSpec、输入/输出声明、validator、retry/idempotency 策略的 Task 模板；topological_sort 在执行前检查依赖和环。

agent/workflow/context.py 的 ExecutionContext 保存 artifacts、facts、action_history、failure_history、variables、messages 和 budget。Artifact 支持按 type 查找和 parent lineage；context 不是跨进程 durable store。

## 普通执行

WorkflowExecutor 的基本过程是：

    topological stages
      -> Stage.to_task
      -> resolve inputs/artifacts
      -> Compiler
      -> ExecutorFactory
      -> validator
      -> next Stage / terminal output validation

已存在的 workflow 示例包括 workflows/code_generation.py。PlannerStage 的专用 route 可以直接调用 WorkflowExecutor.execute；默认 checkpoint_request 为 None，所以该 route 不记录 RunCheckpoint。

## Checkpoint 可选路径

只有调用者传入 WorkflowCheckpointRequest 时，WorkflowExecutor 才会创建或恢复 CheckpointRecorder，在 Stage 边界记录 checkpoint，hydrate artifacts，调用 validate_resume，并消费 RESUME_EXACT 或 REPLAY_FROM_STAGE。错误的 artifact digest、缺少 ResumeContext、依赖缺失或未支持的 ResumeAction 会产生可见的 blocked result，不进入 Executor。

这是一条明确的可选执行模式，不是 WorkflowExecutor 对所有调用的隐式行为。

## 生产集成结论

WorkflowExecutor 的 checkpoint 分支和 RunResumeCoordinator 组件有测试证据，tests/test_workflow_checkpoint_runtime.py 和 tests/test_run_resume_runtime.py 覆盖其行为。但当前在 agent 生产代码中没有找到 AgentService、UniversalAgent、CLI 或 sidecar 调用 RunResumeCoordinator 或构造 WorkflowCheckpointRequest 的路径。故 Workflow checkpoint 是 VERIFIED 的组件能力，主 Service resume 集成是未证明的。

## 证据

主要实现：agent/workflow/workflow.py、stage.py、context.py、agent/executor/executors/workflow.py、agent/run_resume/coordinator.py。工作流的 side effect reconciliation 依赖 scoped workspace 和显式 artifact/effect facts。
