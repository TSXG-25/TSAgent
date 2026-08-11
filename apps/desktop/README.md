# TSAgent Studio

TSAgent Studio 是 TSAgent v2.3D-4c 的 Contract-driven Desktop Console。Desktop-4a
通过显式的 `mock | local` composition root 选择数据源；页面始终只依赖
`AgentServiceClient` 公开 DTO，不直接依赖 Python Runtime 内部对象。

## Local development

```bash
npm install
npm run dev
```

默认使用 Mock client。切换到本地 sidecar 时必须显式配置：

```bash
VITE_AGENT_SERVICE_MODE=local \
VITE_TSAGENT_TENANT_ID=tenant-local \
VITE_TSAGENT_USER_ID=user-local \
VITE_TSAGENT_SESSION_ID=session-desktop-local \
npm run dev
```

`VITE_TSAGENT_SESSION_ID` 可选；未提供时在一次 App composition-root 生命周期内生成一次，
不会在每次创建 Run 时变化。local 模式需要 Tauri host 注入
`window.__TSAGENT_SIDECAR_BRIDGE__`。健康检查失败或配置缺失只显示 Backend unavailable，
绝不会自动回退到 Mock。

构建静态产物：

```bash
npm run build
```

## 当前覆盖

- Run 列表与 Run Inspector；
- Conversation / Workflow / Stage / Task 状态；
- Checkpoint 与 `RESUME_EXACT` 请求交互；
- Artifact metadata、opaque reference、源码预览占位与 Verifier 状态；
- 使用 exclusive `afterSequence` 的 append-only 事件时间线；
- `event_id` 去重、`sequence_number` 排序与断开后续读模拟；
- `cancelRun()` 的显式取消请求、稳定 request ID 与幂等错误投影；
- `ACTIVE → CANCELLING → CANCELLED` 的权威 Snapshot/Event 状态展示；
- `TIMED_OUT` 状态展示，以及取消/超时后已验证部分产物和未执行任务的保留摘要；
- 取消按钮只在 `ACTIVE` 显示，断开、卸载或事件重连不会隐式调用取消；
- `RUN_NOT_FOUND`、幂等冲突、非法 Resume、cursor 过期等 Service Error 展示；
- 从请求输入创建一个新的 Run；运行时由 composition root 显式决定。

## 前端边界

页面只依赖 `src/types/service.ts` 中的 `AgentServiceClient`，组合根由
`src/service/clientFactory.ts` 显式选择 `MockAgentServiceClient` 或
`LocalAgentServiceClient`。Desktop-3 提供 Local client 与 Tauri transport；Desktop-4a
负责 health bootstrap、稳定 identity 和失败可见性。

`src/service/localTransport.ts` 定义 JSONL protocol seam，
`src/service/tauriSidecarTransport.ts` 定义 request correlation、超时、进程退出和关闭
语义，`src/service/localAgentServiceClient.ts` 负责 public DTO 到 wire DTO 的映射。
Tauri/Rust host 通过 `TauriSidecarBridge` 注入实际 child-process 实现；本轮不引入
FastAPI、WebSocket，也不在页面中隐式 fallback 到 Mock。

### Desktop-1 Local JSONL Contract

Desktop-1 已冻结 `docs/adr/0025-desktop-local-transport-contract.md` 定义的
stdin/stdout JSON Lines envelope。Desktop-2 已实现 Python sidecar，Desktop-3 已实现
真实 Local client contract；在 `VITE_AGENT_SERVICE_MODE=local` 时页面通过该入口运行。协议只允许 `health`、`start_run`、`get_run`、`resume_run`、`cancel_run`、
`list_artifacts`、`read_events`、`shutdown` 八个方法，业务身份与幂等仍由
AgentService DTO 负责。由于当前协议尚无 `list_runs`，local client 的 Run 列表只包含
当前 client 实例已经通过 `getRun` 观察到的 Run；历史 Run 浏览必须在后续公开
AgentService 查询合同中实现，前端不得绕过 Service 读取 SQLite。

Desktop-2 sidecar 的本地启动入口为：

```bash
python -m agent.service.local_sidecar \
  --database /path/to/runtime.sqlite \
  --workspace-root /path/to/workspace
```

sidecar 的 stdout 只输出 JSONL 响应，诊断信息输出 stderr。

`RunSnapshot`、`ArtifactSummary` 和 `RunEvent` 是稳定的公开 DTO。Artifact 内容当前仅允许 Mock preview；客户端不提交任意本地路径，也不读取 SQLite、checkpoint payload 或 Runtime 内部对象。

## 边界

原始展示剧本仍在 `src/mocks/runData.ts`，但页面不直接导入它；Mock Adapter 负责把剧本转换为 ADR-0021 DTO，再由 view mapper 投影成界面模型。

当前仍不包含 Tauri Rust 壳、REST、Pause、拖拽
Workflow 编辑器或真实 Provider Desktop E2E。D4c 只冻结桌面客户端与 AgentService
的取消 DTO/状态契约；真实 Provider 取消证据属于已冻结的 D4b。

## D4c contract checklist

The UI boundary is intentionally fact-driven:

| Case | Expected contract |
| --- | --- |
| UI01 | `ACTIVE` enables one `cancelRun()` request. |
| UI02 | `CANCELLING` disables the button and is not rendered as `CANCELLED`. |
| UI03 | `run_cancelled`/Snapshot moves the view to `CANCELLED`. |
| UI04 | `TIMED_OUT` is shown as its own terminal state. |
| UI05 | Verified partial artifacts remain visible after interruption. |
| UI06 | Event-stream disconnect or component unmount does not call `cancelRun()`. |
| UI07 | Repeated cancellation uses the same Run-scoped request ID. |
| UI08 | Terminal Runs do not expose a cancel action. |

`npm run build` is the current automated contract gate; this small MVP has no browser-test runner yet. The TypeScript build verifies that every future `AgentServiceClient` adapter implements `cancelRun()` and that the public DTOs remain free of Runtime/SQLite imports.
