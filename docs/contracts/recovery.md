# Recovery Contract

## 失败分类

当前恢复决策至少区分：

- ordinary Action failure：工具或执行步骤失败，但 Runtime 结构仍可观察。
- structural failure：状态、计划、权限、依赖或执行结构不能按普通结果继续。
- provider/runtime exception：Provider、LLM 或未预期 Runtime 错误。
- interruption：用户取消、timeout、stage/tool/provider timeout 或 service shutdown。

这些类别的恢复权不同，不能统一成“再试一次”。

## Policy seam

structural failure 进入单一 FailurePolicy.resolve：

    FailureEvent
      -> Reflection.reflect
      -> Decision.decide
      -> RecoveryDirective

Reflection 是确定性诊断/根因投影，Decision 是确定性矩阵，不直接执行副作用。RecoveryDirective 携带 action、reason、diagnosis、correction、confidence、event/decision id，再由 Runtime/Orchestrator 执行。

Reflection 和 Decision 是生产可达的：Runtime 的 structural recovery 经过 FailurePolicy；tests/test_h8_single_runtime_spine.py 直接验证 structural path。ordinary Action failure 不调用 Reflection/Decision；已知无效果的只读失败可请求 Runtime 在同一状态机内有界重规划。

## 执行规则

- observe：ExecutionOrchestrator.observe_failure 对 retryable action 做一次有限 retry；只读任务遇到 `FILE_NOT_FOUND`、`NO_SEARCH_MATCH`、`HTTP_404` 或 `INVALID_TOOL_ARGUMENT` 时进入 REPLAN，其它不可恢复失败收敛为 FAILED。
- retry：structural recovery 将已有 failed task 重新置 pending。
- switch：选择已有 pending task。
- ask/unknown：进入可见终态或等待用户，不猜测新的计划。
- replan：Runtime 调用 Planner.replan 前后检查取消边界；共享 recovery 预算限制次数。新任务按原目标契约和授权验证；空计划或重复计划以 `REPLAN_NO_PROGRESS` 终止。已完成任务保留，替代计划覆盖旧的未完成计划。结构恢复本身仍不调用 Planner。

Recovery budget、goal/run budget、action retry count 和 cancellation safe boundary 共同限制循环。

## Exception 与 interruption

ADR-0034：LLMRouter 的 ProviderError 经 _recover 保留 PROVIDER_* 分类；其他 Runtime exception 产生 RUNTIME_EXCEPTION failure evidence。失败结果披露已完成步骤，不隐藏异常后再走旧路径。CancellationCoordinator 记录 intent；CancellationView 在安全边界抛出 RunInterruptionRequested，launcher 再将观察到的结果收敛到 durable terminal state。

## 证据

主要实现：agent/failure/policy.py、agent/reflection/reflector.py、agent/decision/decision.py、agent/runtime.py、agent/orchestrator/executor.py、agent/orchestrator/main.py、agent/interruption。Runtime 接线、安全失败分类、授权拒绝、效果保留和重复计划停止均由离线回归测试支撑。
