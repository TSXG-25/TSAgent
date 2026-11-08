# Desktop 前端 ↔ AgentService 后端对接文档

日期：2026-11-08 · 适用代码：`apps/desktop/src/service/*`、`agent/service/local_protocol.py`、`agent/service/local_sidecar/*`、`agent/service/contracts.py`
读者：要在前端或后端新增、修改、排查接口的模型或工程师。

本文以**当前代码**为准，所有事实都附带文件位置。`docs/contracts/service-wire.md` 末尾的 "Traps already hit" 仍然有效，但该文档有几处与现状不符（见 §10）。技术债背景见 `docs/quality/desktop-frontend-debt.md`。标注 **[假设]** 的条目未经运行验证。

---

## 1. 架构总览

```
React 页面 / 工作台组件
   │  只依赖 DesktopAgentServiceClient（types/service.ts:970，camelCase DTO）
   ▼
RunController（features/runs/runController.ts）── Run 生命周期、cursor、长轮询
   │
   ▼
clientFactory.createAgentServiceClient()（service/clientFactory.ts:368）
   ├─ mock   → MockAgentServiceClient（内存，service/mockAgentService.ts）
   ├─ 配置缺失 → UnavailableDesktopAgentServiceClient（每个方法都 throw）
   └─ local  → LocalAgentServiceClient（camel ↔ snake 映射，service/localAgentServiceClient.ts）
                 ├─ 有 window.__TSAGENT_SIDECAR_BRIDGE__ → TauriSidecarTransport（stdio JSONL）
                 └─ 没有 bridge                          → HttpLocalTransport（POST /rpc）
                                   │
══════════════════════════ 进程边界 ═══╪═══════════════════════════════════
                                   ▼
python -m agent.service.local_sidecar [--http PORT]（local_sidecar/__main__.py）
   ├─ server.serve()        stdio JSONL
   └─ run_http_sidecar()    POST /rpc、GET /events（http_server.py）
                 │
                 ▼
SidecarDispatcher（dispatcher.py:30）── 方法表 → XxxRequest.from_dict(params)
                 │
                 ▼
AgentService（service.py）→ SQLite runtime store / Runtime
```

职责划分：

| 层 | 负责 | 不负责 |
|---|---|---|
| 页面 | 展示、用户手势 | 拼 wire 字段、读 SQLite、选 Resume 策略 |
| `LocalAgentServiceClient` | camel→snake、注入 `user_id`、校验响应 DTO 形状、错误投影 | 业务规则 |
| Transport | 封包、id 关联、超时、进程生命周期 | DTO 语义 |
| Dispatcher | 方法白名单、`from_dict` 构造请求 DTO、异常→错误信封 | 业务决策 |
| AgentService | 身份/作用域校验、幂等、状态机、持久化 | 传输 |

---

## 2. 运行模式与启动

### 2.1 前端环境变量（`apps/desktop/.env` / Vite）

| 变量 | 作用 | 默认 |
|---|---|---|
| `VITE_AGENT_SERVICE_MODE` | `mock` 或 `local`；其他值返回 Unavailable 客户端 | `mock` |
| `VITE_TSAGENT_TENANT_ID` / `VITE_TSAGENT_USER_ID` | local 模式必填，缺失时返回 Unavailable | — |
| `VITE_TSAGENT_SESSION_ID` | 页面加载时的初始 session | 每次加载生成一次 |
| `VITE_TSAGENT_SIDECAR_URL` | HTTP 模式的 sidecar 地址 | `http://127.0.0.1:8765` |
| `VITE_TSAGENT_SIDECAR_CMD` / `_ARGS` / `_CWD` | Tauri 模式下 spawn 的命令 | `python3` / `-u -m agent.service.local_sidecar` |

规则（`clientFactory.ts:321-357`）：local 模式配置不全时**不会**回退到 Mock，所有调用都会抛出 `INVALID_REQUEST` 或 `PROVIDER_UNAVAILABLE`。

### 2.2 后端 sidecar 参数（`local_sidecar/lifecycle.py:19`）

| 参数 | 环境变量 | 必填 |
|---|---|---|
| `--database PATH` | `TSAGENT_RUNTIME_DB` | 否；默认 `~/.tsagent/runtime.db`（父目录自动创建），会展开 `~` |
| `--workspace-root PATH` | `TSAGENT_WORKSPACE_ROOT` | 否 |
| `--writer-id ID` | `TSAGENT_WRITER_ID` | 否 |
| `--http [PORT]` | — | 否；加上后走 HTTP，不加走 stdio |

所有 Runtime 的 `print` 都被重定向到 stderr，stdout 只输出协议内容（`__main__.py:29`）。

### 2.3 三种组合

| 场景 | 前端 | 后端 |
|---|---|---|
| 纯 UI 开发 | `npm run dev`（mock） | 无 |
| 浏览器联调（最常用） | `VITE_AGENT_SERVICE_MODE=local VITE_TSAGENT_TENANT_ID=tenant-local VITE_TSAGENT_USER_ID=user-local npm run dev` | `python -m agent.service.local_sidecar --database <db> --workspace-root <dir> --http 8765` |
| Tauri 桌面 | `npm run tauri dev`；`tauriBridge.ts` 在 `__TAURI_INTERNALS__` 存在时注入 bridge | Rust `sidecar_spawn` 启动 `VITE_TSAGENT_SIDECAR_CMD/ARGS`（`src-tauri/src/main.rs:21`） |

**注意**：`--database` 已有默认值（`~/.tsagent/runtime.db`），Tauri 默认 `ARGS` 可以直接启动 sidecar；要用别的库路径时通过 `VITE_TSAGENT_SIDECAR_ARGS` 或 `TSAGENT_RUNTIME_DB` 覆盖。

---

## 3. 传输层

### 3.1 信封（两种传输共用）

请求：`{"id": "<非空字符串>", "method": "<白名单方法>", "params": { ... }}`，三个键必须齐全，不能有多余键（`local_protocol.py:280`）。`health`、`shutdown` 不接受 params。

成功：`{"id": ..., "ok": true, "result": <JSON>}`
失败：`{"id": ..., "ok": false, "error": {"code", "message", "retryable", "run_id"?, "request_id"?, "details"?}}`

限制：单行不超过 1 MiB（`MAX_JSON_LINE_BYTES`）；禁止 NaN/Infinity；result 里只能有 JSON 值，出现对象泄漏会报 `INTERNAL_MODEL_LEAK`。

协议版本：`health` 返回 `{"status":"ok","protocol_version":"desktop-local-jsonl-v1","service":"ready"}`。

### 3.2 stdio JSONL（Tauri）

- 每行一个信封，响应顺序不保证，靠 `id` 关联（`tauriSidecarTransport.ts`）。
- 单请求超时默认 30s（`DEFAULT_REQUEST_TIMEOUT_MS`）。
- 响应信封会被严格校验（`decodeLocalRpcResponse`，`exactKeys`），校验失败时整个 transport 进入失败态。
- 进程退出后，所有挂起请求以 `PROVIDER_UNAVAILABLE` 失败，transport 不会自动重启。

### 3.3 HTTP（浏览器开发态）

- `POST /rpc`，body 是请求信封。正常时返回 HTTP 200，body 是响应信封（**业务失败也是 200 + `ok:false`**）。
- 请求信封非法时返回 HTTP 400，body 是 `{"error": "..."}`；dispatcher 内部超时（300s）或异常时返回 500。
- 前端 `fetch` 超时 30s（`httpTransport.ts:20`）。`subscribe_events` 最长会挂起约 8s，在这个范围内。
- CORS 设置为 `Access-Control-Allow-Origin: *`（`http_server.py:42`），没有鉴权，见 §10 风险项。
- `GET /events?tenant_id&session_id&user_id&run_id&after` 是一个 SSE 流（0.4s 轮询，15s 心跳，Run 终态后发送 `event: end`）。**前端目前没有使用它**：`HttpLocalTransport.subscribeEvents` 没有调用方，Run 事件走的是 `subscribe_events` 长轮询。

---

## 4. 身份、定位符与幂等

- 每个业务方法的 params 都要带 `tenant_id`、`user_id`、`session_id`、`request_id`。例外：`list_sessions` 不需要 `session_id`；`health`、`shutdown`、`list_directory`、`pick_folder`、`list_capabilities`、`list_skills`、`list_workflows`、`web_search` 不经过请求 DTO（见 §6.3）。
- `user_id` 由 `LocalAgentServiceClient` 从 `identity.userId` 注入，页面不用传。
- `tenant_id` 是身份：客户端 `assertIdentity` 只校验 tenant（`localAgentServiceClient.ts:1243`）。
- `session_id` 是**定位符**，不是身份。可以跨 session 读取 Run 和事件；读取一个当前客户端没见过的 Run 时必须传它所属的 `session_id`。
- `request_id` 是幂等键：相同的 `tenant_id + request_id` 加相同 params 会重放已存储的结果，params 不同会返回 `IDEMPOTENCY_CONFLICT`。**每个用户手势生成一个新的** `request_id`。客户端内部生成的格式是 `desktop-<operation>-<uuid>`，页面用的是 `requestIdForRun()`（`app/ui.tsx:93`，每次调用返回新的 uuid）。
- 当前 session 的切换：`LocalAgentServiceClient.setSession()` 会原地修改 `identity.sessionId`（`:552`）。不传 session 的方法（例如 `listRuns()`）用的就是这个值。这是可变的全局状态，调用方要清楚自己读到的是哪个 session（见技术债文档 §2.5）。

---

## 5. 命名与参数解析规则

- 页面层用 camelCase，wire 层用 snake_case，转换只在 `LocalAgentServiceClient` 里做，每个方法手写一次。
- 后端 `XxxRequest.from_dict` 是**宽松**解析，例如 `StartRunRequest.from_dict`（`contracts.py:300`）：
  - **未知键会被直接忽略**：字段名写错不会报错，只是该字段静默丢失；
  - 缺失的字符串字段会被填成 `""`，再由 dataclass 的 `__post_init__` 校验必填项，失败时报 `INVALID_REQUEST`；
  - `from_dict` 抛出的 `TypeError/ValueError/KeyError` 统一变成 `INVALID_REQUEST: request params do not match the AgentService contract`（`dispatcher.py:548`），原始原因不会返回给前端，要看 sidecar 的 stderr。
- 因此新增字段时**必须**两侧同时改，并用离线测试断言该字段确实到达了 Service（见 §9）。

---

## 6. 方法目录（56 个）

必填参数省略四件套（`tenant_id/user_id/session_id/request_id`），`?` 表示可选。参数清单从 `agent/service/contracts.py` 的 dataclass 字段提取。"前端调用方"由 grep `apps/desktop/src`（不含 `service/`）得到，"—"表示 UI 当前没有调用。

### 6.1 Run 生命周期

| wire 方法 | 客户端方法 | 额外参数 | 前端调用方 |
|---|---|---|---|
| `health` | `health()` / `ready()` | 无 params | runController |
| `start_run` | `startRun` | `request_text`；`run_id? task_id? workspace_root? metadata?` | App, delegate, runController |
| `get_run` | `getRun` | `run_id` | runController |
| `list_runs` | `listRuns` | `limit?`（客户端固定传 100，session 用 `identity.sessionId`） | SunWorkspace, runController |
| `pause_run` | `pauseRun` | `run_id`；`requested_by?` | — |
| `resume_run` | `resumeRun` | `run_id`；`request_text? checkpoint_id? action? external_state_evidence? no_effect_confirmation?` | App, runController |
| `cancel_run` | `cancelRun` | `run_id`、`requested_by`；`reason` 由 dispatcher 固定补为 `USER_CANCEL` | AgentComposer, App, runController |
| `list_artifacts` | `listArtifacts` | `run_id` | runController |
| `read_events` | `readEvents` | `run_id`；`after_sequence? limit?`（立即返回） | runController |
| `subscribe_events` | `subscribeEvents` | 同上（无新事件时最长挂起约 8s） | runController |
| `delegate_run` | `delegateRun` | `parent_run_id`、`request_text`；`target_planet? profile_id? context_refs?` | delegate |
| `shutdown` | `shutdown` | 无 params | — |

### 6.2 会话、任务、审批、协作

| wire 方法 | 客户端方法 | 额外参数 | 前端调用方 |
|---|---|---|---|
| `create_session` | `createSession` | — | GlobalChat, SunWorkspace |
| `delete_session` | `deleteSession` | — | SunWorkspace |
| `list_sessions` | `listSessions` | `limit?`（**不需要** `session_id`） | GlobalChat, SunOrchestration, SunWorkspace |
| `assign_session_workspace` | `assignSessionWorkspace`* | `workspace_id` | SunWorkspace |
| `create_task` | `createTask` | `parent_task_id? origin_planet? owner_profile_id? task_id?` | — |
| `list_tasks` / `get_task` | `listTasks` / `getTask` | `limit?` / `task_id` | SunOrchestration, TaskTray / — |
| `list_handoffs` | `listHandoffs` | `limit?` | SunOrchestration |
| `list_participants` | `listParticipants` | — | SunOrchestration, SunWorkspace |
| `list_approvals` | `listApprovals` | `run_id? status? limit?` | SunOrchestration, SunWorkspace, TaskTray |
| `decide_approval` | `decideApproval` | `run_id`、`approval_id`、`decision`（`APPROVED`/`DENIED`）；`decided_by?` | 同上 |
| `list_history` | `listHistory` | `limit?` | — |

### 6.3 工作区、文件、配置

| wire 方法 | 客户端方法 | 额外参数 | 前端调用方 |
|---|---|---|---|
| `upsert_workspace` | `upsertWorkspace` | `workspace_id`；`display_name? path? capabilities? default_profile_id? description?` | SunWorkspace |
| `list_workspaces` | `listWorkspaces` | `limit?` | GlobalSettings, SunWorkspace |
| `delete_workspace` | `deleteWorkspace`* | `workspace_id` | SunWorkspace |
| `list_directory` | `listDirectory`* | 只传 `path`，**不经过 DTO、不做身份校验** | SunWorkspace |
| `pick_folder` | `pickFolder` | 无；macOS `osascript` 弹窗 | SunWorkspace（经 `setSidecarPicker`） |
| `reveal_path` | `revealPath` | 只传 `path`；`open`/`xdg-open`/`os.startfile`，不经过 DTO | SunWorkspace 文件夹菜单 |
| `list_workspace_files` | `listWorkspaceFiles` | `limit?` | Mars |
| `read_workspace_file` / `write_workspace_file` | 同名 camel | `path`；写入时带 `content` | Mars |
| `get_settings` / `update_settings` | 同名 camel | 更新时：`provider_mode? deepseek_model? ollama_model?` | App（30s ping）、GlobalSettings |
| `upsert_profile` / `list_profiles` | 同名 camel | `profile_id`；`display_name? planet_affinity? model_ref? skill_refs? permission_policy?` | — / GlobalSettings, Uranus |
| `list_capabilities` | `listCapabilities` | 无 DTO；直接读 capability registry | GlobalSettings |
| `list_skills` | `listSkills` | 无 DTO；读 SKILL.md 索引 | GlobalSettings, Uranus |
| `web_search` | `webSearch` | `query`、`max_results?`（默认 8），无 DTO | Mercury |

\* `pick_folder`、`list_directory`、`reveal_path` 不经过请求 DTO、不做身份校验（见 §10-3）；`assign_session_workspace`、`delete_workspace` 走请求 DTO。五者都声明在 `WorkspaceClient` 上（`types/service.ts`），Mock 与 Unavailable 客户端均已实现。

工作区源目录：`WorkspaceSummary.path` 是 primary 目录（run 文件操作的作用域），`paths` 是全部源目录；`upsert_workspace` 只传 `path` 时等价于单目录 `paths=[path]`，`paths` 非空时 primary 取 `path`（须在列表内）或回退 `paths[0]`。

### 6.4 领域工作台（Jupiter / Saturn / Earth / Pluto / Uranus）

| wire 方法 | 客户端方法 | 额外参数 | 前端调用方 |
|---|---|---|---|
| `list_facts` / `forget_fact` | 同名 camel | `scope? limit?` / `category`、`key`、`scope?` | Jupiter / — |
| `search_artifacts` | `searchArtifacts` | `query? all_sessions? limit?` | Jupiter |
| `create_schedule` | `createSchedule` | `request_text`、`interval_seconds`；`target_planet? schedule_id? auto_eval?` | — |
| `list_schedules` / `update_schedule` | 同名 camel | `limit?` / `schedule_id`、`status` | Saturn |
| `record_evaluation` / `list_evaluations` | 同名 camel | `name? run_id? baseline_ref? candidate_ref? score? verdict? notes? eval_id?` / `all_sessions? limit?` | Earth |
| `create_motif` / `assign_motif` / `resolve_motif` / `list_motifs` | 同名 camel | `text`、`motif_id? source?` / `motif_id`、`parent_run_id?` / `motif_id`、`resolution_run_id`、`resolution` / `status? limit?` | Pluto（不用 assign） |
| `list_workflows` | `listWorkflows` | 无 DTO；读 workflow registry | — |
| `upsert_workflow_graph` / `list_workflow_graphs` / `run_graph` | 同名 camel | `graph_id`、`name? definition? owner?` / `limit?` / `graph_id`、`parent_run_id?` | Uranus |

返回值结构以 `contracts.py` 中对应的 `XxxSummary` / `XxxHandle` dataclass 的 `to_dict()` 为准，TS 侧镜像在 `types/service.ts`，映射函数在 `localAgentServiceClient.ts`（`mapSnapshot`、`mapEvent` 等）。

---

## 7. Run 状态与事件

### 7.1 状态映射（`localAgentServiceClient.ts:115`）

| 后端 `RunStatus` | 前端 `RunStatus` | 说明 |
|---|---|---|
| `CREATED` | `pending` | `start_run` 只写入 durable head，Runtime 之后才会接手执行 |
| `RUNNING` | `active` | |
| `CANCELLING` | `cancelling` | 不等于已取消 |
| `SUSPENDED` | `blocked` | 来自 `pause_run`，**UI 无法区分暂停和阻塞** |
| `WAITING_USER` | `blocked` | 有 `PENDING` 审批，调用 `decide_approval` 解除 |
| `FAILED_RECOVERABLE` | `blocked` | |
| `BLOCKED` | `blocked` | 可以 resume |
| `FAILED_TERMINAL` | `failed` | 不可恢复 |
| `COMPLETED` / `CANCELLED` / `TIMED_OUT` | 同名小写 | 终态 |

规则：每个 session 同时只能有一个活跃 Run，否则后端返回 `RUN_ALREADY_ACTIVE`，前端 `delegate.ts:21` 也会先做一次守卫。

### 7.2 事件（`contracts.py:215` `EventType`）

`run_created`、`run_started`、`run_resumed`、`workflow_activated/started/completed`、`stage_started/completed`、`task_started/completed`、`assistant_delta`、`artifact_published`、`checkpoint_committed`、`resume_decided`、`run_cancelling`、`run_waiting_user`，以及终态事件 `run_completed/failed/blocked/cancelled/timed_out`。

事件字段：`event_id`、`sequence_number`（≥1，Run 内单调递增）、`event_type`、`tenant_id`、`session_id`、`run_id`、`workflow_id?`、`stage_id?`、`task_id?`、`run_revision`、`timestamp`、`payload`。

### 7.3 读取语义

- `after_sequence` 是**排他**游标：返回的事件 `sequence_number` 都大于它，违反时客户端会报 DTO 错误（`mapEvents`）。客户端会把负值钳成 0。
- 同一 `event_id` 去重，按 `sequence_number` 排序（RunController）。
- 一次最多返回 `limit` 条（默认 100）；读到终态事件就停止。
- **Snapshot 是事实，事件只用来唤醒刷新**。RunController 收到 `subscribe_events` 返回后会重新 `get_run` 和 `list_artifacts`，拼成 RunView。
- 最终回复取 `snapshot.output.text`；`conversation` 是中间消息；`assistant_delta` 用于流式打字效果。
- 长轮询链：`RunController.startPollingIfLive`（`runController.ts:295`）对每个活跃 Run 循环调用 `subscribe_events`，失败时退避 `pollIntervalMs` 后重试，Run 进入终态后停止。

---

## 8. 错误模型

### 8.1 错误码（`agent/service/errors.py`、`local_sidecar/serializer.py`）

| 来源 | 码 | 说明 |
|---|---|---|
| 协议 | `INVALID_REQUEST`、`UNSUPPORTED_OPERATION`、`INVALID_RESPONSE`、`INTERNAL_MODEL_LEAK` | message 原样返回 |
| Service | `IDENTITY_MISMATCH`、`RUN_NOT_FOUND`、`SESSION_NOT_FOUND`、`RUN_ALREADY_ACTIVE`、`RUN_ALREADY_CANCELLING`、`ALREADY_CANCELLED`、`ALREADY_TIMED_OUT`、`ALREADY_COMPLETED`、`RUN_NOT_CANCELLABLE`、`RESUME_NOT_ALLOWED`、`IDEMPOTENCY_CONFLICT`、`EVENT_SEQUENCE_INVALID`、`EVENT_CURSOR_EXPIRED`、`CURSOR_INVALID`、`STORE_BUSY`、`PROVIDER_UNAVAILABLE`、`SERVICE_CLOSED` 等 | **message 会被替换成固定的安全文案**（`_SAFE_ERROR_MESSAGES`），具体原因只在 stderr |
| 未分类异常 | `INTERNAL_ERROR` | 堆栈打到 stderr，前端只收到 `internal service error` |

### 8.2 前端收到的形态（两种传输一致）

| 情况 | Tauri（JSONL） | HTTP |
|---|---|---|
| 业务失败 `ok:false` | `AgentServiceClientError{code=原码, retryable=原值, runId, requestId}` | 同左 |
| HTTP 非 2xx | — | `INTERNAL_ERROR`（`local transport protocol error on <method>: sidecar http <status>`） |
| 网络或进程不可用 | `PROVIDER_UNAVAILABLE`，retryable=true | `PROVIDER_UNAVAILABLE`，retryable=true |
| fetch 被中止 | — | 原样抛出 `DOMException AbortError`，调用方可以忽略 |
| 响应 DTO 形状不对 | `INTERNAL_ERROR: AgentService returned an invalid public DTO`（原因被丢弃，`serviceProjectionError`） | 同左 |

`ok:false` → `AgentServiceClientError` 的投影由 `localTransport.ts` 的 `rpcErrorToClientError` 完成，stdio 与 HTTP 共用；HTTP 路径的响应同样经过 `decodeLocalRpcResponse` 信封校验和 id 匹配。

### 8.3 前端处理约定

- 按 `error.code` 分支，不要匹配 message 文本。
- `retryable=true` 才允许自动重试。
- 导航中断导致的 `AbortError` 可以忽略；其他错误必须显示给用户，不要用 `.catch(() => {})` 吞掉。

---

## 9. 新增或修改一个方法的完整清单

按顺序修改，每一项都不能省：

1. `agent/service/contracts.py`：新增 `XxxRequest` dataclass（含 `from_dict`、`__post_init__` 校验）和返回 DTO（含 `to_dict`）。
2. `agent/runtime_store/contracts.py` + `sqlite.py`：需要持久化时修改。
3. `agent/service/service.py`：实现 `async def xxx(request)`，并在这里做身份和作用域校验。
4. `agent/service/local_protocol.py`：在 `LocalTransportMethod` 里加枚举值。
5. `agent/service/local_sidecar/dispatcher.py`：在方法表里注册，并实现 `_xxx`，用 `self._from_dto(XxxRequest.from_dict, params)` 构造请求。
6. `tests/test_desktop_local_protocol.py`：白名单测试直接解析 `localTransport.ts` 的数组与 Python 枚举对比，**不需要**改测试本身，但顺序要与 Python 枚举保持一致。
7. `apps/desktop/src/service/localTransport.ts`：`LOCAL_TRANSPORT_METHODS` 加上该方法，顺序与 Python 枚举一致。
8. `apps/desktop/src/types/service.ts`：camelCase 请求/返回类型，并把方法**加进某个 `*Client` 接口**，让它成为 `DesktopAgentServiceClient` 的一部分。
9. `apps/desktop/src/service/localAgentServiceClient.ts`：写 camel→snake 参数映射和响应 `mapXxx` 校验。
10. `apps/desktop/src/service/mockAgentService.ts`：内存实现。
11. `apps/desktop/src/service/clientFactory.ts`：在 `UnavailableDesktopAgentServiceClient` 里加 `throw this.error` 桩。
12. 页面：通过 `ctx.service.xxx(...)` 调用，**不使用** `as never` 或 `as unknown as`。
13. 测试：`apps/desktop/tests/localAgentServiceClient.test.ts` 用假 transport 断言 wire params 和响应映射；Python 侧加 dispatcher 或 service 测试，断言字段确实到达 Service。

验证命令：

```bash
cd apps/desktop && npm run typecheck && npm test
.venv/bin/python -m pytest -q tests/test_desktop_local_protocol.py
# 可选：真实 sidecar 冒烟
cd apps/desktop && npm run test:sidecar-e2e
```

---

## 10. 已知不一致与风险（对接时必须知道）

| # | 问题 | 证据 | 影响 |
|---|---|---|---|
| 1 | ~~HTTP 传输把业务错误码压平成 `INTERNAL_ERROR`~~ **已修复** | `localTransport.ts` 的 `rpcErrorToClientError` 由两种传输共用 | §8.2 |
| 2 | HTTP sidecar 设置 `ACAO: *` 且没有鉴权，而 `list_directory`、`read/write_workspace_file`、`start_run` 都可以通过 `/rpc` 调用 | `http_server.py:42`、`service.py:535` | 本机任意网页都可以向 `127.0.0.1:8765` 发请求。仅限开发使用，不要在不可信环境下开启 `--http` |
| 3 | `list_directory`、`pick_folder`、`reveal_path` 绕过请求 DTO 和身份校验；`pick_folder` 只支持 macOS | `dispatcher.py:392-400`、`service.py:508` | 跨平台不可用；行为不受 tenant 约束 |
| 4 | ~~4 个 UI 在用的方法不在 TS 接口里（§6.3 星号项）~~ **已修复** | 已并入 `WorkspaceClient`，Mock/Unavailable 同步实现 | §6.3 |
| 5 | `start_run.metadata.model/effort` 由 Composer 发送，但 `agent/` 下没有任何代码读取 | grep `agent/` 无匹配 | 模型和强度选择器目前对执行**没有效果** |
| 6 | `SUSPENDED` 被映射成 `blocked` | `localAgentServiceClient.ts:119` | 暂停态在 UI 上不可见；而且 `pause_run` 目前没有任何 UI 调用方 |
| 7 | `workspace` 注册表同时承担两种语义：Sun 路由的"星球能力工作区"（`route.workspace_id` 会被当成 `target_planet`，`service.py:614`）和新的"文件夹工作区"（`workspace_id` 是绝对路径） | `service.py:607-616`、`:1505`、`SunWorkspace.tsx` upsert 时 `workspaceId: picked.path` | **[假设]** 请求文本命中文件夹名时，可能把路径当作 target_planet 来路由，需要离线测试确认 |
| 8 | `from_dict` 忽略未知键 | `contracts.py:300` | 字段名拼错时静默丢失 |
| 9 | `web_search` 的空 query 抛出 `ValueError`，被当作未分类异常处理 | `dispatcher.py:326` | 前端收到 `INTERNAL_ERROR`，而不是 `INVALID_REQUEST` |
| 10 | ~~Tauri 默认 sidecar 参数缺少 `--database`~~ **已修复** | `lifecycle.py` 默认 `~/.tsagent/runtime.db` | §2.2 |
| 11 | `GET /events` SSE 在后端实现了，但前端没有使用 | `httpTransport.ts:60` 无调用方 | 两套事件推送路径，只有长轮询在用 |
| 12 | `service-wire.md` 的过时描述：说"单一 HTTP 端点"（实际还有 stdio）、"响应 envelope 用 exactKeys 校验"（~~只有 JSONL 路径会校验~~ 两种传输都已校验）、"SSE event stream"（实际未使用） | 对照本节 11 | 以本文为准 |
| 13 | 已实现但 UI 没有调用的方法：`pause_run`、`create_task`、`get_task`、`list_history`、`create_schedule`、`upsert_profile`、`forget_fact`、`list_workflows`、`assign_motif` | §6 表格 | `desktop_p0_audit.md` 记录的 "Pause 按钮"、"MEMORY tab 删除" 在后续改版中丢失 |

---

## 11. 排查流程速查

1. 看前端错误的 `code`：业务错误码在两种传输下都会原样到达（§8.2）；`INTERNAL_ERROR` 且 message 以 `local transport protocol error on <method>:` 开头说明是信封/HTTP 层的失败，看 sidecar stderr。
2. 看 sidecar 的 **stderr**：`INVALID_REQUEST`（`from_dict` 失败）、`INTERNAL_ERROR` 的堆栈和 Service 的真实原因都只在那里。
3. 直接用 curl 复现，绕开前端：

   ```bash
   curl -s localhost:8765/rpc -H 'Content-Type: application/json' -d '{"id":"t1","method":"list_sessions","params":{"tenant_id":"tenant-local","user_id":"user-local","request_id":"dbg-1"}}'
   ```

4. 涉及 Run 不可见或刷新不到的问题，先确认读取时传的 `session_id` 是不是 Run 所属的 session（§4），以及 `after_sequence` 是否已经越过了目标事件（§7.3）。
5. Mock 模式下正常、local 模式下异常时，先查该方法是否是 §6.3 的星号方法，以及 Mock 实现与后端语义是否一致。
