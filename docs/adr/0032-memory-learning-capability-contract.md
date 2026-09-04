# ADR-0032: Memory Learning Capability Contract（v2.4D）

- 状态: Implemented — D-2/D-3a/D-3b/D-3c/D-3d/D-3e/D-3f verified; Human Acceptance #1 next
- 评估基线: `5aa814d7`（v2.4D-1 discovery）；D-2 实现：`306d5adc`
- 范围: Memory learning evidence、资格判断、scope、provenance、去重/冲突边界、commit evidence
- 非范围: 现有 Memory store 架构重写、retrieval 排序、Conversation Runtime、RunOutput、Repository index、MemoryLearner 实现

## 1. 背景

v2.4D 先审计生产代码中的真实 Memory 写路径，再决定是否需要新的学习决策能力。
本次审计不调用 Provider，也不把已有的 Memory facade 误认为统一 learning boundary。
D-2 保留现有事实、摘要和 resolution store 作为存储适配器，只在其上增加显式的决策与
提交边界；session/short-term runtime buffer 仍由各自生命周期 owner 管理。

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
resolution             # resolution memory 的最小 ResolutionEvidence（v1.1 起必需）
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

`5aa814d7` 的 D-1 基线预期为 blocked。D-2 完成后，preflight 应只在发现直接绕过
`MemoryPersistenceBoundary`、缺少 scope/provenance 或存在 unscoped retrieval fallback 时
保持 blocked；它本身仍不调用 Provider，也不执行 Memory 写入。

`realtest_reports/harness/v24d_memory_learning_provider_preflight.py` 是 D-3a 独立的
Provider-entry preflight。它只扫描生产源码；只有发现正式的
`MemoryLearningProvider` 且 Provider 没有 persistence/store/evaluation import 时，才输出
`READY_FOR_REAL_BASELINE`。它不把事实抽取器或确定性 policy 当作 Provider，也不调用模型。

## 8. D-2 实现

生产边界已经收敛为：

```text
InteractionEvidence + MemoryPolicyProjection
        ↓
decide_memory_learning()
        ↓
MemoryLearningDecision
        ↓
MemoryPersistenceBoundary.commit()
        ↓
MemoryCommitEvidence
```

具体 owner：

- `agent/memory/learning.py` 只负责确定性的 `STORE / UPDATE / IGNORE`、source/scope/type、
  sensitive/secret/volatile 和 duplicate/update policy；并在边界复核 canonical key 与 value
  中的明显敏感模式；不读取或写入存储。
- `agent/memory/persistence.py` 是唯一 learned-memory durable commit boundary；它调用既有
  fact、summary、resolution storage adapter，并返回 `committed`、store、scope、key、
  evidence、record id/revision 或稳定错误。
- `agent/memory/preference.py` 只保留非权威 candidate extraction utility；它不再决定或提交
  durable learned Memory。
- `MemoryService`/`ScopedMemoryView` 是 production durable-learning owner，负责把 interaction
  投影成 evidence，并将 namespace 与显式 `learning_scope` 交给 Provider、policy 和 commit
  boundary；只将已提交事实暴露给上层。
  session message 和 short-term exchange 是 transient/runtime 写入，不属于 learned-memory
  decision gate；short-term compression 生成的 durable summary 必须经过同一 boundary。
- summary/fact/resolution storage metadata 携带 `scope`、`evidence_id`、`source_kind`、
  `source_ref`；facts 的 canonical uniqueness 包含 scope 和 revision；resolution 的同
  scope/canonical value 重复提交保持幂等。
- scoped summary/resolution/fact retrieval 不允许在过滤失败时退化为全局查询。
- `MemoryRuntime.reset` 仍然是删除/forget 的生命周期 owner，不由 learning decision 模拟 delete。

因此，只有 `MemoryCommitEvidence.committed == true` 才能支持用户层的持久化成功确认。
`IGNORE`、存储异常和 scope mismatch 都必须保留为非提交结果。

## 9. D-3a Provider 入口

`agent/memory/learning_provider.py` 提供唯一的 Provider-backed decision entry：

```text
InteractionEvidence + ExistingMemory + MemoryPolicyProjection
        ↓
MemoryLearningProvider.select()
        ↓
MemoryLearningDecision（未提交 proposal）
```

该 Provider 只负责提出一个 canonical decision。它不导入 Memory store 或
`MemoryPersistenceBoundary`，不修改 Runtime，不执行重试或跨 Provider fallback，也不返回
commit 结论。Provider proposal 必须经过
`authorize_memory_learning_proposal()`，由 D-2 deterministic policy 重新验证 safety、scope、
provenance、dedup 和 update eligibility，之后才允许交给 persistence boundary。

Provider transport 的 structured/raw path 只用于记录证据；`STRUCTURED_TO_RAW_FALLBACK`
表示同一 Provider 的格式降级，不表示 Provider 切换。D-3a 离线 contract/preflight 通过后，
才允许使用固定 Dataset 执行真实 Provider baseline。

## 10. D-3c Measurement Calibration

The real Provider baseline keeps the original strict Oracle result immutable.  The
Provider must still emit the seven-field canonical decision shape, including a
non-empty `reason_code`, so transport and audit evidence remain complete.  However,
`reason_code` is diagnostic metadata, not a semantic capability gate: it is not
used by D-2 authorization to choose a different safety, scope, deduplication, or
persistence branch.

The D-3c calibrated semantic view therefore scores only:

```text
action + memory_type + scope + canonical_key + value + provenance
```

and reports `reason_code` vocabulary mismatches separately as
`P-MEASUREMENT:REASON_CODE_VOCABULARY`.  The calibration has its own version/hash
and never rewrites the v2.4D-memory-learning-v1 dataset or the D-3b raw result.

`IGNORE` still requires the canonical empty object `{}` for `provenance`; an empty
string is a schema-invalid Provider response, not an implicit repair target.  An
`IGNORE` decision carries no durable provenance claim or write fields.

The reproducible audit is:

```text
realtest_reports/harness/v24d_memory_learning_attribution.py
```

It makes zero Provider calls and consumes only the immutable D-3b result.

## 11. D-3d Persistence-aware Integration

The persistence-aware evidence replays the captured real Provider proposals through:

```text
Provider proposal
    → authorize_memory_learning_proposal()
    → MemoryPersistenceBoundary.commit()
    → MemoryCommitEvidence
```

It uses disposable isolated stores and does not make a second Provider call.  The
Provider is not a safety boundary: D018/D020 may remain unsafe proposals, but the
deterministic policy must veto them before durable commit.  Only
`MemoryCommitEvidence.committed == true` can support a persistence acknowledgement.

D023 also demonstrates that resolution persistence needs its `utterance` and `kind`
details in the projected evidence; missing details are a contract/integration
failure and must not be reported as a successful commit.

The replay harness is:

```text
realtest_reports/harness/v24d_memory_learning_integration.py
```

## 12. D-3e Resolution Evidence Contract

D023 的 persistence failure 是一个合同缺口，而不是 Provider prompt 问题：resolution
decision 已经包含 target，但 persistence 还需要原始 `utterance` 与解析 `kind`。
这些字段必须作为 Runtime projection 的一部分显式进入 learning boundary，不能由
persistence 回头读取 Conversation、Runtime 或其他全局状态补齐。

v1 数据集保持不可变。D-3e 发布了显式的 v1.1 数据集和新 hash：

```text
evals/memory_learning/dataset_v1_1.json
version = v2.4D-memory-learning-v1.1
base_dataset_hash = e821b67e7da66b40a7c1cc38ac6e18d1636b6d01c6aeaa3e873f03bb95f928b7
dataset_hash = c3b4ed509f6629674c4476f00bf0026561c1f5b1ceb54084ca1bca7443692e82
```

v1.1 的输入合同明确增加：

```text
InteractionEvidence + ResolutionEvidence + MemoryPolicyProjection
    → MemoryLearningDecision

ResolutionEvidence = {
    utterance: non-empty string,
    kind: non-empty string,
    metadata: object
}
```

`agent.memory.learning.ResolutionEvidence` 是 typed canonical payload；它只在
`memory_type == "resolution"` 时允许出现。`decide_memory_learning()` 将它绑定到
内部 decision，Provider 的七字段公开 proposal 形状不变；授权后的 decision 才能把
该 payload 交给 `MemoryPersistenceBoundary`。

Resolution persistence 只消费 `decision.resolution`，缺少该 payload 时返回稳定的非提交
`MemoryCommitEvidence`，不会从 Runtime 或 Conversation 读取字段。成功的
`MemoryCommitEvidence` 还包含 canonical `content` projection（`utterance`、resolved
target、`kind`、metadata），并携带 record id/revision；调用方可以据此核对实际 durable
record，而不把 `committed == true` 当作唯一证据。

D-3e 回放使用不可变的 D-3b Provider proposal、v1.1 evidence 和隔离存储，不产生新的
Provider 调用。D023 的 resolution record 现在逐字段验证通过；D018/D020 仍保持
`proposal → deterministic veto → 0 durable commit`。

## 13. D-3f Production Memory Wiring

真实 Runtime/CLI 的 durable user-memory 路径已经迁移为单一 canonical chain：

```text
User interaction
    → InteractionEvidence projection
    → MemoryLearningProvider proposal
    → authorize_memory_learning_proposal()
    → MemoryPersistenceBoundary.commit()
    → MemoryCommitEvidence
```

`UniversalAgent` 的 fact-capture 路径调用 `ScopedMemoryView.learn_from_interaction()`，并将当前
Run id（无 Run 时为 session namespace）作为 `source_ref`。`MemoryService` 在调用 Provider
前只投影窄的 candidate evidence 与同 scope/key existing value；Provider 不读取 Store，policy
不信任 Provider 的 safety/scope/provenance 判断，persistence 只消费授权后的 decision。

summary compression 与 confirmed resolution 也通过同一 Provider → authorization → commit
链进入既有存储适配器。旧 `extract_facts_with_llm()` 只保留为非权威 utility；production
durable-learning call site 为零，不存在 extractor → Provider 的双 LLM learning path。

D-3f 离线证据固定：

- `我叫 TS`：production Runtime 调用 Provider 一次、legacy extractor 零次、提交
  `personal.name=TS`；
- `我的 API key 是 sk-test-example`：即使 Provider 提议 `STORE`，policy 仍返回
  `SECRET_NEVER_STORE`，storage adapter 调用为零且无成功确认；
- production Provider proposal owner 仅位于 `agent/services/memory_service.py`；旧
  `extract_and_save_facts` public entry 已移除。

## 14. 非目标与后续决策

本阶段不把事实抽取器改造成 Learning Provider，不做 expiry/TTL engine，不改变 retrieval
ranking，不把 post-answer learning 改成异步投递，也不改变 CLI 生命周期、预算或 identity。
后续单独进入：

```text
v2.4D-3b  real Provider baseline / attribution                 DONE
v2.4D-3c  semantic measurement calibration                     DONE
v2.4D-3d  persistence-aware integration replay                  DONE
v2.4D-3e  resolution evidence contract closeout                 DONE
v2.4D-3f  production Memory wiring                              DONE
v2.4D-4   Human Acceptance #1 / final freeze                     NEXT
```

Provider proposal capability 不能替代 durable commit truthfulness；D-3d 的
unauthorized/sensitive/secret/scope commit、false acknowledgement 和 cross-scope
leakage 指标必须保持为零后，才可进入最终 freeze。
