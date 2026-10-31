# Architecture Invariants

本文件只列当前实现或针对性测试能支持的约束。每项同时给出证据和边界，避免把目标设计写成既成事实。

| 不变量 | 当前证据 | 状态 |
| --- | --- | --- |
| Service 主路径由 AgentService 管理身份、生命周期和 durable transition | agent/service/service.py、runtime_launcher.py、Service E2E tests | VERIFIED |
| Runtime 是唯一主循环；Planner 不拥有第二个隐式 recovery loop | agent/runtime.py、tests/test_h8_single_runtime_spine.py | VERIFIED |
| 普通 Action failure 不调用 Reflection/Decision；仅已知无效果的只读失败可进入有界 Planner.replan | agent/orchestrator/main.py、agent/runtime.py、tests/test_h8_single_runtime_spine.py、tests/test_p2_lh3_replan_preservation.py | VERIFIED |
| Structural failure 经 FailurePolicy 到 Reflection/Decision，再由 directive 恢复 | agent/failure/policy.py、agent/reflection、agent/decision、H8 tests | VERIFIED |
| Task 必须经过 Compiler/ExecutionPlan/ExecutorFactory | agent/orchestrator/executor.py、agent/executor/contract.py、execution tests | VERIFIED |
| Tool 成功不能绕过 ExecutionVerifier 直接成为 verified effect | agent/executor/executors/tool.py、verifier.py | VERIFIED |
| 文件 effect 必须在 scoped workspace 中检查 | agent/executor/verifier.py、workspace tests | VERIFIED |
| AgentState 是 cache，durable facts 在 Store/Event/Checkpoint | agent/state.py、runtime_store、checkpoint modules | VERIFIED（职责） |
| Service 原始对话按 tenant/session 隔离，获准 learned facts 按 tenant/user 共享 | agent/service/context_factory.py、agent/services/memory_service.py、tests/test_runtime_context_isolation.py | VERIFIED |
| cancel intent 不等于执行已停止 | agent/interruption、service、runtime_launcher | VERIFIED |
| Memory proposal 不等于 durable commit | memory service、learning provider、persistence tests | VERIFIED |
| Checkpoint resume 只在显式 checkpoint_request 下启用 | agent/executor/executors/workflow.py、checkpoint tests | VERIFIED |
| AgentService.resume_run 已接入 RunResumeCoordinator | 静态 agent 生产调用搜索未找到 | 未证明，不得宣称 |

## 解释边界

ADR-0035 的文件效果保证由 tests/test_ha_r3_file_evidence.py 覆盖：实际路径/摘要贯穿 Executor、Finalizer、Runtime 与 Service；文件生产者的 Task 失败不能自动抹除已验证文件；声明范围内没有真实变化的命令不得交付现成文件；发布前证据失效不能维持 COMPLETED。此保证不覆盖任意未声明写入、并发外部写入归属或发布后文件不可变性。

ADR-0034 的新增离线证据见 tests/test_ha_r2_goal_pipeline.py：代码执行不能丢失语义文件交付要求；保留约束不能变成删除任务；本轮依赖输出应进入解释任务；文件路径与所要求的终端结果应同时交付；总结阶段的 ProviderError 必须保留类别与已完成步骤。Summary archive provenance 和多 workspace repository ownership 已有离线结构回归；仍不覆盖真实模型对历史原话的指令混淆、跨进程项目缓存或真实入口场景验收。

“VERIFIED（组件）”不等于“VERIFIED（公共生产路径）”。当前 AgentService resume 已从持久化 Run index 进入 RunResumeCoordinator / WorkflowExecutor，并有 Service 重启离线 E2E；这不覆盖真实 Provider、跨进程并发 worker 仲裁、Direct API/Service 等价性或 Desktop local host。相同原则适用于 Planner capability 和 real-provider outcome。

## 维护规则

新增架构主张时，至少附一个当前生产入口或针对性测试。若只有 ADR/README，应标为 DOCUMENTED_ONLY；若只有实现和历史测试、没有当前入口，应标为 LEGACY_OR_UNUSED。
