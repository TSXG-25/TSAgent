# ADR-0023: Cancellation / Timeout Contract（v2.3D）

- 状态: Accepted — Implemented and Verified
- 日期: 2026-08-10
- 关联: ADR-0016（RunCheckpoint）、ADR-0019（Context Ownership）、ADR-0020（Durable Store）、ADR-0021（AgentService/Event Stream）、ADR-0022（P2 Acceptance）
- Dataset: `benchmarks/v23d/`
- Contract version: `adr-0023-v1`

## 一、背景与范围

TSAgent 已经证明 Context 隔离、durable Store、AgentService、进程死亡恢复和双
Provider Runtime 正确性，但尚未冻结用户取消和不同层级 timeout 的生命周期语义。
一个进程内 `asyncio.Task.cancel()` 只能是实现机制，不能代表产品合同：进程重启后，
新的 Worker 必须仍然知道用户已经请求取消；正在提交的原子事实也不能被任意拆断。

本 ADR 冻结：

```text
durable interruption intent
reason-specific policy
safe cancellation boundary
Tool cancellation safety class
terminal / resume semantics
Dataset / Oracle / hard gates
```

本阶段 D1 不实现 `AgentService.cancel_run()`，不修改 Planner/Provider/Tool/Workflow
执行链，不接 CLI/前端，也不声称真实 cancellation capability 已完成。

## 二、核心决策

### 2.1 Cancellation 是 durable intent

取消顺序固定为：

```text
CancelRunRequest
    ↓ identity + idempotency validation
durable CancellationIntent
    ↓ Runtime observes current revision/fence
safe cancellation boundary
    ↓ flush checkpoint/effect/event evidence
terminal transition
```

禁止将以下顺序作为产品语义：

```text
asyncio.Task.cancel()
    ↓
假设 Run 已经 CANCELLED
```

`CancellationIntent` 是 JSON-only immutable fact，至少包含：

```text
tenant_id
user_id
session_id
run_id
request_id
requested_at
requested_by
reason
revision
phase
details
```

同一 scope、同一 `request_id`、同一 canonical digest 的重复请求返回同一 intent；同一
`request_id` 代表不同 digest 时必须冲突。intent 必须先持久化，执行层才允许观察并
采取中断动作。

### 2.2 Intent phase 与 Run status 分离

`InterruptionPhase` 描述 durable intent 的处理进度：

```text
REQUESTED → OBSERVED → CANCELLING → FINALIZED
                    └────────────→ FINALIZED
```

第二条路径用于 `RUN_TIMEOUT`、`SERVICE_SHUTDOWN` 或 Decision-owned timeout，它们
不需要伪装成用户取消中的 `CANCELLING`。

这不是第二套 Run lifecycle。D2 接线时必须扩展现有公开 `RunStatus` 和同一 durable
Run 事实源，不得新建平行状态机。Run 层需要支持：

```text
CREATED / RUNNING / SUSPENDED / WAITING_USER
FAILED_RECOVERABLE / FAILED_TERMINAL / BLOCKED
CANCELLING / CANCELLED / TIMED_OUT / COMPLETED
```

用户取消的公开迁移为：

```text
CREATED / RUNNING / SUSPENDED / WAITING_USER / FAILED_RECOVERABLE / BLOCKED
    → CANCELLING
    → CANCELLED
```

以下 Run 是终态，不能被取消请求改写：

```text
COMPLETED
FAILED_TERMINAL
CANCELLED
TIMED_OUT
```

对 `COMPLETED`/`FAILED_TERMINAL`/`TIMED_OUT` 的取消必须拒绝；对已经
`CANCELLED` 的相同取消请求返回既有事实，不追加第二个 terminal event。

### 2.3 Reason 统一，Policy 不统一

所有 interruption 使用同一个 `InterruptionReason`：

```text
USER_CANCEL
RUN_TIMEOUT
STAGE_TIMEOUT
TOOL_TIMEOUT
PROVIDER_TIMEOUT
SERVICE_SHUTDOWN
```

确定性 policy 固定为：

| Reason | Action | Run outcome | 普通 `resume_run` |
| --- | --- | --- | --- |
| `USER_CANCEL` | `CANCEL_AT_SAFE_BOUNDARY` | `CANCELLED` | 否 |
| `RUN_TIMEOUT` | `TIME_OUT_AT_SAFE_BOUNDARY` | `TIMED_OUT` | 否 |
| `SERVICE_SHUTDOWN` | `SUSPEND_AT_SAFE_BOUNDARY` | `SUSPENDED` | 允许显式恢复 |
| `STAGE_TIMEOUT` | `DELEGATE_TO_DECISION` | 非自动终态 | 由 Decision 决定 |
| `TOOL_TIMEOUT` | `DELEGATE_TO_DECISION` | 非自动终态 | 由 Decision 决定 |
| `PROVIDER_TIMEOUT` | `DELEGATE_TO_DECISION` | 非自动终态 | 由 Decision 决定 |

因此 Tool/Provider timeout 不能无条件把整个 Run 标记为 `TIMED_OUT`；同样，Run
timeout 不能被错误汇总为 `COMPLETED`。

## 三、Safe Cancellation Boundary

### 3.1 可观察边界

Runtime 只允许在明确边界采取 interruption 动作：

```text
BEFORE_PLANNER
AFTER_PLANNER
BEFORE_TOOL
AFTER_TOOL
BEFORE_WORKFLOW_ACTIVATION
AFTER_FINALIZATION_BUNDLE
DURING_INTERRUPTIBLE_WAIT
```

`DURING_INTERRUPTIBLE_WAIT` 仅适用于声明为 `INTERRUPTIBLE` 的 Provider、搜索或
只读等待。其他操作只能在调用前或调用后观察请求。

### 3.2 不可拆分的原子区

以下区域内禁止完成 cancellation transition：

```text
SQLITE_TRANSACTION
ARTIFACT_DIGEST_COMMIT
IDEMPOTENCY_FINALIZATION
FILESYSTEM_ATOMIC_REPLACE
```

收到 cancel 不等于忽略请求；实现必须记住 durable intent，在原子区完成后第一个
安全边界停止新工作。事务中不得调用 Provider、Tool、文件 I/O 或 `await`。

## 四、Tool Cancellation Safety Class

每个可执行 capability 必须声明一类安全语义：

```text
INTERRUPTIBLE
  Provider wait、web search、长时间只读等待

BOUNDARY_ONLY
  filesystem write/move、artifact/checkpoint finalization

NON_CANCELLABLE_ONCE_COMMITTED
  message send、reservation、payment、remote mutation
```

`NON_CANCELLABLE_ONCE_COMMITTED` 不表示 Run 无法取消，而表示已提交的现实副作用
不能被 cancellation 叙述为“已撤销”。正确行为是：

```text
preserve committed effect evidence
prevent subsequent effects
flush partial-result truth
terminal CANCELLED
```

`CANCELLED` 因此不等于“没有任何效果发生”。公开 Snapshot/Artifact/Event 必须保留
取消前已经验证的产物和外部 reference。

## 五、终态、事件与 Resume

### 5.1 Durable events

D2 必须在同一 Run durable event stream 中增加：

```text
run_cancelling   # non-terminal
run_cancelled    # terminal, exactly once
run_timed_out    # terminal, exactly once
```

硬规则：

```text
CANCELLED Snapshot → exactly one run_cancelled
TIMED_OUT Snapshot → exactly one run_timed_out
CANCELLING Snapshot → no terminal cancellation claim yet
terminal event 后不得重新出现 active 状态事件
```

状态事实和关键 terminal event 应在同一 durable transaction 中提交；progress event
可以独立追加，但不能成为 terminal truth 的唯一事实源。

### 5.2 Resume

默认规则固定为：

```text
CANCELLED   → 普通 resume_run 拒绝，不自动恢复
TIMED_OUT   → 普通 resume_run 默认拒绝，不自动恢复
SUSPENDED by SERVICE_SHUTDOWN → 可按既有 ResumeValidator 显式恢复
PROCESS_CRASH → 沿用 v2.2/v2.3B 的 recoverable resume
```

以后若支持 `restart_cancelled_run`，必须创建新的 Run identity，并通过
`supersedes_run_id` 等 lineage 指向旧 Run；不得复活原 terminal Run。

## 六、进程、Fence 与客户端语义

durable intent 在进程死亡后仍有效：

```text
Worker A commits cancel intent
    ↓ process dies
Worker B acquires new fence
    ↓ reads intent before execution
does not resume/start new task
    ↓ safe flush
CANCELLED
```

旧 Worker 的任何后续 checkpoint、event、artifact 或 terminal write 必须被新 fence
拒绝。客户端断开只影响事件消费，不拥有 Runtime；它既不能丢失 durable cancel，
也不能隐式创建 cancel。

## 七、16-case Dataset / Oracle

固定 Dataset 为：

```text
C01 cancel before first tool
C02 cancel during provider wait
C03 cancel before filesystem write
C04 cancel immediately after effect commit
C05 cancel during Finalization Bundle
C06 duplicate cancel request
C07 cancel COMPLETED run
C08 cancel already CANCELLED run
C09 process dies after durable cancel intent
C10 new worker sees cancel and does not resume
C11 Run timeout during Planner/Provider
C12 Tool timeout delegates to Decision
C13 cancel multi-Workflow Run after A completed, B active
C14 cancel + client disconnect
C15 stale worker after cancel is fenced
C16 external committed effect + cancel
```

Dataset/Oracle 只证明合同可序列化、policy 完整、边界矩阵确定且期望唯一；它不证明
SQLite、AgentService、Provider 或 Tool 已经接线。

## 八、硬门禁与性能观测

以下任意非零都阻塞 v2.3D 收口：

```text
Post-cancel new side effect                    0
Duplicate cancel transition                   0
False CANCELLED before durable flush           0
Completed effect silently lost                 0
Cancelled Run auto-resumed                     0
Atomic transaction torn by cancellation        0
Terminal Snapshot/Event mismatch               0
Stale writer after cancel accepted             0
Cancel intent lost after process restart       0
Timeout misclassified as COMPLETED             0
```

以下 latency 先记录趋势，不在 D1 设置脱离环境的固定阈值：

```text
cancel_request_to_cancelling_ms
cancelling_to_terminal_ms
provider_cancellation_ms
tool_safe_boundary_ms
```

## 九、实施切片

```text
v2.3D-1 Contract / Dataset / Oracle
  ADR-0023
  interruption DTO / reason / policy / boundary matrix
  C01–C16 deterministic Dataset

v2.3D-2 Durable Cancellation Core
  AgentService.cancel_run()
  SQLite durable intent + idempotency/CAS/fence
  RunContext cancellation view/token
  CANCELLING/CANCELLED/TIMED_OUT projection and events

v2.3D-3 Propagation & Side-effect Safety
  Planner/Provider/Tool/Workflow boundary observation
  committed effect preservation
  no-new-task gate
  process restart / stale writer fault injection

v2.3D-4 Service / CLI / Real E2E Closeout
  CLI adapter and frontend contract
  real Provider cancellation/timeout smoke
  final freeze evidence
```

FastAPI、WebSocket、Provider failover、跨机器 cancellation、分布式 worker 协调和
Human Approval 不进入 v2.3D-1。

## 十、D1 验收证据

```text
Dataset / Oracle:       16/16 PASS
Dataset hash:           090adaf6a972f812e11990fe2b04e7736e1d74455e34ddcb10ad7c12bd55654c
Reason policy coverage: 6/6
Hard gate coverage:     10/10
DTO round-trip:         PASS
Oracle determinism:     PASS
Deterministic tests:    9/9 PASS
Related regression:     33 PASS
Architecture:           PASS
Contract Verification:  PASS
mypy:                    PASS
Runtime implementation: DEFERRED to D2/D3
Real E2E:               DEFERRED to D4
```

D1 完成后，ADR 状态保持 `Contract / Dataset / Oracle Frozen`，不能表述为 production
cancellation 已实现。真实 Cancel 按钮必须等待 D2/D3 完成后才能接入。

## 十一、D2 / D3 实现证据

v2.3D-2 已将 cancellation 从进程内信号提升为 durable Runtime fact：

```text
Durable intent / idempotency / CAS / fence        PASS
CANCELLING → CANCELLED / TIMED_OUT lifecycle      PASS
Terminal Snapshot/Event atomicity                 PASS
Restart rehydration and stale-writer rejection    PASS
```

v2.3D-3 在不引入第二套状态机的前提下完成执行传播：

```text
RunContext read-only CancellationView             PASS
Planner / Task / Workflow no-new-work gate        PASS
Provider interruptible wait                       PASS
Provider fallback after cancellation                0
Boundary-only current effect preservation         PASS
Post-cancel subsequent side effects                 0
Run timeout durable watchdog                      PASS
Provider/Tool timeout misclassified as Run timeout  0
False COMPLETED                                     0
Terminal Snapshot/Event mismatch                    0
```

实现保持以下职责分离：

```text
CancellationView       = 只读观察 durable intent
Runtime / Executor     = 只在声明的 safe boundary 停止新工作
CancellationCoordinator = 使用当前 fence 收敛 durable lifecycle
```

`RUN_TIMEOUT` watchdog 不使用 `asyncio.wait_for(runtime.run())` 撕裂 Runtime；它先写入
durable timeout intent，再等待 Provider/Tool/Workflow 的下一个安全边界。文件和其他
`BOUNDARY_ONLY` 操作若已开始，会完成当前原子操作并保留部分执行证据，然后阻止下一
副作用。`PROVIDER_TIMEOUT`、`TOOL_TIMEOUT` 仍由 Decision policy 处理，不会自动生成
`run_timed_out`。

在 D3 阶段，真实 Provider、CLI/前端 Cancel adapter 和端到端 latency evidence
仍留待 v2.3D-4；因此当时的确定性收口不应表述为真实环境 cancellation 已完成。
这些证据现已由下方 Final Closeout 节补齐。

## 十二、v2.3D Final Closeout（2026-08-11）

v2.3D-4a、v2.3D-4b 与 v2.3D-4c 已完成。本 ADR 现在冻结完整的
Cancellation / Timeout Runtime、CLI 与桌面客户端合同；真实 LocalAgentServiceClient、
FastAPI、WebSocket 和前端真实进程接线不属于本里程碑。

最终证据基线：

```text
implementation baseline: 7fcb5bb4
dataset / oracle:        16/16 PASS
dataset hash:            090adaf6a972f812e11990fe2b04e7736e1d74455e34ddcb10ad7c12bd55654c
D2 durable core:         PASS
D3 propagation:          PASS
D4a CLI contract:        PASS
D4c desktop contract:    PASS
```

D4b 保留真实 Provider 的原始结果，不将 Provider 基础设施错误改写为能力成功：

```text
D401 real Ollama cancellation:       PASS
D408 real Ollama Run timeout:        PASS
D407 real child-process SIGKILL:     PASS
D409 Provider timeout contrast:      PROVIDER_ERROR / Runtime Correctness PASS
Runtime Correctness:                 10/10
Hard invariant violations:           0
```

v2.3D 最终硬不变量全部为零：

```text
lost cancellation intent                  0
post-cancel new side effect               0
pending task started after cancel         0
duplicate cancellation transition        0
duplicate side effect                     0
false CANCELLED                           0
false COMPLETED after cancellation        0
committed effect/artifact lost            0
cancelled Run normal-resumed              0
stale writer accepted                     0
atomic transaction torn                   0
Run timeout reported COMPLETED            0
Provider timeout promoted to RUN_TIMEOUT  0
Snapshot / terminal-event mismatch        0
cancel-triggered provider fallback       0
client disconnect implicit cancellation  0
frontend optimistic CANCELLED             0
```

本次 closeout 的本地验证为：v2.3D 相关 pytest `50 passed`，D1 Dataset/Oracle
validator `16/16 PASS`，`mypy agent/interruption benchmarks/v23d` 通过（15 个文件），
桌面端 `npm run build` 通过，Architecture/Contract 相关测试通过。全量 pytest 为
`553 passed, 17 skipped, 1 environment failure`；唯一失败是 `test_web_fetch` 的沙箱
DNS 不可达，已作为环境异常保留，不影响 v2.3D Runtime 证据。全仓库 mypy 当前仍有
146 个既有类型错误，超出本 ADR 的 D4 closeout scope，不能将其表述为全仓库 mypy
通过。

完整冻结报告归档于：

```text
realtest_reports/v2.3/v23d_freeze.md
realtest_reports/v2.3/v23d_freeze.json
```
