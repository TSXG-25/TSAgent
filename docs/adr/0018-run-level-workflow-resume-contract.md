# ADR-0018: Run-Level Workflow Resume Contract（v2.2C）

- 状态: Accepted — v2.2C Implemented and P0 Verified
- 日期: 2026-08
- 关联: ADR-0016（Run Checkpoint Contract）、ADR-0017（Workflow Resume Runtime）

## 一、决策摘要

v2.2C 将可恢复执行从单 Workflow 提升到 Run 级协调：一个 Run 可以按固定顺序
包含多个 Workflow，恢复入口先定位 Run 的 active Workflow，再把该 Workflow 的
Stage/Task 恢复交给 v2.2B 的 `RunCheckpoint` 和 `ResumeValidator`。

```text
RunResumeIndex
    ├── completed Workflow IDs
    ├── active Workflow ID + active checkpoint ID
    ├── pending Workflow IDs
    └── Workflow dependencies / artifact summaries
                         │
                         ▼
             Run-Level ResumeDecision
                         │
                         ▼
       v2.2B WorkflowExecutor + ResumeValidator
```

Run-level index 是协调事实，不是 Stage/Task 的第二份 Source of Truth。

## 二、范围

### 纳入 v2.2C

- 从 `run_id` 定位一个 Run 的恢复索引；
- 按固定 `workflow_sequence` 识别已完成、当前 active 和尚未开始的 Workflow；
- 跨 Workflow 传递已验证的 Artifact 摘要与依赖关系；
- 跳过已完成 Workflow，只向 active Workflow 委派恢复动作；
- 检查 Run、Workflow、Checkpoint、上游 Artifact 和当前版本的一致性；
- 证明进程重启后可从 Store 重建同一个 Run 级恢复事实；
- 将不安全状态显式转换为 `REQUIRE_CLARIFICATION` 或 `REJECT`；
- 建立确定性 Dataset / Oracle，并为后续 Orchestrator 接线保留验收边界。

### 明确不纳入 v2.2C

- Planner 重新规划或语义级 Plan 修复；
- Human Approval；
- Budget、Timeout、Cancellation；
- 并发、分布式或跨机器 Workflow；
- 任意 DAG 调度；本 ADR 只支持 Run 内固定顺序和向前依赖；
- Provider Failover；
- 大规模 Checkpoint Schema Migration；
- 将 Stage/Task 状态复制到 Run index；
- 在 Dataset / Oracle 阶段修改 Orchestrator 或声称 E2E Resume 已完成。

## 三、RunResumeIndex 最小合同

```text
RunResumeIndex
├── run_id
├── workflow_sequence
├── completed_workflow_ids
├── active_workflow_id
├── active_checkpoint_id
├── pending_workflow_ids
├── workflow_dependencies
├── artifact summaries
└── store_generation
```

每个 Workflow 摘要至少包含：

```text
workflow_id
workflow_version
status
checkpoint_id
depends_on
required_artifacts
active_side_effect_state
active_stage_idempotent
verifier_status
```

其中 `checkpoint_id` 只引用 v2.2B 的最新 `RunCheckpoint`，不内嵌 Stage/Task
状态。Run index 必须满足以下不变量：

1. `workflow_sequence` 中 Workflow ID 唯一，且与 Workflow 摘要一一对应；
2. `completed + active + pending` 恰好覆盖整个 sequence，三者互不重叠；
3. `active_checkpoint_id` 通常必须引用当前 activation attempt 的最新合法
   `RunCheckpoint`。仅在 `pending → active` 已原子提交、且能够确定 Executor 尚未开始的
   短暂状态中允许为空。一旦当前 attempt 已产生 Checkpoint，Resolver 必须绑定或回退到
   该 attempt 的最新合法 Checkpoint；不得因 index 中引用为空而无条件从零执行。每个
   Workflow 在 CheckpointStore 中维护独立的 `(run_id, workflow_id)` 追加链，不允许跨
   Workflow 建立 parent 关系；
4. dependency 只能引用 sequence 中更早的 Workflow；
5. Artifact producer 必须属于同一个 Run；
6. 已完成 Workflow 的副作用只作为历史事实，不得被恢复入口重新选择。

## 四、恢复决策边界

Run 级 Decision 只负责选择 Workflow，不替代 v2.2B 的 Stage 级 Decision：

```text
RunResumeDecision
├── disposition: ALLOW / REQUIRE_CLARIFICATION / REJECT
├── run_id
├── selected_workflow_id
├── selected_checkpoint_id
├── workflow_action: RESUME_EXACT / REPLAY_FROM_STAGE
├── skipped_workflow_ids
├── remaining_workflow_ids
├── reason_code
└── evidence
```

确定性规则：

1. 显式 `run_id` 不匹配时拒绝；多个候选 Run 无法唯一确定时澄清；
2. active Workflow 或 active checkpoint 不一致时拒绝；
3. 已完成 Workflow 始终进入 `skipped_workflow_ids`，不能再次执行；
4. 上游 Workflow 未完成、Artifact 缺失或 digest 变化时拒绝；
5. Workflow 版本不兼容时拒绝，不进行隐式迁移；
6. active Workflow 的副作用按外部事实确定性处理：
   - `UNKNOWN` / `STARTED` / `FAILED_AFTER_COMMIT`：无法证明安全时
     `REQUIRE_CLARIFICATION` 或 `REJECT`；
   - `COMMITTED` 且 reference/digest 与当前外部状态一致：承认既有副作用，禁止重放并
     继续恢复；
   - `COMMITTED` 但外部状态冲突：`REJECT`；
   - `COMMITTED` 但证据不足、无法确定：`REQUIRE_CLARIFICATION`；
7. `REPLAY_FROM_STAGE` 只能交给 v2.2B 已声明幂等的 Stage；
8. 通过 Run 级检查后，仍必须由 v2.2B `ResumeValidator` 决定 Stage/Task 是否可执行；
9. 恢复完成后的产物必须由 `ExecutionVerifier` 验证，不能由自然语言结果判定成功。

## 五、Store 重启合同

v2.2C 的进程重启场景要求：

```text
Store
  → serialize RunResumeIndex
  → process restart
  → deserialize RunResumeIndex
  → same RunResumeDecision
```

相同的 index、当前事实和候选 Run 集合必须产生字段级、序列化后字节级等价的
Decision。内存中的 Executor 对象、闭包、Tool handle 和 Conversation projection
不能作为恢复依据。

## 六、Dataset / Oracle 状态

本阶段冻结 16 个确定性 case，覆盖：

- A 完成、B 中断；A/B 完成、C 中断；pending Workflow 保留；
- active Workflow 的精确恢复与幂等 Replay；
- 已完成 Workflow 的副作用跳过；未知副作用澄清；
- 上游 Workflow 不完整、Artifact 缺失、Artifact digest 变化；
- 错误 Run、候选 Run 歧义、active checkpoint 不一致；
- Workflow 版本不兼容；
- Store 重启后的决策等价；
- 恢复完成后的 `ExecutionVerifier=VERIFIED` 期望证据。

Dataset / Oracle 阶段本身只证明 Run-level 合同和评测器自身正确，不单独证明生产接线。
当时保留的以下能力，现已由第八节生产实现和第九节真实 Provider P0 Gate 完成验证：

- 真实 Run Store 的加载；
- Orchestrator 的 active Workflow 选择；
- 多 Workflow 的实际执行跳过与恢复；
- 进程重启后的真实 Executor 重建；
- 真实 Provider-backed Resume Smoke。

## 七、验收门槛

### Contract / Dataset 阶段

```text
Dataset case uniqueness                 100%
Index invariant validation               100%
Oracle determinism                       100%
Index/request round-trip                 100%
Completed Workflow skip oracle           100%
Unsafe dependency/effect blocking        100%
```

### Run-level 生产接线阶段

```text
Run selection correctness                100%
Active Workflow selection                100%
Duplicate completed Workflow execution     0
Unsafe cross-Workflow resume acceptance    0
Process-restart decision drift             0
ExecutionVerifier false success            0
```

本 ADR 不授权建立第二套 Workflow Orchestrator。Run-level 接线必须通过薄
`RunResumeCoordinator` 完成；它只负责 Run 定位、原子激活、恢复委派、Artifact 发布
和索引提交。Stage/Task 编排及恢复动作仍由 v2.2B `WorkflowExecutor` 与
`ResumeValidator` 承担。

## 八、v2.2C-A 当前实现切片

Dataset / Oracle 通过后，生产侧已落地以下最小接线：

```text
RunResumeStore
      ↓ load(run_id)
RunResumeResolver
      ↓ select active Workflow + checkpoint
RunResumeCoordinator
      ↓ delegate
v2.2B WorkflowExecutor
```

当前实现包括：

- `agent/run_resume/contracts.py`：不可变 Run index、Workflow summary、Artifact
  fact 和 Request；
- `agent/run_resume/codec.py`：canonical JSON / digest；
- `agent/run_resume/store.py`：严格 revision 的内存 Store 和单写者 JSON Store；支持进程
  关闭后由新进程重新加载，但不承诺多进程并发写入、分布式锁或跨机器一致性；
- `agent/run_resume/resolver.py`：Run identity、active Workflow、依赖、Artifact、版本、
  副作用和 Replay 条件的确定性判断；
- `agent/run_resume/coordinator.py`：恢复当前 active Workflow；当没有 active Workflow
  时先调用 Store 的原子激活事务，再把执行委派给现有 `WorkflowExecutor`；完成后发布
  checkpoint 中已验证的 Artifact 摘要并更新 Run index。
- `agent/checkpoint/store.py`：v2.2C 为每个 `(run_id, workflow_id)` 保持独立的
  checkpoint 链，允许同一 Run 顺序执行多个 Workflow，同时保留 Run-wide history。

当前切片已经冻结并验证 `pending → active` 的显式启动事务：

```text
检查身份/依赖/Artifact/revision
    ↓
Store.activate_workflow (原子提交)
    ↓
active Workflow（checkpoint_id 为空也表示“已提交、尚未调用 Executor”）
    ↓
WorkflowExecutor
```

已验证的不变量包括：单 Run 单 active、完成 Workflow 不得重激活、revision 乐观并发
检查、相同 `attempt_id` 幂等、不同 attempt 竞争失败，以及激活后 Executor 调用前的
进程重启恢复。离线 A→B→C E2E 验证了依赖 Artifact 发布、已完成 Workflow 跳过、active
Workflow 恢复、最终 Run 完成和执行/副作用各一次。

当前实现仍不执行并发、分布式或 Planner 重规划。`RunResumeCoordinator` 是薄的
Run-level entry，不是第二套 Workflow Executor；真实 Provider-backed Resume Smoke 已
由第九节记录的 P0 Gate 完成验证。

## 九、C03/C07 故障窗口修复状态（2026-08-06）

首轮真实 API Smoke 暴露了两个 Runtime 缺口，已在当前 v2.2C 切片中收口：

- C03 恢复时按 `ArtifactSnapshot.reference + digest` 水合 file-backed Artifact；缺失
  或 digest 不匹配时 fail-closed。恢复路径不再把缺少 `required_outputs` 静默视为完成，
  并要求 terminal outputs 与 verifier 证据存在后才允许 Run index 进入 COMPLETED。
- C07 在 Run index 尚未写入 `active_checkpoint_id` 的崩溃窗口，按
  `(run_id, workflow_id, activation_attempt_id)` 从 CheckpointStore 找到最新合法
  checkpoint；对内容一致的已提交文件副作用做 COMMITTED reconcile，内容不一致则阻断，
  不重复调用 Provider 或覆盖文件。

离线 A→B→C 回归（C02/C03/C07）及相关 Integration/Contract 测试通过。随后 P0
真实 API 统一复跑 C01–C08，结果为 8/8 PASS、Provider Error Rate=0，并满足
Correct Resume、Completed Workflow Skip、Duplicate Side Effect、Unsafe Resume、
Artifact Integrity 和 Process-Restart Recovery 全部硬门槛。C09–C12 不属于本 ADR 的
收口条件，作为 post-close hardening backlog 保留。

## 十、v2.2C 最终证据

### Contract / Dataset

- Run Resume Dataset: 16/16 PASS
- Dataset hash: `512ba9b37feb0701618c2b0eab62ac646502cbbee6c562c351bce80622c1b908`
- Oracle determinism: PASS
- Codec / request / index round-trip: PASS

### Real Provider P0

- C01–C08: 8/8 PASS
- Raw E2E Rate: 100%
- Runtime Capability Rate: 100%
- Provider Error Rate: 0%
- Correct Workflow Resume: 100%
- Completed Workflow Skip: 100%
- Duplicate Side Effect Rate: 0%
- Unsafe Resume Acceptance Rate: 0%
- Artifact Integrity Rate: 100%
- Process-Restart Recovery: PASS

### 关键发现

1. C03：Artifact 未水合导致零执行假成功；
2. C07：Checkpoint lineage 缺失导致重复 Provider 调用与重复副作用；
3. P0 统一运行的 C03 初次误报来自 Harness stale field，而不是 Runtime 回归。
