# Execution Lifecycle

## 阶段边界

当前 Runtime 的执行序列为：

    INIT
      -> PLAN
      -> EXECUTE
      -> NEXT_ACTION / OBSERVE / REPLAN / RECOVER
      -> NEXT_TASK
      -> FINISH 或 FAIL

PLAN 由 PlannerStage 生成或恢复当前状态，EXECUTE 由 ExecutionStage 消费。Runtime 在阶段前后发布 progress event，并在安全边界检查预算、cancellation 和 RunContext。

## Planner 到 Executor

静态路径把 canonical Task 编译为 ExecutionPlan，再由 ExecutorFactory 取得 ToolExecutor 或 LLMExecutor。ToolExecutor 交给 PlanExecutor 执行有序 steps，并把结果送入 ExecutionVerifier。动态路径由 NextActionSelector 选择 ask、answer 或工具，但工具动作仍回到相同的 ExecutorFactory。

ExecutionContext 携带当前 task、artifacts、facts、history、variables、workspace、artifact/event/cancellation 依赖。scoped workspace 是文件事实边界；相对路径不能从进程当前目录获得成功证据。

## 结果与继续

成功的 task 会追加 action/effect/artifact evidence，并根据是否仍有 pending task 进入 NEXT_ACTION 或 NEXT_TASK。普通 action failure 进入 OBSERVE；确定为只读且无效果的已知失败可再进入有界 REPLAN；结构故障进入 RECOVER。重规划仍在唯一 Runtime 循环内，并在新计划执行前重新检查授权。

## Finalizer 与 durable output

NEXT_TASK 调用 Finalizer，结合最佳答案、运行观察、artifact、output policy 和 freshness 要求形成 Runtime evidence。launcher 不仅保存文本，还保存 evidence 中的 verified artifact commit facts，并让 run head、terminal event、run output 和 artifact facts 原子收敛。

这意味着“Executor 返回 success”不等于“公共 Run COMPLETED”；后者需要更高层 terminal evidence 和 durable transition。

## 证据

agent/runtime.py、agent/orchestrator/planner.py、executor.py、finalizer.py、agent/executor/contract.py、executors/tool.py、plan_executor.py、verifier.py、agent/service/runtime_launcher.py。
# 2026-09-09 文件效果增补

ADR-0035：ToolExecutor 采集实际受管文件操作回执和声明命令输出的变化，ExecutionVerifier 转为规范路径/摘要。ExecutionStage 记录成功及失败任务的部分文件证据；Finalizer 撤销已移动/删除的旧路径并复核剩余摘要；Runtime 和 Service 消费同一文件证据投影。Service 发布前发现证据失效时改为失败，不能只丢弃 Artifact 却继续发布 COMPLETED。未声明命令输出和发布后文件变更不在此保证内。
