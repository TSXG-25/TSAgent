# ADR-0021: AgentService and Event Stream Contract

状态: Accepted — v2.3C-1 Contract / Dataset / Oracle Frozen

日期: 2026-08-06

## 一、上下文

TSAgent 已经拥有明确的 Runtime Context、Durable SQLite Store 和 Run-level
Resume 能力，但 CLI、未来的 macOS 客户端、REST 和 SDK 仍不应直接依赖
`UniversalAgent`、`RunCheckpoint`、`RunResumeIndex`、SQLite row 或内部
`EventBus`。

本 ADR 冻结一个纯 Python 服务边界。它先定义稳定的请求、响应、错误和事件
语义，具体实现分到 v2.3C-2；HTTP/WebSocket、Cancellation、Approval 和
Provider Retry 不属于本 ADR。

## 二、决策

### 2.1 服务边界

公开服务实现必须满足 `agent.service.contracts.AgentService` Protocol：

```python
class AgentService(Protocol):
    async def start_run(self, request: StartRunRequest) -> RunHandle: ...
    async def get_run(self, request: RunLookupRequest) -> RunSnapshot: ...
    async def resume_run(self, request: ResumeRunRequest) -> RunHandle: ...
    async def list_artifacts(
        self, request: RunLookupRequest
    ) -> tuple[ArtifactView, ...]: ...
    def stream_events(
        self, request: EventStreamRequest
    ) -> AsyncIterator[RunEvent]: ...
```

`get_run` 和 `list_artifacts` 使用 identity-complete request DTO，而不是只
接受一个裸 `run_id`。这是对早期 `get_run(tenant_id, run_id)` 草案的收口：
所有服务请求都必须携带完整身份边界。

服务内部调用关系固定为：

```text
CLI / REST / macOS / SDK
            ↓
       AgentService
            ↓
ApplicationContext / SessionContext / RunContext
            ↓
      Existing Runtime
            ↓
    SqliteRuntimeStore
```

AgentService 只负责身份解析、Context 生命周期、现有 Runtime 调用、公开
DTO 投影和事件流访问。它不得重新实现 Planner、Executor、ResumeAction
判定、Workflow 状态机或 SQLite 写事务。

### 2.2 身份与幂等

`StartRunRequest` 必须携带 `tenant_id / user_id / session_id / request_id`；
Service 为新 Run 分配 `run_id`。`ResumeRunRequest`、`RunLookupRequest` 和
`EventStreamRequest` 还必须携带已存在的 `run_id`。所有请求都必须携带：

```text
tenant_id
user_id
session_id
request_id
```

其中 `run_id` 对 Start request 是返回的 RunHandle 字段，对其他 request 是必需的
lookup identity；客户端提交的可选 replay hint 不参与 start request digest。

其中：

- `tenant_id + session_id + run_id` 是 durable lookup 的边界；
- `session_id` 是 Conversation 边界；
- `run_id` 是服务返回的逻辑 Run 身份，不因一次消息或一次进程内函数调用改变；
- `request_id` 是 durable 的 start/resume 幂等身份，不能只存在于进程内 map；
- `request_id` 的幂等判定必须包含 `tenant_id` 和 canonical request digest。

`start_run()` 的合同固定为：

```text
相同 tenant_id + request_id + 相同请求 digest
    → 返回同一个 run_id / RunHandle，不创建第二个 Run

相同 tenant_id + request_id + 不同请求 digest
    → IDEMPOTENCY_CONFLICT

不同 tenant_id + 相同 request_id
    → 彼此独立
```

`start_run()` 返回的是已持久化、可通过 `get_run()` 立即查询的 RunHandle，不是
Provider 已开始或 Runtime 已完成执行的承诺。服务不得隐式回退到 default user、
global session、current run 或 last workspace。

### 2.3 公开 DTO 与内部模型隔离

公开 DTO 位于 `agent/service/contracts.py`，目前包括：

- Request：`StartRunRequest`、`ResumeRunRequest`、`RunLookupRequest`、
  `EventStreamRequest`；
- Run：`RunHandle`、`RunSnapshot`；
- Projection：`ArtifactSummary`/`ArtifactView`、`ResumeSummary`、
  `FailureSummary`；
- Event：`RunEvent`。

`RunSnapshot` 是稳定的公开投影，至少固定暴露：

```text
tenant_id
session_id
run_id
request_id
status
revision
created_at
updated_at
active_workflow
completed_workflows
pending_workflows
verifier_summary
resume_summary
failure_summary
```

同一个 Run 的 `revision` 只能单调递增，terminal 状态不能静默回到 active；一次
`get_run()` 返回的全部字段必须来自同一个 durable snapshot。未找到和跨 tenant 的
身份不匹配对外都可以稳定返回 `RUN_NOT_FOUND`，避免泄漏该 Run 属于另一个 tenant；
只有请求已绑定到当前 scope 但显式身份字段互相矛盾时才返回 `IDENTITY_MISMATCH`。

`RunSnapshot` 不得暴露：

```text
ExecutionPlan
RunCheckpoint 完整 payload
RunResumeIndex 数据库结构
Planner state
SQLite row
EventBus handler
```

所有 DTO 都是 frozen dataclass。事件 payload 和失败详情只接受 JSON-shaped
数据；HTTP response、file handle、callable、generator、exception 等 live
object 必须在 DTO 边界被拒绝。DTO 通过 `to_dict()`/`from_dict()` 进行稳定
round-trip，不把内部模型作为字段类型传出。

`ResumeSummary` 沿用已冻结的安全决策边界：

```text
ALLOW                 → action 必须存在
REQUIRE_CLARIFICATION → action 必须为空
REJECT                → action 必须为空
```

公开层只展示摘要，不重新计算 `ResumeAction`。

Service 的 `resume_run` 只负责身份、生命周期和委派：

```text
ResumeRunRequest
    → 验证 scope
    → 调用 RunResumeCoordinator
    → 返回 RunHandle / RunSnapshot / ResumeSummary
```

Service 不自行判断 `RESUME_EXACT`、`REPLAY_FROM_STAGE`、Artifact 是否安全或副作用
是否可以重放。状态矩阵固定为：

```text
BLOCKED / INTERRUPTED / FAILED_RECOVERABLE
    → 委派 Coordinator
COMPLETED
    → ALREADY_COMPLETED，不创建执行者
RUNNING
    → RUN_ALREADY_ACTIVE，不创建第二个执行者
FAILED_TERMINAL / REJECTED / 不可恢复
    → RESUME_NOT_ALLOWED 或 REQUIRE_CLARIFICATION
```

相同 `resume_request_id`（当前 DTO 中对应 resume request 的 `request_id`）重试必须
返回同一 resume fact，不得启动两个 Worker。

### 2.3.1 Artifact 公开安全边界

`ArtifactSummary` 只允许公开以下 metadata：

```text
artifact_id
run_id
artifact_type
display_name
reference
digest
size
verified
producer
created_revision
```

`reference` 是 Service 内部可解析的不透明标识，不等于可信文件系统路径；前端不能
提交任意本地 path 让 Service 读取。内容访问只能按 `artifact_id` 进行，不能提供
`GET /files?path=...` 语义。跨 Run、跨 tenant 的 reference 默认拒绝，未验证 Artifact
必须明确 `verified=false`。

### 2.4 事件流语义

`RunEvent` 至少包含：

```text
event_id
sequence_number
tenant_id
session_id
run_id
workflow_id
stage_id
task_id
event_type
timestamp
payload
run_revision
```

同一 Run 的持久化事件必须满足：

1. `sequence_number` 从 1 开始并连续递增；
2. `event_id` 在同一 Run 内唯一；
3. `run_revision` 不得倒退；
4. tenant/session/run identity 必须与读取请求一致；
5. `run_completed`、`run_failed` 或 `run_blocked` 是明确终态事件；
6. 终态事件之后不得追加事件；
7. `after_sequence = N` 只返回 `sequence_number > N` 的事件；
8. `event_id` 是稳定身份，客户端按 `event_id` 去重；
9. 读取语义是 at-least-once readable，不承诺 exactly-once delivery；
10. terminal event 在保留窗口内可 replay；
11. cursor 超出保留范围返回稳定的 `EVENT_CURSOR_EXPIRED`，不能静默从最新事件开始；
12. 客户端断开、停止读取或重连不会取消、暂停或重启 Run；
13. 客户端通过 `after_sequence` 重连时只读取后续事件，不重新执行 Workflow。

v2.3C-1 的 `EventOrderingOracle` 是纯验证器，不订阅 EventBus、不调用
Provider、不启动 Runtime。持久化事件表、cursor retention、慢消费者隔离和实际
重连由 v2.3C-3 实现；本阶段只冻结 replay 语义，内存流断开不能影响 Run 执行。

### 2.5 稳定错误分类

服务错误必须使用 `ServiceErrorCode`，不能把 SQLite exception、Provider
exception 或 Python traceback 直接暴露给适配器。当前合同至少冻结：

```text
INVALID_REQUEST
IDENTITY_MISMATCH
RUN_NOT_FOUND
RUN_ALREADY_ACTIVE
ALREADY_COMPLETED
RESUME_NOT_ALLOWED
IDEMPOTENCY_CONFLICT
EVENT_CURSOR_EXPIRED
CURSOR_INVALID
STORE_BUSY
PROVIDER_UNAVAILABLE
INTERNAL_ERROR
```

所有公开错误 DTO 至少包含 `code / message / retryable / run_id? / request_id /
details`。`STORE_BUSY`、`PROVIDER_UNAVAILABLE` 可以标记 `retryable=true`；身份错误、
幂等冲突、非法状态和 cursor 过期通常不可自动重试。details 不得泄漏数据库路径、
SQL、Provider secret、Python traceback 或其他 tenant/session/run 身份。原始异常属于
内部 Diagnostics/FailureEvent，不属于公开 Service DTO。

## 三、实现阶段边界

### v2.3C-1：Contract / Dataset / Oracle（本 ADR）

已完成：

- public request/response/event DTO；
- stable service error taxonomy；
- event ordering/replay oracle；
- 32 个确定性 Dataset case 与 canonical dataset hash；
- DTO round-trip、identity rejection、request digest 和 internal-model
  leakage 规则。

本阶段不声称 concrete AgentService 已接入 Runtime。

### v2.3C-2：AgentService Core

实现纯 Python Service，接入现有 Context/Runtime/SQLite path，并把 CLI 改成
Service adapter。不得创建第二套 Planner、Executor 或 Run state machine。

### v2.3C-3：持久事件与重连

增加事件持久化、`after_sequence` replay、慢消费者隔离和客户端断开测试。证明
客户端断开只影响事件读取，不影响 Runtime，也不触发重复执行。

### v2.3C-4：真实 Provider 与收口

只做少量真实 API E2E：创建并完成 Run、通过 Service 恢复中断 Run、客户端断开
后重连读取状态/事件，以及重复 `request_id` 不创建第二个 Run。

Cancellation、Timeout、Pause、Human Approval、Provider Retry/Failover 和
FastAPI 适配器属于后续阶段，不提前塞入 C-1/C-2。

## 四、C-1 Dataset / Oracle 验收

Dataset 位于 `benchmarks/v23c/`，共 32 例，覆盖：

```text
identity                  001–006, 027
idempotency               007–008, 017, 028
dto                       009–010
start_lifecycle           018
snapshot                  019–021
event_ordering            011–013
event_replay              014, 022–023
terminal_state            015–016
artifact_scope            024
resume                    025–027, 029–030
lifecycle                 031
errors                    032
```

执行：

```bash
python -B -m benchmarks.v23c.validate
```

C-1 门槛：

```text
DTO round-trip                         100%
Identity validation                    100%
Same request digest idempotency       100%
Different digest conflict             100%
Public projection leakage             0
Event ordering oracle                 100%
Event replay oracle                   100%
Terminal event oracle                 100%
StartHandle persistence contract      100%
Snapshot revision / terminal guard   100%
Artifact scope isolation              100%
Resume state matrix                   100%
Error sanitization                    100%
Oracle determinism                    PASS
```

## 五、后续整体服务门槛

这些指标不在 C-1 单独宣称完成，留给 C-2/C-3/C-4：

```text
Duplicate start request creates extra Run       0
Run snapshot projection correctness             100%
Client disconnect affects Runtime               0
Resume through Service correctness              100%
CLI direct Runtime dependency imports           0
Process restart Service rehydration             PASS
```

## 六、C-1 证据

```text
Dataset cases: 32
Dataset hash: 7cf52067c7af4a9217aceb70ef600fb5e79638c4ed77a63dce08f6438c416e6a
Oracle determinism: PASS
Contract validation: PASS
Concrete AgentService: deferred to v2.3C-2
Event persistence/reconnect: deferred to v2.3C-3
```
