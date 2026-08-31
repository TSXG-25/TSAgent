# ADR-0032: Memory Learning Capability Contract（v2.4D-1）

- 状态: Proposed — Discovery complete / production precondition blocked
- 评估基线: `79305fe3`（v2.4C Workflow capability freeze）
- 范围: Memory learning evidence、资格判断、scope、provenance、去重/冲突边界
- 非范围: 现有 Memory store 重写、retrieval 排序、Conversation Runtime、RunOutput、Repository index、MemoryLearner 实现

## 1. 背景

v2.4D 先审计生产代码中的真实 Memory 写路径，再决定是否需要新的学习决策能力。
本次审计不调用 Provider、不修改 Memory store，也不把已有的 Memory facade 误认为统一
learning boundary。

审计基线发现：生产代码目前有多条独立写入路径，`ScopedMemoryView` 只绑定 namespace，
并不判断一条 evidence 是否有资格长期保存。当前不存在一个生产入口消费：

```text
InteractionEvidence + MemoryPolicyProjection
    → MemoryLearningDecision
    → one scoped persistence boundary
```

因此在补齐 contract 前，不能进行真实 Provider 的 Memory Learning baseline，也不能把
当前 `extract_and_save_facts()` 的结果称作 Learning Decision。

## 2. Discovery 结果

| 层 | 当前生产写入口 | 实际存储 | scope 事实 | provenance / 去重 / 过期 |
| --- | --- | --- | --- | --- |
| Session | `session.add_*_message()` | process-global dict | namespace key | 无 evidence provenance；进程生命周期 |
| Short-term | `short_term.add_exchange()` | `data/short_term/<namespace>.json` | 文件名 namespace | 追加；窗口/压缩阈值，不是事实级过期；无 provenance |
| Long-term summary | `long_term.store_summary()`、short-term compression | Chroma `long_term_memory` | metadata `user_id` | 追加 document；只有 type/timestamp；无 evidence id、冲突或 expiry |
| User facts / preferences | `preference.async_extract_and_save_facts()` → `long_term.save_fact()` | `data/user_facts.db` | `user_id` | `(user_id, category, key)` 唯一；`INSERT OR REPLACE` 无 confidence/source/revision |
| Resolution memory | `resolution.record_resolution()` | `data/resolution_memory/<namespace>.json` | 文件名 namespace | 追加，最多 100 条；metadata 可选但当前 Planner 不传 provenance；无冲突/expiry |

以下内容明确不属于本 ADR 的 Memory Learning 写入：

- `ConversationTracker` 的 `ConversationState`：ADR-0013 定义为当前交互 Runtime state；
- `RunOutput`、Checkpoint、Artifact、Event：由 Runtime durable contracts 所有；
- `RepositoryIndexer` 的 symbol/file index：workspace grounding index，不是用户记忆。

## 3. 当前边界问题

### D32-001：write authorization fragmented

`MemoryService` 暴露 session、short-term、summary、facts、resolution 多组 public mutator；
`UniversalAgent`、`PlannerStage`、短期压缩和 preference extractor 分别直接触发写入。
没有统一的 eligibility decision，也没有一个可审计的“本轮允许写入哪些 memory type”事实。

### D32-002：namespace 不等于 learning scope

`ScopedMemoryView` 能防止调用方省略 namespace，但 `SessionRuntime` 的 memory namespace
可能是 session id，也可能是 `tenant:user` 组合；底层 store 仍把这个字符串同时当作
user/session key。它没有独立表达 `session`、`user`、`repository` 或 `run` scope，也没有
阻止 scope widening 的 decision contract。

### D32-003：provenance 不完整

summary 只保存 `user_id/type/timestamp`；facts schema 只保存 user/category/key/value；
resolution 虽有 metadata 字段，但当前 Planner 只传 utterance、target、kind。写入结果
不能稳定回答“由哪条 evidence、哪个 run、哪个来源确认”。

### D32-004：dedup、conflict、expiry 语义分裂

facts 使用唯一键覆盖，summary/resolution/short-term 使用追加；没有跨层 canonical key、
revision、冲突状态或 evidence freshness 合同。`MAX_ENTRIES` 与 short-term window 只是
容量限制，不等于事实过期策略。

### D32-005：错误结果可能被伪装成成功写入

`save_fact()`、`store_summary()` 等当前实现吞掉存储异常；extractor 返回抽取到的 facts
与实际持久化提交没有独立 commit evidence。这是后续实现必须保留的 truthfulness 风险，
本 ADR 不在 discovery 阶段修复。

### D32-006：读取 fallback 可能绕过 scope

`retrieve_summaries()` 在带 `user_id` filter 的 Chroma 查询异常时，会重试不带 filter 的
相似度查询。该 fallback 不是一个可接受的 Memory Learning contract，因为它可能返回
其他 namespace 的 summary；本轮只登记为现有实现风险，不修改生产代码。

## 4. 目标合同

Memory Learning 的最小决策边界定义为：

```text
InteractionEvidence + MemoryPolicyProjection
    → MemoryLearningDecision
```

### 4.1 InteractionEvidence

输入必须是经过 Runtime projection 的事实，而不是完整 Runtime state：

```text
evidence_id
source_kind             # user_statement / user_confirmed_resolution / ...
source_ref              # turn/run/artifact reference
text
memory_type             # fact / preference / summary / resolution
requested_scope         # session / user / repository
canonical_key
value
explicit_persist        # 是否明确要求记住/长期使用
sensitive
secret
volatile
existing               # 当前同 scope/key 的已持久化事实（可空）
```

`assistant_output`、`tool_observation`、`repository_observation`、`run_artifact` 可以作为
evidence 的来源被审计，但默认不能单独授权为 user Memory 写入。

### 4.2 MemoryPolicyProjection

Policy projection 由上游提供已确定的 scope、敏感信息和持久化许可事实；Selector/未来
decision component 不读取 SQLite、Chroma、Checkpoint 或 Workspace。

### 4.3 MemoryLearningDecision

第一版只允许三个动作：

```text
STORE   新的 canonical key
UPDATE  同 scope/key 的显式新值
IGNORE  不产生 Memory write
```

写入决策必须包含：

```text
action
memory_type
scope
canonical_key
value
provenance: { evidence_id, source_kind, source_ref }
reason_code
```

`IGNORE` 不携带可执行的 key/value/provenance；它只表达“不写入”的原因。删除/忘记由
`MemoryRuntime` 生命周期合同负责，不由 Learning Decision 冒充 delete。

## 5. 所有权与不变量

```text
Runtime / Conversation / Resolver / Tool
    → emit projected InteractionEvidence

Memory learning policy
    → decide STORE / UPDATE / IGNORE

one scoped Memory persistence boundary
    → commit and return durable evidence

Memory retrieval
    → read only within the requested scope
```

必须满足：

1. 没有 `MemoryLearningDecision` 的 evidence 不得触发长期 Memory write；
2. STORE/UPDATE 必须有同 scope 的 canonical key、非空 value 和 provenance；
3. source、scope、memory type 不得由持久化层猜测或扩大；
4. sensitive data 需要显式持久化许可；secret 永不进入该 contract；volatile observation
   默认不进入 durable user Memory；
5. 同 scope/key 的同值重复写应被识别为 `IGNORE/DUPLICATE`，不同显式值才是 `UPDATE`；
6. `IGNORE` 与持久化失败不能被用户层表达为“已记录”；成功写入必须有 commit evidence；
7. retrieval 的 scope filter 失败不得降级为不带 scope 的全局查询；
8. Memory reset/delete 仍由生命周期 owner 执行，并且必须保持 namespace isolation。

## 6. Dataset / Oracle

合同自洽数据集位于：

```text
evals/memory_learning/dataset.json
version = v2.4D-memory-learning-v1
cases  = 24
```

六个 family 各覆盖四例：

```text
ELIGIBILITY             用户事实/偏好是否值得学习
SOURCE_AUTHORITY        用户、助手、工具、仓库来源边界
SCOPE                   session/user/repository scope ownership
DEDUP_CONFLICT          STORE/UPDATE/IGNORE 的 key 冲突
SENSITIVITY_VOLATILITY  敏感、secret、时变数据
LIFECYCLE_BOUNDARY      run/artifact、assistant output、forget/delete 边界
```

确定性 Oracle 只验证合同形状、scope、provenance、安全约束和 golden self-check；不调用
Provider、Store、Chroma、SQLite 或 Runtime。Golden self-check 通过不代表生产 Memory
Learning 已实现。

## 7. Production preflight

`realtest_reports/harness/v24d_memory_learning_preflight.py` 是只读 AST/source preflight：

- 不 import Memory store，不触发 data/ 初始化；
- 不调用 Provider，不执行真实写入；
- 盘点生产 writer、scope、provenance、dedup/conflict/expiry 和 retrieval fallback；
- 没有正式 production learning entry 或统一 persistence boundary 时，输出
  `BLOCKED_PRECONDITION`，所有 24 case 标记 `NOT_EVALUABLE/P-INT`；
- 不能把 `MemoryService` facade、`extract_and_save_facts()` 或 `record_full_exchange()`
  当作 Memory Learning Selector。

当前基线预期为 blocked。只有后续明确完成合同 owner、生产入口和单一写入边界后，才能
进入 real-provider baseline。

## 8. 非目标与后续决策

本 ADR 不实现 `MemoryLearner`，不迁移现有 store，不改变既有 retrieval 行为，也不修复
本次审计发现的 scope fallback。下一步只有在评审确认本合同后，才单独进入：

```text
v2.4D-2  production learning boundary bootstrap
v2.4D-3  deterministic contract acceptance
v2.4D-4  real Provider baseline / attribution
```

如果评审决定不需要独立 learning decision，则应修订本 ADR，明确现有写入路径的统一
授权 owner，而不是保留“多个写入口但文档称其统一”的状态。
