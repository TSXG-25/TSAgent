# ADR-0016: Run Checkpoint Contract（v2.2A）

- 状态: Accepted（v2.2A Scope 冻结）
- 日期: 2026-08
- 关联: ADR-0001（核心模型）、ADR-0009（确定性验证）、ADR-0012（Execution Runtime）、ADR-0013（Conversation Runtime）、ADR-0015（Session Isolation）

## 一、决策摘要

> **RunCheckpoint 是不可变的执行事实快照；ResumeValidator 基于快照和当前事实生成 ResumeDecision。**

v2.2A 只建立可恢复执行的事实模型、安全边界和纯验证器，不接入 Workflow 执行，
不重构 Planner，不新增 Resume Orchestrator。

```text
RunCheckpoint
    + current_context
    + external_state_evidence
    + compatibility_registry
            │
            ▼
    ResumeValidator（纯函数）
            │
            ▼
    ResumeDecision
```

Checkpoint 记录已经发生的事实；Decision 记录当前验证结论。旧 checkpoint 中的
`proposed_next_action` 只能作为历史建议，不能被恢复流程直接信任。

## 二、Disposition 与 Action 分离

### ResumeDisposition

```text
ALLOW
REQUIRE_CLARIFICATION
REJECT
```

### ResumeAction

```text
RESUME_EXACT
REPLAY_FROM_STAGE
REPLAN_FROM_CHECKPOINT
ABANDON_AND_RESTART
```

不变量：

```text
ALLOW                  → action 必须存在
REQUIRE_CLARIFICATION  → action 必须为空，resulting_status=WAITING_USER
REJECT                 → action 必须为空，历史 checkpoint 不被修改
```

`WAITING_USER` 是 Decision Outcome 对当前恢复请求的结果，不是第五种
`ResumeAction`。副作用未知、运行目标冲突、多个 Run 无法消歧、外部状态无法确认时，
必须返回 `REQUIRE_CLARIFICATION`，不得强行选择 `ABANDON_AND_RESTART`。

## 三、不可变 Checkpoint 链

Run 与 Checkpoint 分离：

```text
Run
 └── Checkpoint 0
      └── Checkpoint 1
           └── Checkpoint 2
```

每次状态变化都创建新快照，禁止原地修改：

```text
checkpoint_id
parent_checkpoint_id
sequence_number
created_at
updated_at
```

Run identity：

```text
run_id
workflow_id
workflow_version
```

Checkpoint identity：

```text
checkpoint_id
parent_checkpoint_id
sequence_number
checkpoint_schema_version
```

`RunCheckpoint` 只保存可序列化事实：canonical execution plan payload、artifact
引用、Verifier 状态、FailureEvent 快照、Side-effect 记录、External Guard 和
Runtime Evidence；不得保存 live tool handle、callable 或可变执行容器。

## 四、最小 Schema

```text
RunCheckpoint
├── Identity
│   ├── run_id / checkpoint_id / parent_checkpoint_id / sequence_number
│   ├── session_id / conversation_id / user_scope
│   └── supersedes_run_id
├── Version
│   ├── checkpoint_schema_version / contract_version
│   ├── workflow_id / workflow_version / plan_version
├── Execution Position
│   ├── active_stage_id / active_task_id
│   ├── completed_stage_ids / completed_task_ids
│   └── target_summary
├── Execution State
│   ├── status / execution_plan / artifacts
│   ├── verifier_status / failure_event
│   └── proposed_next_action
├── Safety
│   ├── task_effect_records / idempotency_keys
│   ├── external_state_guards / invalidation_reasons
└── Audit
    ├── created_at / updated_at / runtime_evidence
```

## 五、生命周期

冻结状态集：

```text
CREATED
RUNNING
SUSPENDED
WAITING_USER
FAILED_RECOVERABLE
FAILED_TERMINAL
COMPLETED
CANCELLED
```

合法迁移由 `agent.checkpoint.lifecycle.ALLOWED_TRANSITIONS` 唯一声明：

```text
CREATED            → RUNNING / CANCELLED
RUNNING            → SUSPENDED / WAITING_USER / FAILED_RECOVERABLE /
                     FAILED_TERMINAL / COMPLETED
SUSPENDED          → RUNNING / CANCELLED
WAITING_USER       → SUSPENDED / CANCELLED
FAILED_RECOVERABLE → SUSPENDED / CANCELLED
```

`COMPLETED`、`CANCELLED`、`FAILED_TERMINAL` 是终态。禁止复活终态 Run；重新处理
必须创建新 Run，并用 `supersedes_run_id` 建立关系。

## 六、ResumeAction 安全规则

### RESUME_EXACT

只有 Workflow、Plan、Contract 版本兼容，执行位置存在，且副作用状态明确时允许。
已 `COMMITTED` 的任务只能从后续边界继续，不得重复执行。

### REPLAY_FROM_STAGE

必须同时满足：

1. Stage 显式声明幂等；
2. 相关副作用状态不是 `COMMITTED`、`STARTED`、`FAILED_AFTER_COMMIT` 或 `UNKNOWN`；
3. Stage / Workflow / Checkpoint 版本兼容。

仅有 idempotency key 不足以证明 Stage 可安全重放。

### REPLAN_FROM_CHECKPOINT

保留已验证成果和失败上下文，向未来 Planner 暴露标准输入：

```text
Checkpoint
+ unresolved_goal
+ verified_artifacts
+ failure_context
→ new ExecutionPlan
```

v2.2A 只定义输入和 Decision，不实现新的 Planner 能力。

### ABANDON_AND_RESTART

必须是显式的恢复动作。它不是 Validator 对不确定状态的默认兜底，也不代表旧
副作用已经安全完成。

## 七、Side-effect 状态

```text
NONE
NOT_STARTED
STARTED
COMMITTED
FAILED_BEFORE_COMMIT
FAILED_AFTER_COMMIT
COMPENSATED
UNKNOWN
```

安全含义：

| 状态 | Exact Resume | Replay Stage |
|---|---|---|
| `NONE` / `NOT_STARTED` | 允许 | Stage 幂等时允许 |
| `FAILED_BEFORE_COMMIT` | 允许重试 | Stage 幂等时允许 |
| `COMMITTED` | 从后续边界继续 | 禁止重复 Stage |
| `COMPENSATED` | 依 Workflow 规则 | 可重新执行 |
| `STARTED` | 要求澄清 | 禁止 |
| `FAILED_AFTER_COMMIT` | 要求澄清 | 禁止 |
| `UNKNOWN` | 要求澄清 | 禁止 |

## 八、版本兼容

- Checkpoint schema：同 major 才允许 codec 尝试读取；不同 major 直接 Reject。
- Contract version：major 不兼容直接 Reject。
- Workflow version：完全相同才允许 Exact/Replay；版本不同必须存在显式
  migration mapping，且最多允许 Replan。
- Plan version：不同版本禁止 Exact/Replay，只允许 Replan。
- 不得根据相同的 `stage_id` 名称推断兼容。

## 九、ExternalStateGuard 与纯验证器

Validator 不查询外部世界。调用方先把事实转换成：

```text
ExternalStateGuard
├── resource_id
├── guard_type
├── expected_value
├── observed_value
├── checked_at
└── status: VERIFIED / UNKNOWN / MISMATCH / MISSING
```

Validator 只比较结构化值：

```python
validate_resume(
    checkpoint,
    current_context,
    external_state_evidence,
    compatibility_registry,
) -> ResumeDecision
```

## 十、pending_target 投影

Conversation Runtime 继续使用轻量投影，但它不是执行真相：

```text
RunCheckpoint → project_pending_target() → PendingTarget
```

投影最多包含：`run_id`、`workflow_id`、`target_summary`、当前 Stage 摘要、状态和
更新时间。禁止提供 `PendingTarget → RunCheckpoint` builder；不得用
`pending_target` 重建完整 Run。

## 十一、v2.2A Scope 与后续边界

### v2.2A

- Checkpoint immutable schema
- JSON codec 与 digest
- 生命周期验证
- 版本兼容评估
- Side-effect safety
- Deterministic ResumeValidator
- 单向 Conversation projection
- Dataset / Oracle / Fixture Validation

### v2.2B

- Workflow Runtime 创建真实 Checkpoint
- 只读恢复寻址
- 安全执行恢复
- Artifact / Verifier / FailureEvent 接线

### v2.2C

- 多 Run 寻址
- Cross-workflow continuation
- End-to-end resume benchmark

v2.2A 不宣称 Workflow Resume 已实现；它只证明 Checkpoint schema、纯验证器和
评测 Oracle 正确。
