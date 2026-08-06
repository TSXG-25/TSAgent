# ADR-0021: AgentService and Event Stream Contract

状态: Accepted — v2.3C-1 Contract / Dataset / Oracle

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

`StartRunRequest`、`ResumeRunRequest`、`RunLookupRequest` 和
`EventStreamRequest` 均必须携带：

```text
tenant_id
user_id
session_id
run_id
request_id
```

其中：

- `tenant_id + run_id` 是 durable lookup 的边界；
- `session_id` 是 Conversation 边界；
- `run_id` 是逻辑 Run 身份，不因一次消息或一次进程内函数调用改变；
- `request_id` 是服务调用幂等键，重复请求不得创建第二个 Run 或第二次外部
  副作用。

同一 `request_id` 搭配相同 request digest 必须返回同一逻辑结果；同一
`request_id` 搭配不同 digest 必须返回稳定的 `REQUEST_ID_CONFLICT`。服务不得
隐式回退到 default user、global session、current run 或 last workspace。

### 2.3 公开 DTO 与内部模型隔离

公开 DTO 位于 `agent/service/contracts.py`，目前包括：

- Request：`StartRunRequest`、`ResumeRunRequest`、`RunLookupRequest`、
  `EventStreamRequest`；
- Run：`RunHandle`、`RunSnapshot`；
- Projection：`ArtifactSummary`/`ArtifactView`、`ResumeSummary`、
  `FailureSummary`；
- Event：`RunEvent`。

`RunSnapshot` 只暴露 Run 状态、Workflow 进度、artifact 摘要、验证状态、
恢复摘要、失败摘要和 revision。不得暴露：

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
7. 客户端通过 `after_sequence` 重连时只读取后续事件，不重新执行 Workflow。

v2.3C-1 的 `EventOrderingOracle` 是纯验证器，不订阅 EventBus、不调用
Provider、不启动 Runtime。持久化事件表、慢消费者隔离和实际重连由
v2.3C-3 实现；内存流断开不能影响 Run 执行。

### 2.5 稳定错误分类

服务错误必须使用 `ServiceErrorCode`，不能把 SQLite exception、Provider
exception 或 Python traceback 直接暴露给适配器。当前合同至少冻结：

```text
INVALID_REQUEST
IDENTITY_REQUIRED
IDENTITY_MISMATCH
TENANT_SCOPE_VIOLATION
SESSION_SCOPE_VIOLATION
RUN_NOT_FOUND
REQUEST_ID_CONFLICT
DUPLICATE_REQUEST
EVENT_SEQUENCE_INVALID
EVENT_REPLAY_UNAVAILABLE
SERVICE_CLOSED
UNSUPPORTED_OPERATION
INTERNAL_MODEL_LEAK
```

错误只保存稳定 code、用户可理解的 message 和 JSON details；原始异常属于
内部 Diagnostics/FailureEvent，不属于公开 Service DTO。

## 三、实现阶段边界

### v2.3C-1：Contract / Dataset / Oracle（本 ADR）

已完成：

- public request/response/event DTO；
- stable service error taxonomy；
- event ordering/replay oracle；
- 16 个确定性 Dataset case 与 canonical dataset hash；
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

Dataset 位于 `benchmarks/v23c/`，共 16 例，覆盖：

```text
identity                  001–006
idempotency               007–008
dto                       009–010
event_ordering            011–013
event_replay              014
terminal_state            015–016
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
Dataset cases: 16
Dataset hash: 2bfad6b6be7649c8228a657f4ec3a6bd859d80559f16ee9bb69906ab6521be64
Oracle determinism: PASS
Contract validation: PASS
Concrete AgentService: deferred to v2.3C-2
Event persistence/reconnect: deferred to v2.3C-3
```
