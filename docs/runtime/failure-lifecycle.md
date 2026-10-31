# Failure Lifecycle

## 普通 Action failure

工具或执行步骤返回失败时，ExecutionStage 先记录 failure/effect observation。若任务为只读且错误属于 `FILE_NOT_FOUND`、`NO_SEARCH_MATCH`、`HTTP_404` 或 `INVALID_TOOL_ARGUMENT`，observe_failure 把控制权交给 Runtime 的 REPLAN 状态，由共享 recovery budget 限制 Planner 生成修订计划；新计划按原 goal contract 和授权范围验证，空计划或重复计划终止。其余 retryable action 最多复用当前 execution plan 重试一次；写入、执行与效果未知失败不会自动重放。普通 action 失败不调用 Reflection/Decision。

## Structural failure

状态损坏、计划结构、权限、依赖或其他 structural evidence 进入：

    FailurePolicy.resolve
      -> Reflection.reflect
      -> Decision.decide
      -> RecoveryDirective
      -> Runtime / ExecutionOrchestrator

Runtime/Orchestrator 只执行 directive 允许的 bounded action：retry 已有 task、switch 到已有 pending task、ask，或终止。Recovery budget 和 goal/run budget 限制重复恢复。

## Runtime exception

ADR-0034：LLMRouter 的 ProviderError 保留具体 PROVIDER_* 类别和 cause；即使发生在结果总结阶段，也要沿 Runtime 到 Service 保留类别。其他未预期异常仍由 Runtime 的 _recover 变成 RUNTIME_EXCEPTION evidence。失败输出披露已执行步骤与已验证的部分文件，不声称本次什么也没做。Runtime exception 不静默切换旧实现或重调 Planner；只有上节限定的只读 action failure 可触发有界 replan。launcher 将未收敛的异常记为 FAILED，并写入 secret-free failure payload。

## Interruption

cancel_run 和 timeout 先写入 CancellationIntent。CancellationView 在 LLM/provider 可中断等待或安全边界观察请求，抛出 RunInterruptionRequested；launcher 的 convergence 再把结果收敛到 CANCELLED、TIMED_OUT 或其他明确终态。请求取消不等于客户端已经观察到停止。

## 可观察结果

每类 failure 都应保留 failure classification、reason、diagnostics、recovery decision 或 interruption reason，以及 terminal output 是否 verified。公共 snapshot 和 terminal event 必须来自同一 durable transition。

## 证据

agent/failure/policy.py、agent/reflection/reflector.py、agent/decision/decision.py、agent/runtime.py、agent/orchestrator/executor.py、main.py、agent/interruption、agent/service/runtime_launcher.py、tests/test_h8_single_runtime_spine.py。
