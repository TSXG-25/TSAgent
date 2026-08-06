# ADR-0017: Workflow Resume Runtime（v2.2B）

- 状态: Accepted（v2.2B Scope）
- 日期: 2026-08
- 关联: ADR-0012（Execution Runtime）、ADR-0016（Run Checkpoint Contract）

## 一、决策摘要

v2.2B 将 v2.2A 的事实模型接入现有 `WorkflowExecutor`，建立单 Workflow 的
Checkpoint 创建、阶段进度记录和安全恢复闭环：

```text
WorkflowExecutor
    │ stage/task/result facts
    ▼
CheckpointRecorder → CheckpointStore
    │ latest RunCheckpoint
    ▼
ResumeValidator → ResumeDecision
    │ ALLOW: RESUME_EXACT / REPLAY_FROM_STAGE
    ▼
WorkflowExecutor（跳过已完成边界，继续 active stage）
```

Checkpoint 接线是可选的。没有提供 `WorkflowCheckpointRequest` 时，现有
`WorkflowExecutor` 行为保持不变。

## 二、v2.2B 范围

本版本只负责：

- 创建 `CREATED → RUNNING` 的初始 Checkpoint 链；
- 每个 Stage 完成后记录不可变进度快照；
- 在显式中断后生成 `SUSPENDED` Checkpoint；
- 在失败后生成 `FAILED_RECOVERABLE` 或 `WAITING_USER` Checkpoint；
- 完成时生成 `COMPLETED` Checkpoint；
- 通过 `ResumeValidator` 验证并消费 `RESUME_EXACT` / `REPLAY_FROM_STAGE`；
- 恢复时跳过已确认完成的 Stage，避免重复副作用；
- 提供内存 Store 作为运行时和集成测试的持久化边界。

以下内容不在 v2.2B：

- Planner 新能力或自动 Replan；
- 多 Run 寻址和跨 Workflow Resume；
- durable 数据库/远程 Checkpoint Store；
- `REPLAN_FROM_CHECKPOINT` 的实际 Planner 消费；
- `ABANDON_AND_RESTART` 的新 Run 创建流程。

## 三、边界与职责

### `CheckpointStore`

Store 只保存不可变 `RunCheckpoint`，并保证同一 Run 的 parent、sequence 和
latest 链连续。Store 不执行 Workflow，不调用 Validator。

### `CheckpointRecorder`

Recorder 将已发生的 Stage/Task/Artifact/Result 事实转换为 Checkpoint。它不
查询外部系统，也不选择恢复动作；外部事实必须先由调用方转换为
`ExternalStateGuard`。

### `WorkflowExecutor`

WorkflowExecutor 仍是唯一的 Workflow 编排入口。启用 Checkpoint 时：

- 在 Stage 边界调用 Recorder；
- 恢复前调用 `validate_resume`；
- 只消费 `ALLOW + RESUME_EXACT` 或 `ALLOW + REPLAY_FROM_STAGE`；
- `REQUIRE_CLARIFICATION` / `REJECT` 不进入任何 Executor；
- `REPLAN_FROM_CHECKPOINT` 和 `ABANDON_AND_RESTART` 在 v2.2B 返回显式未执行结果，
  不伪装成成功恢复。

## 四、Stage 进度与生命周期

Checkpoint 链允许在生命周期状态不变时追加事实快照。例如 Stage 完成后，
`RUNNING → RUNNING` 是新的 Checkpoint，不是非法生命周期迁移；只有状态发生
变化时才使用 ADR-0016 的 `ALLOWED_TRANSITIONS`。

典型链路：

```text
CREATED
  → RUNNING（开始）
  → RUNNING（Stage 1 完成）
  → SUSPENDED（显式中断）
  → RUNNING（恢复获准）
  → COMPLETED
```

失败链路：

```text
RUNNING
  → FAILED_RECOVERABLE（副作用明确未提交/可重试）
  → SUSPENDED
  → RUNNING（恢复获准）
```

副作用为 `UNKNOWN`、`STARTED` 或 `FAILED_AFTER_COMMIT` 时，Recorder 生成
`WAITING_USER`，恢复请求必须先得到新的外部事实，不能直接进入 Executor。

## 五、恢复规则

### `RESUME_EXACT`

使用 Checkpoint 的 `completed_stage_ids` 作为已验证边界，从
`active_stage_id` 继续执行。已完成 Stage 不得再次调用 Executor。

### `REPLAY_FROM_STAGE`

只有当 Validator 已确认 Stage 声明幂等且相关副作用状态安全时，才从
`active_stage_id` 重新执行；此前已完成 Stage 仍然跳过。

### ResumeDecision 是唯一入口

WorkflowExecutor 不读取 `proposed_next_action` 作为执行指令。每次恢复都用当前
Workflow/Plan 版本、Run 地址、权限和外部 Guard 重新生成 `ResumeDecision`。

## 六、验收场景

v2.2B 必须通过以下五组离线集成场景：

1. `exact_resume`：中断后恢复正确 Stage，已完成 Stage 不重复；
2. `stage_replay`：幂等 Stage 允许重放，非幂等 Stage 被拒绝；
3. `committed_side_effect`：已提交副作用阻止重复执行；
4. `unknown_side_effect`：返回 `REQUIRE_CLARIFICATION + WAITING_USER`，不进入 Executor；
5. `external_state_mismatch`：Guard 不匹配返回 `REJECT`，不执行任何 Stage。

指标至少包括：

```text
Checkpoint creation correctness       100%
Resume decision consumption            100%
Duplicate stage execution              0
Duplicate committed side effect        0
Unsafe resume acceptance               0
```

Workflow 端到端跨 Run 选择和真实外部服务恢复留到 v2.2C / 后续版本。
