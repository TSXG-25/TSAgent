# Runtime Contract

## 结论

Runtime 是单次请求的状态机和编排边界；它消费上下文与 Planner/Executor/Finalizer 的结果，不能把进程内 cache 当作 durable 事实源。

## 所有权

| 对象 | 所有者 | 作用 |
| --- | --- | --- |
| ApplicationContext | 应用/服务进程 | 配置、workspace 根、SQLite store、session 集合 |
| SessionContext | 会话 | tenant/user/session 身份、ConversationTracker、ScopedMemoryView、逻辑 run |
| RunContext | 单次运行 | writer/fence、DurableRuntimeStoreView、CancellationView、workspace、artifact、event |
| AgentState | UniversalAgent | Runtime 内部 cache 和阶段投影 |
| ExecutionContext | Task/Workflow 执行 | artifacts、facts、history、variables、budget 的可变执行容器 |
| SQLite runtime store | DurableRuntimeStore | run head、revision、事件、output、artifact、effect 和终态事实 |

RunContext.close 释放进程内资源，但不删除 durable run。AgentState 的 plan、execution_plans、current index、failure/recovery 等字段是可重建投影；跨进程恢复必须读取 Store、workspace 和显式 checkpoint。

## 入口

主入口是 AgentService.start_run。ServiceContextFactory 创建 SessionContext/RunContext，RuntimeExecutionLauncher 再调用 UniversalAgent.run。agent/api.py 的 TSAgent 和 SessionRuntime 是直接进程内入口，除非显式配置相同 store，否则不共享 AgentService 生命周期。

AgentService 的公开 DTO 带 tenant_id、user_id、session_id、request_id。查询和操作必须在 tenant/run 身份边界内完成；内部 Task、Planner state、Checkpoint 和 SQLite row 不直接暴露给客户端。

## 生命周期不变量

1. Runtime 在 PLAN、EXECUTE、OBSERVE、RECOVER 等安全边界检查预算和取消。
2. Planner 产生 Task，Executor 消费编译后的 ExecutionPlan；Runtime 不直接执行工具。
3. 终态由 Runtime evidence、output/effect/artifact verification 和 durable transition 共同收敛。
4. cancel 请求表示 durable intent，不等于执行已经停止。
5. Service resume 的 takeover/re-launch 与 Workflow checkpoint resume 是不同契约。

## 证据与限制

生产路径证据位于 agent/runtime.py、agent/runtime_context.py、agent/service/service.py、agent/service/runtime_launcher.py。tests/test_h8_single_runtime_spine.py 验证单一 Runtime spine；Service、store 和 cancellation 测试覆盖对应 durable 生命周期。

当前未验证的是：直接 TSAgent/SessionRuntime 使用者是否遵守与 Service 相同的 durable 运行约束，以及公共 Service resume 是否按 checkpoint 精确恢复。后者详见 checkpoint-resume.md。
