# ADR-0025: Desktop Local Transport Contract（Desktop-1）

- 状态: Accepted — Desktop-1 Contract Frozen
- 日期: 2026-08-11
- 关联: ADR-0021（AgentService/Event Stream）、ADR-0023（Cancellation/Timeout）
- 后端基线: `2c4a68c8`（v2.3D Cancellation and Timeout closeout）

## 一、范围

Desktop MVP-2 通过一个本地 sidecar 接入已经冻结的 Python `AgentService`：

```text
React / Tauri UI
        ↓
LocalAgentServiceClient
        ↓
Local JSONL Transport
        ↓
Python Sidecar
        ↓
AgentService
```

本 ADR 只冻结本地传输 envelope、生命周期和重连所需的协议事实，不实现 sidecar、
Tauri glue、真实 `LocalAgentServiceClient` 或 UI 切换。当前 Desktop 仍使用
`MockAgentServiceClient`。

## 二、传输选择

第一版使用 stdin/stdout 上的 UTF-8 JSON Lines：

- 每个请求占一行，每个响应占一行；
- stdout 只允许协议响应，诊断日志写 stderr；
- 一行必须是完整 JSON object，最大编码长度为 1 MiB；
- 不使用 FastAPI、WebSocket、端口监听或隐式全局状态；
- JSON payload 只允许 JSON 类型，禁止 exception、file handle、callable、generator
  等进程内对象；
- transport `id` 只负责请求/响应相关性，不替代业务 DTO 中的 `request_id` 幂等身份。

协议版本标识为 `desktop-local-jsonl-v1`，由实现与证据报告固定记录；本阶段不在
每个 envelope 中重复发送版本字段。

## 三、请求 Envelope

请求的 wire 形状固定为：

```json
{"id":"req-123","method":"get_run","params":{"tenant_id":"tenant-1","user_id":"user-1","session_id":"session-1","run_id":"run-1","request_id":"lookup-1"}}
```

字段合同：

| 字段 | 合同 |
| --- | --- |
| `id` | 非空字符串；响应必须原样返回；不用于业务幂等 |
| `method` | 只能是下列八个方法之一 |
| `params` | JSON object；业务方法的字段由对应 AgentService DTO 验证 |

允许的方法固定为：

```text
health
start_run
get_run
resume_run
cancel_run
list_artifacts
read_events
shutdown
```

`health` 与 `shutdown` 的 `params` 必须为空 object。其他方法的 params 不在
transport 层重新实现业务语义；sidecar 必须将其转换为 ADR-0021 的 identity-complete
DTO，缺失 `tenant_id / user_id / session_id / request_id` 时拒绝请求，不能填入
default user、global session 或 current run。

## 四、响应 Envelope

成功响应：

```json
{"id":"req-123","ok":true,"result":{}}
```

失败响应：

```json
{"id":"req-123","ok":false,"error":{"code":"RUN_NOT_FOUND","message":"run was not found","retryable":false}}
```

`ok=true` 时只能出现 `id / ok / result`；`ok=false` 时只能出现
`id / ok / error`。error 至少包含 `code / message / retryable`，可以包含
`run_id / request_id / details`。错误必须使用稳定的 AgentService error taxonomy，
不能暴露 SQL、数据库路径、workspace 绝对路径、traceback、API key 或 provider
exception 类型。

响应 `id` 与请求不一致是 transport contract failure；客户端不得把不相关响应交给
调用方。

## 五、方法委派与生命周期

Python sidecar 的唯一业务路径是：

```text
decode JSONL
→ construct public request DTO
→ AgentService method
→ project public response DTO
→ encode JSONL
```

sidecar 不得直接调用 Orchestrator、WorkflowExecutor、SQLite、CheckpointStore、
CancellationStore 或自行 kill/取消 asyncio task。`health` 只报告 sidecar 与
AgentService 可接受请求的状态；`shutdown` 请求优雅关闭 AgentService 并在响应发送
后结束 sidecar。

客户端/sidecar 进程生命周期固定为：

```text
spawn
→ health
→ business calls
→ shutdown
→ EOF / process exit
```

stdin EOF 或客户端断开不得隐式取消 Run；Run 的取消只能通过 `cancel_run`，最终
状态只能由 durable Snapshot/Event 事实确认。

## 六、事件读取与重连

Desktop-1 只冻结轮询/读取语义，不在本阶段实现事件后台线程：

```text
read_events(params.after_sequence = N)
→ 返回 sequence_number > N 的持久化事件
```

客户端按 `event_id` 去重，并在事件读取后用 `get_run` 刷新 Snapshot；事件本身不能
让 UI 乐观地把 Run 变为 terminal。客户端断开、重新 spawn 或停止消费事件不得重新
执行 Workflow。cursor 过期必须返回稳定的 `EVENT_CURSOR_EXPIRED`，不能静默跳到最新
事件。

## 七、架构门禁与后续切片

Desktop 生产代码不得直接 import Runtime、Orchestrator、SQLite 或 CancellationStore：

```text
desktop → Runtime imports                 = 0
desktop → SQLite direct access             = 0
sidecar → Orchestrator direct calls       = 0
sidecar → CancellationStore direct writes = 0
```

后续切片固定为：

```text
Desktop-2  Python AgentService sidecar
Desktop-3  LocalAgentServiceClient
Desktop-4  Real UI wiring
Desktop-5  Real local E2E closeout
```

本 ADR 不授权加入认证、云同步、WebSocket、远程 REST、Pause、Approval 或新的
Cancellation/Timeout policy。

## 八、Desktop-1 验收证据

Desktop-1 必须满足：

```text
method allowlist / envelope validation     PASS
JSONL canonical round-trip                 PASS
request/response correlation                PASS
live object / non-finite payload rejection  PASS
stable public error envelope               PASS
Python protocol scoped tests                PASS
TypeScript desktop build                   PASS
```

Sidecar、真实本地进程通信和真实 Provider Desktop E2E 明确延后到 Desktop-2–5，不能
由本 ADR 的协议测试冒充已完成。
