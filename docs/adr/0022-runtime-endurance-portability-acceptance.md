# ADR-0022: Runtime Endurance & Provider Portability Acceptance（P2）

- 状态: Accepted — Contract Frozen; P2-LH1 / P2-S1 / P2-R1 Runtime Verified
- 日期: 2026-08-09
- 关联: ADR-0016（Run Checkpoint）、ADR-0018（Run-Level Resume）、ADR-0019（Context Ownership）、ADR-0020（Durable Store）、ADR-0021（AgentService/Event Stream）
- Dataset: `benchmarks/p2/`
- Contract version: `adr-0022-v1`

## 一、背景与范围

P1 已经证明单次能力、Service、Context 隔离、恢复安全和 effect truthfulness 的
基础门禁。P2 不再主要回答“一个请求能否完成”，而回答：

```text
连续长链、真实进程退出、并发/Soak、Provider 更换之后，
P1 已冻结的 Runtime 保证是否仍然成立。
```

本 ADR 只冻结 P2 的验收合同、Dataset、Oracle、双层评分和性能观测口径。它不
授权在本阶段直接引入新的 Orchestrator、Planner、Provider failover、Cancellation
或分布式执行。

## 二、双层评分合同

每个真实执行 case 必须同时输出两层结果：

```text
Capability Outcome
  PASS / FAIL / PARTIAL

Runtime Correctness
  PASS / FAIL
```

Capability 失败不自动等于 Runtime 失败。例如 Provider 没有修好代码，但 Run
正确保留已验证产物、进入 `FAILED`、发布唯一 `run_failed`，则：

```text
Capability Outcome = FAIL
Runtime Correctness = PASS
```

相反，没有完成任务但产生 `COMPLETED`、重复副作用或错误恢复，即使回答看起来
正确，也必须是 Runtime Correctness = FAIL。

## 三、P2 硬门禁

以下指标在所有适用 case 上必须为零；任何非零都阻塞 P2 收口：

```text
False COMPLETED
Duplicate Side Effect
Cross-context Leakage
Security Violation
Stale Writer Acceptance
Terminal Snapshot/Event Mismatch
Durable State Loss
Completed Workflow Re-execution
Unsupported Effect Hallucination
```

Restart/Soak 还必须将以下结果单独报告，并按安全门禁处理：

```text
Event Gap
Orphan Active Run
Subscriber Leak
SQLite Deadlock / unhandled BUSY
```

## 四、16-case Dataset

Dataset 的固定分组为：

| 组 | Case | 目标 |
| --- | --- | --- |
| Long-horizon | L01–L05 | 10–20 步依赖链、分支合并、有限重规划、多产物、进度保留 |
| Restart/Recovery | R01–R04 | 子进程 kill、effect reconcile、事件 replay、跨 Workflow 恢复 |
| Soak/Concurrency | S01–S04 | 50 顺序 Run、10×5 Session、10 并发 Run、500 replay/read |
| Provider Portability | P01–P03 | 同一任务在 primary/secondary Provider 上运行，不重新提示 |

所有 case 都要求 Runtime Correctness 的期望值为 `PASS`；Capability Outcome 和
性能数据是观测指标，不以“为了满分而重试”替代正式结论。

## 五、Restart/Recovery 证据

R 组必须使用真实独立进程，至少覆盖这些崩溃窗口：

```text
active Run 执行中被 kill
external effect 已提交、finalization 尚未完成
checkpoint/event 已提交、客户端尚未收到响应
Workflow A 已完成、Workflow B active
```

恢复报告必须包含 `run_id`、checkpoint lineage、fence/revision、effect ledger、
execution counts、event cursor 和最终 Snapshot/Event 一致性；不能只根据内存中的
Executor 对象判定恢复成功。

## 六、Soak/Concurrency 证据

S 组必须记录：

```text
connections
pending tasks
subscriber count
RunContext / workspace count
memory trend
SQLite busy/deadlock
cross-scope artifact/event/memory evidence
```

内存使用采用趋势报告，不设脱离环境的固定 MB 阈值；身份串扰、重复副作用、孤儿
active Run、subscriber 泄漏和未处理 SQLite busy 则按硬门禁处理。

## 七、Provider Portability

P 组每个 case 使用同一 scenario/parity key，在两个 Provider 上运行；不得因为
第一个 Provider 的失败而改写提示、目标或验收条件。允许第二个 Provider 的
Capability Outcome 失败，但以下 Runtime 语义必须保持一致：

```text
tool grounding
terminal semantics
effect truthfulness
event semantics
resume semantics
```

Provider 结果必须分离记录：Provider Error、Capability Outcome、Runtime Correctness。

## 八、性能观测合同

不使用一个统一的固定 timeout 作为 P2 结论。每个 case 声明 profile，并至少记录：

```text
wall_ms
provider_ms
llm_calls
replans
tool_calls
time_to_first_event_ms
time_to_first_artifact_ms
```

profile 至少区分：`simple`、`single_tool`、`multi_tool`、`event_replay`、`resume`、
`long_horizon`。报告应提供 p50/p95/max，并保留 Provider 与 Runtime 的耗时分层。

## 九、阶段边界与 Deferred Validation

当前阶段只完成：

```text
Contract / Dataset / Oracle / Validation
```

后续实现顺序固定为：

```text
P2-L  Long-horizon harness and evidence
P2-R  Real subprocess crash/recovery harness
P2-S  Soak/concurrency harness
P2-P  Second-provider parity
P2-F  Final clean acceptance and freeze
```

以下内容明确 deferred：Cancellation/Timeout Contract、Provider Failover、
分布式执行、跨机器 Store、DAG Orchestrator 扩展、Multi-Agent、为 MEM03 单例
模型波动重新设计 Memory Runtime。

## 十、当前证据

```text
P1 commit: 06cf0345
P1 full real API: 31/32
P1 remaining case: MEM03, classified MODEL_QUALITY_VARIANCE
H2 deterministic dataset: 9/9 PASS
Offline regression after H2: 427 passed, 17 skipped, 2 deselected
mypy: PASS
P2 dataset: 16 cases
P2 dataset validation: PASS after this ADR/Dataset slice
```

P2 的真实能力结果不得回写覆盖 P1 的 `31/32`，也不得将 Provider Error 或模型质量
波动混入 Runtime Correctness 门禁。

## 十一、当前 Runtime 实现证据

P2 仍未整体收口：Long-horizon 的真实 Provider Capability 和第二 Provider
Portability 保持 deferred。但以下 Runtime 子阶段已经独立冻结：

```text
P2-LH1 Workspace Boundary
commit: 4b477370
scoped Tool / Artifact / Verifier boundary: PASS
global workspace leakage: 0
false COMPLETED: 0

P2-S1 Deterministic Soak
commit: d2a0bae8
S01 50 sequential Runs: PASS
S02 10 Sessions × 5 Runs: PASS
S03 10 concurrent interleaved Runs: PASS
S04 500 durable event replays: PASS
resource / subscriber / context / workspace leaks: 0

P2-R1 Process Crash / Restart
harness/discovery: 25bdf8d1
Runtime hotfix: 39e5aea7
final evidence: e0c4340d
R01–R04: 4/4 PASS
true child-process SIGKILL: 4/4
duplicate side effect: 0
completed Workflow re-execution: 0
stale writer acceptance: 0
terminal Snapshot/Event mismatch: 0
durable state loss: 0
event replay gap: 0
false COMPLETED: 0
```

P2-R1 使用真实父子进程。父进程只在 fsync milestone marker 出现后发送
`SIGKILL`；新进程使用新的 SQLite connection、Context、EventBus 和 writer fence，
不能依赖旧进程内对象。R02 还证明 effect 已写入、Finalization Bundle 未提交的窗口
会通过 scoped workspace reconcile，而不会再次执行副作用。

证据文件：

```text
realtest_reports/results/p2_r1_discovery_round1.json  # 原始 0/4 FAIL
realtest_reports/results/p2_r1_round1.json            # 修复后 4/4 PASS
```

Dataset provenance：

```text
Full P2 Dataset hash:
f51c5fef7a13d82e20003bf08bcb593b482511477a5c493bdff8df6bfbf9baef

R01–R04 subset hash:
83eab4a5e0762cd044ef5fcc7e057e090647bfd89e43f4e7da2a421b4d31cb63
```

P2-R1 closeout gate：

```text
R/Store/Service/Workflow/Architecture regression: 96 PASS
P2 Dataset validation: 16/16 PASS
mypy (changed production + harness modules): PASS
git diff --check: PASS

Full pytest:
460 passed, 17 skipped, 1 environment failure
environment failure: tests/test_tools_execution.py::test_web_fetch
reason: sandbox DNS unavailable for example.com
```

`test_web_fetch` 的 pytest 状态是 FAIL，不是 skip；它作为外网环境失败单独记录，
不计入 P2-R1 的 deterministic process-recovery 能力结论。

后续边界保持不变：P2-L 的真实 Provider Capability 只补证据，不重新打开 LH1；
P2-P 等待第二 Provider；Cancellation、Provider Failover 和分布式恢复不进入 P2-R1。
