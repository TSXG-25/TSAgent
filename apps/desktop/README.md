# TSAgent Studio

TSAgent Studio 是 TSAgent v2.3D-4c 的 Contract-driven Mock Console。当前版本使用 `MockAgentServiceClient`，验证 ADR-0021 的公开 DTO 与前端交互契约，不直接依赖 Python Runtime 内部对象。

## Local development

```bash
npm install
npm run dev
```

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
- 从请求输入创建一个新的 Mock Run。

## 前端边界

页面只依赖 `src/types/service.ts` 中的 `AgentServiceClient`，当前由 `src/service/mockAgentService.ts` 实现。后续接入 C-4 AgentService 时，新增 `LocalAgentServiceClient`（Tauri sidecar / JSON Lines 或其他 LocalTransport），不修改页面组件。

`src/service/localTransport.ts` 只定义未来 sidecar 的调用/关闭 seam；本轮不会启动本地进程、监听端口或引入 FastAPI。

`RunSnapshot`、`ArtifactSummary` 和 `RunEvent` 是稳定的公开 DTO。Artifact 内容当前仅允许 Mock preview；客户端不提交任意本地路径，也不读取 SQLite、checkpoint payload 或 Runtime 内部对象。

## 边界

原始展示剧本仍在 `src/mocks/runData.ts`，但页面不直接导入它；Mock Adapter 负责把剧本转换为 ADR-0021 DTO，再由 view mapper 投影成界面模型。

当前不包含真实 Runtime/REST 调用、Tauri 壳、Pause、拖拽 Workflow 编辑器或真实场景验证。D4c 只冻结桌面客户端与 AgentService 的取消 DTO/状态契约；真实 Provider 取消证据属于已冻结的 D4b。

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
