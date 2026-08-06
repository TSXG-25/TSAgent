# ADR-0020: Durable SQLite Runtime Store Contract（v2.3B）

- 状态: Proposed — Transaction Contract and Crash Dataset
- 日期: 2026-08
- 关联: ADR-0016（Run Checkpoint）、ADR-0018（Run-Level Workflow Resume）、ADR-0019（Runtime Context Ownership）

## 一、决策摘要

v2.3B 将当前分离的进程内/JSON 持久化事实收敛到一个单机 SQLite Runtime Store：

```text
SqliteRuntimeStore
├── RunCheckpoint history
├── RunResumeIndex revisions
├── Artifact metadata / digest
├── Idempotency ledger
└── writer revision / fencing
```

一次已确认执行结果的最终事实必须在同一个 Finalization Transaction 内提交；任何
外部副作用在执行前必须先由独立的 Preparation Transaction 持久化 intent。SQLite
不能把文件写入或 Provider 调用纳入事务，因此 v2.3B 是一个带 durable intent 的
持久化状态机，不宣称外部副作用 exactly-once。这个 Store 只提供持久化、并发写入
保护和崩溃恢复事实，不负责 Planner、Workflow 选择、Provider retry、Cancellation
或 REST 服务。

SQLite 版本的边界是单机、多进程、单数据库文件：允许多个 reader 和多个 writer 由
SQLite 串行化，不承诺跨机器分布式锁、跨数据库事务或高可用复制。

## 二、为什么不能继续扩展 JSON Store

v2.2A/B/C 已经证明了 Checkpoint 和 RunResume 的数据合同，但当前生产实现仍存在
多个事实写入点：

```text
JsonCheckpointStore   → checkpoints.json
JsonRunResumeStore    → run-resume.json
Artifact / workspace  → 文件系统或独立内存对象
Idempotency           → 调用方局部状态
```

这些 Store 可以分别成功，无法保证下面的状态始终一致：

```text
checkpoint 已写入
RunResumeIndex 未更新
artifact metadata 未发布
```

因此 v2.3B 不再增加另一个 facade，而是定义一个共享数据库和事务边界；现有
`CheckpointStore` / `RunResumeStore` Protocol 继续作为兼容接口，SQLite 实现同时满足
两者。

## 三、Ownership 与数据库边界

SQLite database 属于 ApplicationContext 的持久化基础设施；SessionContext 和
RunContext 只能获得带身份约束的 view。

所有写入至少携带：

```text
tenant_id
session_id
run_id
request_id
writer_id
fence_token
expected_revision
```

数据库文件可以由多个 Session/Run 共享，但任何 query/update 都必须带 `tenant_id`
和 `run_id` 作用域。跨 Run 的 Checkpoint、Artifact 或 Idempotency key 默认拒绝。
当前 Run identity、revision、digest 和 fence 的唯一 CAS 事实源是 `run_heads`；
`run_resume_revisions` 只保存不可变审计历史。

## 四、最小持久化模型

实现可以使用不同的物理表名，但必须表达以下逻辑表和唯一约束。

### 1. `runtime_meta`

记录：

```text
schema_version
store_generation
created_at
updated_at
```

`schema_version` 用于 codec/migration 选择，`store_generation` 用于识别数据库被
替换或恢复到另一份快照。

### 2. `run_heads`

每个逻辑 Run 保留一行可变 Head，作为 CAS 和 writer ownership 的当前事实源：

```text
tenant_id
session_id
run_id
request_id
current_revision
current_digest
current_writer_id
current_fence_token
store_generation
run_status
updated_at
```

唯一约束为 `(tenant_id, run_id)`。所有写事务都必须通过 Head 做 CAS，不能每次查询
`MAX(revision)` 后再由应用层判断。

等价的 CAS 语义为：

```sql
UPDATE run_heads
SET current_revision = ?,
    current_digest = ?,
    current_writer_id = ?,
    request_id = ?,
    updated_at = ?
WHERE tenant_id = ?
  AND run_id = ?
  AND current_revision = ?
  AND current_fence_token = ?;
```

受影响行数不是 1 时，必须区分 `REVISION_CONFLICT` 与 `STALE_WRITER`。

### 3. `run_resume_revisions`

RunResumeIndex 以不可变 revision 保存：

```text
tenant_id
session_id
run_id
revision
parent_digest
payload_json
payload_digest
request_id
writer_id
fence_token
created_at
```

`(tenant_id, run_id, revision)` 唯一；`payload_digest` 和 `parent_digest` 必须形成
连续链。最新 revision 可以由索引或查询得到，不能在原行上无审计地覆盖历史。

### 4. `checkpoints`

每个 `(run_id, workflow_id)` 维护独立追加链：

```text
tenant_id
session_id
run_id
workflow_id
checkpoint_id
sequence_number
parent_checkpoint_id
activation_attempt_id
payload_json
payload_digest
request_id
created_at
```

至少有以下唯一约束：

```text
(tenant_id, run_id, checkpoint_id)
(tenant_id, run_id, workflow_id, sequence_number)
```

Checkpoint payload 必须通过 v2.2A canonical codec；数据库不得保存 HTTP response、
file handle、callable、exception 或其他 live object。

### 5. `artifact_metadata`

只保存可恢复的 artifact 事实，不把完整大文件塞进 Checkpoint：

```text
tenant_id
session_id
run_id
artifact_id
artifact_type
digest
reference
exists
verified
producer_workflow_id
producer_stage_id
created_revision
last_updated_revision
request_id
updated_at
```

`(tenant_id, run_id, artifact_id)` 唯一。文件内容仍由 Workspace/外部 artifact
存储负责，SQLite 只保存 digest、reference 和验证状态。

### 6. `idempotency_ledger`

记录一次有副作用或一次可重试 Store operation 的事实：

```text
tenant_id
session_id
run_id
idempotency_key
operation_type
request_digest
expected_effect_digest
effect_state
external_reference
result_json
result_digest
prepared_revision
committed_revision
request_id
created_at
updated_at
```

`(tenant_id, run_id, idempotency_key)` 唯一。`effect_state` 固定为：

```text
PREPARED / STARTED / COMMITTED / FAILED / UNKNOWN
```

相同 key 的行为由 `request_digest` 决定：相同 digest 可重试或读取已有状态，不同
digest 必须 `IDEMPOTENCY_CONFLICT`；不同 key 表示独立操作，正常允许。

### 7. `run_fences`

记录 writer ownership 历史；当前有效 fence 只由 `run_heads.current_fence_token`
提供：

```text
tenant_id
session_id
run_id
writer_id
fence_token
fence_epoch
acquired_at
released_at
```

旧 Worker 的写入必须因 fence token 不匹配而拒绝。`run_fences` 不得成为第二个当前
fence 事实源；它只用于审计和历史追踪。

## 五、SQLite 配置合同

第一版固定以下安全默认值：

```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous = FULL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = <bounded value>;
PRAGMA wal_autocheckpoint = <bounded pages>;
```

Store 必须在连接建立时验证实际 pragma，而不能只把配置写入日志。事务使用
`BEGIN IMMEDIATE` 或等价的明确 writer boundary；超过 bounded busy timeout 必须返回
稳定的 `STORE_BUSY`，不能无限等待。

每个进程必须建立自己的 SQLite connection，connection 不跨进程共享。写事务不得
跨 `await`、LLM、Provider 或 Tool 调用保持开启；外部调用只能发生在短事务提交
Preparation intent 之后。`wal_autocheckpoint` 或等价的受控 checkpoint 策略必须有
默认值，避免长期运行导致 WAL 无界增长。

WAL 只解决单机 reader/writer 并发和崩溃恢复，不提供跨机器锁、分布式 fencing 或
外部副作用的 exactly-once 语义。

## 六、事务边界

### T1：Acquire / Takeover Fence

Writer ownership 的取得或接管必须是短事务：

```text
验证 Run identity
→ CAS current_fence_token / fence_epoch
→ 追加 run_fences 历史
→ 更新 run_heads.current_writer_id
→ COMMIT
```

`fence_epoch` 单调递增，不能复用。旧 token 永久失效；`release()` 只能释放当前
token。是否允许 takeover 由 Coordinator 在恢复入口决定，不能仅凭超时自动抢占。

### T2：Prepare Operation

真正调用 Tool、写文件或 Provider 前，必须先持久化副作用 intent：

```text
BEGIN IMMEDIATE
→ 验证 identity / current fence / expected revision
→ reserve idempotency_key + request_digest
→ 写 effect_state=PREPARED（必要时转 STARTED）
→ 保存 operation_type、expected_effect_digest、external_reference
→ 追加 prepared_revision 对应的 Run revision
→ COMMIT
```

只有 Preparation Transaction 成功后才允许执行外部副作用。任何外部调用都不能
跨事务持有 SQLite writer lock。

### T3：Finalize Checkpoint Bundle

外部调用完成后，最终事实通过一个短 Finalization Transaction 提交：

```text
BEGIN IMMEDIATE
→ 验证 current fence / expected revision / idempotency intent
→ reconcile 外部结果与 reference/digest
→ effect_state → COMMITTED / FAILED / UNKNOWN
→ 写 Artifact metadata（created_revision / last_updated_revision）
→ 插入 Checkpoint
→ 追加 RunResumeIndex revision
→ CAS 更新 run_heads
→ 写 committed_revision
→ COMMIT
```

任意一步失败，Store 内部事实整体 rollback；外部副作用不会被 SQLite rollback，
必须按第七节规则 reconcile，不能盲目重放。

### T4：Activation / Completion

Activation 和 Workflow Completion 都是特定的 Finalization Bundle。

v2.3B 选择 **Activation Option A**：activation transaction 同时创建该
Workflow 的初始 Checkpoint：

```text
activation attempt + initial Checkpoint
→ pending → active
→ active_checkpoint_id 指向该初始 Checkpoint
→ Run revision / idempotency ledger / run_head CAS
→ COMMIT
→ 才允许调用 WorkflowExecutor
```

这样 active Workflow 不依赖 `active_checkpoint_id = NULL` 的特殊恢复分支。初始
Checkpoint 必须绑定 `activation_attempt_id`；后续 Checkpoint 必须沿同一
`(run_id, workflow_id, activation_attempt_id)` lineage 追加。

Completion 则在同一 Finalization Bundle 中提交最后 Checkpoint、已验证 Artifact、
active→completed projection 和 revision。下一个 Workflow 的 activation 是后续
事务，不能在当前事务尚未提交时调用下一个 Executor。

## 七、Crash Semantics

SQLite transaction 的原子性只保证 Store 内部事实，不会把外部文件写入或 Provider
调用自动纳入同一事务。因此必须分别定义以下窗口：

| 窗口 | Store 预期事实 | 恢复动作 |
| --- | --- | --- |
| `BEFORE_BEGIN` | 没有新事实 | 可安全重试 |
| `PREPARATION_BEFORE_COMMIT` | intent 未提交，外部调用不得发生 | 可安全重新准备 |
| `AFTER_CHECKPOINT_INSERT` | 事务回滚；Checkpoint 不可见 | 读取 latest 后重试 |
| `AFTER_ARTIFACT_METADATA` | 事务回滚；不得残留孤立 metadata | 读取 latest 后重试 |
| `AFTER_INDEX_UPDATE` | 事务回滚；不得出现 index/checkpoint 分裂 | 读取 latest 后重试 |
| `BEFORE_COMMIT` | 事务回滚或由 SQLite 恢复为旧 snapshot | 只接受旧或新完整状态 |
| `AFTER_COMMIT_BEFORE_RESPONSE` | 新事实已提交 | 用 idempotency key 返回同一结果 |
| `SIDE_EFFECT_BEFORE_FINALIZATION` | PREPARED/STARTED intent 已存在，外部状态可能已改变 | reconcile digest/reference，不能盲目重放 |
| `UNKNOWN_EXTERNAL_RESULT` | PREPARED/STARTED，不能证明副作用状态 | `REQUIRE_CLARIFICATION` 或安全拒绝 |
| `PROCESS_RESTART_AFTER_COMMIT` | Run Head、revision 和 intent 可由新进程重建 | 读取同一 committed result |

硬性原则：

```text
Store rollback ≠ 外部副作用 rollback
Preparation commit ≠ 外部副作用已完成
COMMITTED + matching digest → 承认并跳过重复副作用
PREPARED/STARTED + missing external state → 仅在操作可重放时重试
UNKNOWN → 不允许自动重放
```

## 八、Revision、CAS 与 Fencing

每个变更必须同时验证：

```text
run_heads.current_revision == expected_revision
run_heads.current_fence_token == fence_token
parent_digest == latest_digest
```

失败时使用稳定错误类别：

```text
REVISION_CONFLICT
STALE_WRITER
IDEMPOTENCY_CONFLICT
STORE_BUSY
SCHEMA_INCOMPATIBLE
```

### Fence acquire / takeover

接管必须在 `BEGIN IMMEDIATE` 内通过 CAS 完成：

```text
writer A acquire → fence_epoch=10
writer A 崩溃
writer B takeover → fence_epoch=11
writer A 使用 token=10 写入 → STALE_WRITER
```

token/epoch 必须单调递增、不能复用；新 token 提交后所有旧 token 永久失效。旧
writer 不能 release 新 writer 的 token，Writer 崩溃也不能让 Run 永久不可恢复。

### Idempotency semantics

```text
same key + same operation/request digest + COMMITTED
    → 返回相同 committed result
same key + same digest + PREPARED/STARTED
    → 返回现有 intent，不创建第二次操作
same key + different operation/request digest
    → IDEMPOTENCY_CONFLICT
different key
    → 独立操作，正常允许
```

同一 `writer_id + fence_token + idempotency_key` 重试应幂等；不同 fence token 即使
revision 看起来较新，也不能覆盖当前 writer 的事实。

## 九、读一致性与恢复

普通读取可以使用 SQLite read transaction，但必须保证一次恢复判定读取到同一个
一致性 snapshot：

```text
RunResumeIndex revision
active Checkpoint
Artifact metadata
Idempotency facts
run_heads.current_revision / current_digest
```

禁止分别读取多个 Store 后在内存中拼装一个混合版本。进程重启后必须由数据库重建
Store view；不得依赖旧 Executor、旧 EventBus 或旧 RunContext 对象。

每条 Artifact 和 Idempotency 事实至少记录 `created_revision`/
`last_updated_revision` 或 `prepared_revision`/`committed_revision`，以便与
Checkpoint 和 RunResumeIndex 对账。

## 十、迁移与兼容边界

- v2.2 的 `InMemory*Store` 保留用于确定性单测；
- v2.2C 的 JSON Store 可作为读取/迁移输入，但不再是 v2.3B 新生产路径的事实源；
- SQLite schema 必须带 major/minor version；major 不兼容时拒绝启动，不静默丢字段；
- migration 必须是显式、可审计、可回滚前置检查的步骤；本阶段不做大规模历史
  Checkpoint migration；
- SQLite 只保存 metadata/reference，不自动删除 durable Workspace 或外部 artifact。

## 十一、验收门槛

```text
Schema / canonical codec round-trip             100%
Preparation intent before external effect        100%
Finalization bundle atomicity                    100%
No torn index/checkpoint/artifact state         100%
Same-key + same-digest retry                    100%
Same-key + different-digest conflict            100% rejected
Different-key independent operation              100% allowed
Revision CAS conflict                           100% rejected
Stale writer fencing / takeover                  100% rejected / monotonic
SQLite WAL / FULL pragma verification            PASS
Crash after COMMIT recovery                     PASS
External side-effect unknown                    0% auto-resume
Process restart rehydration                     PASS
```

本 ADR 的 Dataset / Oracle PASS 只证明事务合同和评测器正确，不宣称 SQLite 生产实现
已经完成。生产能力必须由 v2.3B implementation gate 另外证明。

当前修订后的 Dataset / Oracle 基线为：

```text
benchmark_version: v0.2
contract_version: adr-0020-v2
cases: 19
dataset_hash: 826704e60db7d868b4f27740cf27e0bcb95be872ebd67a5fea5d707fc9faaa61
oracle_validation: PASS
```

该基线特别包含 Preparation intent、Run Head CAS、单调 fence takeover、same-key
digest 冲突和 different-key 独立操作；修改这些语义时必须递增 benchmark version，
不能把新旧结果直接合并到同一条 Trend。

## 十二、v2.3B 明确不做

- REST、SDK、WebSocket 或 AgentService；
- Cancellation、Timeout、Provider retry/failover；
- 跨机器分布式锁和多数据库事务；
- Planner 重规划和 Workflow DAG 调度；
- 自动补偿外部副作用；
- 大规模历史数据迁移和 retention/purge 产品策略。
