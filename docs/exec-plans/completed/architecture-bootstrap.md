# Architecture Bootstrap Audit

## Goal

建立一套基于当前实现证据的 TSAgent 架构、契约、生命周期和质量文档，明确“实现存在”和“公共生产路径已接入”的区别。

## Non-goals

- 不修改 Agent Runtime、Planner、Executor、Workflow、Memory、Service 或测试实现。
- 不把 real-provider、真实桌面 host、现场用户流程当作本次离线验证范围。
- 不覆盖仓库现有 AGENTS.md 或用户已有工作区改动。

## Verified state

- CLI/local sidecar 的公共入口是 AgentService；RuntimeExecutionLauncher 负责 Runtime 与 SQLite durable facts 的收敛。
- TSAgent/SessionRuntime 是直接的进程内入口，可能绕过 AgentService。
- UniversalAgent 使用单一 Runtime 状态机；当前没有生产 replan 调用。
- 普通 Action failure 与 structural failure 分流；structural path 通过 FailurePolicy 到 Reflection/Decision。
- Execution 走 Task、Compiler、ExecutionPlan、ExecutorFactory、Verifier 和 effect/output evidence。
- Workflow checkpoint 与 RunResumeCoordinator 已实现并有针对性测试，但没有找到它们从 AgentService.resume_run 接入主路径的调用。
- Memory 写入经过 provider proposal、deterministic policy 和 persistence boundary；Service memory namespace 与 session scope 存在已确认债务。

## Invariants

1. 文档中的生产路径必须能指向当前 source/test evidence。
2. checkpoint resume 与 Service re-launch 必须分开描述。
3. ordinary action failure 不得描述为会隐式触发第二个 Planner。
4. Memory proposal 不得被描述为 durable commit。
5. Runtime cache、ExecutionContext、durable store 和 checkpoint 的所有权必须分开。

## Work paths

- docs/architecture.md
- docs/contracts/
- docs/runtime/
- docs/quality/

## Milestones

- [x] 读取 AGENTS.md、README、现有 architecture/ADR/test/evaluation 文档。
- [x] 重建 Service、Runtime、Planner、Execution、Failure、Workflow、Resume、Memory 和 Desktop 的当前调用链。
- [x] 对 Reflection/Decision 和 checkpoint/resume 做静态生产调用搜索及针对性测试核对。
- [x] 写入 architecture、contracts、runtime、quality 文档。
- [x] 运行基础离线检查并记录精确命令。
- [x] 将本计划移动到 docs/exec-plans/completed/。

## Acceptance

- 所列文档均有具体当前实现、证据文件或明确未验证边界。
- architecture.md 不把未接入的 checkpoint resume 写成 Service 主路径事实。
- quality/known-debt.md 列出可复核的差异，而不是泛化的未来改进。
- 不产生生产代码或用户既有工作区文件的非文档改动。

## Verification

计划完成时运行：

    rg -n "RunResumeCoordinator|checkpoint_request|replan" agent tests
    python3 -m pytest -q tests/test_h8_single_runtime_spine.py tests/test_run_resume_runtime.py
    python3 -m pytest -q tests/test_architecture_verification.py tests/test_v23b_architecture_gate.py

本次实际验证：上述静态搜索完成；相关离线测试共 58 passed in 3.63s。

真实 Provider、真实 Tauri host 和线上部署不在本次验证范围。

## Risks and decisions

- 风险：工作区已有生产代码、测试和报告的 dirty changes。本次只新增/修改 docs，交付前复核 git status。
- 决定：保留现有 AGENTS.md；用户提供的第一份文本作为本次工作约束，不直接覆盖仓库文件。
- 决定：对 ADR/README 与当前调用链冲突的地方记录差异，不在审计任务中修改生产契约。

## Follow-up

由维护者决定 resume 的正式语义。若要求精确 checkpoint 恢复，应补充 AgentService -> RunResumeCoordinator 的生产接线、主路径验收和新的 ADR；若不要求，应把现有文档降级为 re-launch 语义。
