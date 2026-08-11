# ADR-0026: Desktop LocalAgentServiceClient（Desktop-3）

- 状态: Accepted — Desktop-3 Implemented and Contract Verified
- 日期: 2026-08-11
- 关联: ADR-0021（AgentService/Event Stream）、ADR-0025（Desktop Local JSONL）
- 后端基线: `ca208ea1`（Desktop-2 Python AgentService sidecar）

## 一、范围

Desktop-3 将 React/Tauri 前端可以使用的公开 `AgentServiceClient` DTO 接到
Desktop-1/2 的本地 JSONL sidecar，但不修改页面组件的 Service 抽象，也不在本阶段
切换 Desktop 的组合根：

```text
AgentServiceClient
├── MockAgentServiceClient
└── LocalAgentServiceClient
         ↓
     LocalTransport
         ↓
   TauriSidecarBridge
         ↓
   Python sidecar
```

`LocalAgentServiceClient` 只做 camelCase public DTO 与 snake_case wire DTO 的转换、
稳定错误投影和事件批次投影。`TauriSidecarTransport` 只做 sidecar 进程的行写入、
stdout 行解析、request/response correlation、超时和生命周期；它不理解 Planner、
Workflow、SQLite、Artifact 内容或 CancellationStore。

## 二、显式身份

Local Client 构造时必须传入非空 `userId`：

```ts
new LocalAgentServiceClient(transport, { userId: "user-1" })
```

请求中的 `tenantId`、`sessionId`、`runId` 和业务 `requestId` 仍由每个 public request
提供。只读请求没有业务 request ID 时，Local Client 生成仅用于 lookup/cursor 的
transport-side business request ID；它不使用 default user、global session、current
run，也不在 Local 失败时切换 Mock。

## 三、Transport correlation 与失败语义

每个 wire request 有独立 transport `id`，pending 请求保存在：

```text
Map<transport_id, Promise resolver>
```

客户端必须支持并发请求和乱序响应。unknown/duplicate response 只能产生可观测的
protocol diagnostic，不得完成或拒绝另一条 pending request。以下情况会让受影响的
请求确定性失败：

- malformed stdout JSONL；
- sidecar 进程退出；
- request timeout；
- transport close；
- request write failure。

所有 pending promise 都必须在 sidecar exit 或 close 时结束；不得永久挂起，也不得
隐式创建 Mock client。

## 四、生命周期

Tauri host 通过 `TauriSidecarBridge` 注入真正的 child-process 实现。生命周期为：

```text
first request
→ bridge.spawn()
→ health（由调用方显式执行）
→ business requests
→ shutdown
→ transport.close()
```

`LocalAgentServiceClient.health()` 不自动隐藏启动失败；调用方可以将稳定的
`PROVIDER_UNAVAILABLE` / `INTERNAL_ERROR` 显示为 Backend unavailable。`shutdown()`
只发送 `shutdown` RPC 并关闭 transport，不创建 cancellation intent；EOF、窗口关闭或
事件读取停止同样不等于 `cancel_run`。

## 五、DTO 与事件投影

Python sidecar 的 public DTO 投影规则固定为：

- `CREATED → pending`、`RUNNING → active`、`CANCELLING → cancelling`；
- `CANCELLED → cancelled`、`TIMED_OUT → timed_out`，不得互相转换；
- `FAILED_* → failed`、`SUSPENDED/WAITING_USER/BLOCKED → blocked`；
- Artifact 只保留 opaque `reference`，不暴露绝对路径或 SQLite payload；
- RunOutput、failure summary 和 verifier summary 只接受 JSON public projection；
- `readEvents(afterSequence)` 将 `-1` 初始游标规范化为 sidecar 合同的 `0`，并拒绝
  返回不满足 exclusive cursor 的事件；
- 每个批次按 `sequence_number` 升序排列，并按 `event_id` 去重；冲突的 event ID
  与 sequence 被视为 invalid response。

`cancelRun()` 收到 `CANCELLING` 时必须原样投影；只有之后的 Snapshot/Event 事实才
能让 UI 看到 `CANCELLED`。`TIMED_OUT` 保持独立终态。

## 六、架构门禁

Desktop-3 生产 TypeScript 路径必须满足：

```text
LocalAgentServiceClient → Runtime / SQLite / Orchestrator imports = 0
TauriSidecarTransport   → AgentService business imports          = 0
React components        → LocalTransport / Tauri APIs             = 0
Local failure           → implicit Mock fallback                  = 0
```

Desktop-4 才通过明确配置将组合根从 Mock 切换到 Local；本 ADR 不授权真实 UI 接线、
FastAPI、WebSocket、认证或新的 Runtime 状态。

## 七、验收证据

Desktop-3 contract suite 覆盖：

```text
health round-trip                                  PASS
concurrent out-of-order correlation                PASS
unknown/duplicate response isolation               PASS
malformed stdout rejection                         PASS
sidecar exit pending rejection                    PASS
bounded timeout                                    PASS
Run/Artifact/Event DTO projection                  PASS
exclusive cursor + batch dedup/order               PASS
start/resume mapping                               PASS
CANCELLING / TIMED_OUT preservation                PASS
stable ServiceError mapping                        PASS
no implicit Mock fallback                          PASS
shutdown closes transport without cancellation    PASS
```

页面仍由 `MockAgentServiceClient` 驱动；真实 Tauri 组合根和真实本地 Provider E2E 留在
Desktop-4/5，不由本 ADR 的 client contract 测试冒充完成。
