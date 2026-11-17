# Desktop 前端技术债与修复迟缓根因

日期：2026-11-08 · 范围：`apps/desktop/`（TSAgent Studio）及其依赖的 Service wire 合同
读者：接手前端修复或重构的其他模型/工程师。先读本文，再动 `apps/desktop/src`。

本文所有结论都附带可复核证据（文件:行号、命令输出或 git 历史）。标注为 **[假设]** 的条目未经运行验证，需要先确认再据此修改。

---

## 0. 一页结论

前端修复慢，不是因为单个 bug 难，而是因为**每一次修改都要同时穿过多个手工同步的副本，而类型系统、测试和 CI 都没有在这些副本之间起约束作用**。于是：

1. 一个 RPC 字段的变化要手改 Python 和 TypeScript 两侧约 10–13 个文件（§2.1）；
2. UI 层用 `as never` / `as unknown as { x?: ... }` 绕过类型检查，漏改的地方仍然能通过编译（§2.2）；
3. 失败会被 `.catch(() => {})` 和空 `catch` 吞掉，Mock 模式下缺少的方法被 `?.()` 静默跳过（§2.3）；
4. UI 层没有任何测试，desktop 也不在 CI 里（§2.4）；
5. 所以 bug 只能在真实 sidecar 联调时由人发现，而本项目约定离线开发、不负责真实场景测试，bug 进不了开发者的反馈回路，只能事后一个一个补（§3）。

再加上 session 状态有三份事实来源、轮询有七处、CSS 是 40 多轮叠加的覆盖层，任何改动都很难局部推断其影响。

## 0.1 偿还进展（2026-11-17）

本轮按 §4 的顺序执行，以下条目已落地并有离线回归：

- **CI 覆盖 desktop**：`ci.yml` 增加 desktop job（typecheck + vitest + build + `src-tauri` cargo test）→ §4 P0-2。
- **统一测试入口**：引入 Vitest + Testing Library + jsdom，`npm test` 一条命令跑完；新增 `tests/dom/` DOM 测试（TaskTray、SessionRail、RunThread、ApprovalCards）→ §4 P2-1 部分。
- **合同代码生成**：`agent/service/wire_schema.py` 成为 wire 方法表与参数/结果类型的唯一来源，`scripts/gen_wire_types.py` 生成 `src/service/wire.generated.ts`；`tests/test_wire_schema.py` 做漂移检查。`localTransport.ts` 不再维护第二份手写方法表 → §4 P2-3、§2.1 大头。
- **请求构造下沉**：identity 校验与 `requestId` 生成移入 `LocalAgentServiceClient`，UI 只传业务参数；`as never` 47→3，`as unknown as` 清零 → §4 P0-4 / P1-2。
- **删除 window 事件总线**：`tsagent:composer-draft` 走 `draftStore.prefillDraft`，`tsagent:run-attachments` 走 `runAttachments` 模块，`tsagent:new-session`（无发送方的死监听）删除 → §4 P1-4。
- **轮询收敛**：`app/usePoll.ts` 提供"立即 + 间隔 + deps 重启 + 最新闭包"的共享轮询；TaskTray、SunWorkspace、WorkspaceThread、Pluto、Saturn、Uranus 全部收敛，组件内 `setInterval` 只剩 App 心跳 → §4 P1-3。
- **SunWorkspace 拆分**：596→101 行；拆出 `useSunSessions`、`SessionRail`、`RunThread`、`ApprovalCards`、`useSunDialogs` → §4 P2-2。
- **CSS 拆分**：`styles.css` 拆为 `src/styles/`（base + shared + 各星球 + omni），barrel 按原顺序 `@import`；删除 1307 条引用已失效 class 的规则（444 个死类，如旧 sidebar/thread/forge/omni2 布局）。`!important` 从 14 处保留——它们大多是防护性 reset（`content: none`、reduced-motion），逐条去除需要浏览器验证，留作外部验证项 → §4 P3-2 部分。
- **后端 `list_sessions` N+1**：store 新增 `session_run_stats`（窗口函数一次取回每 session 的 run 数 + 最新 run），service 不再每行 `list_run_heads(limit=2000)`。
- **吞错收敛**：删除会话/工作区/选目录/加载失败走 `ctx.reportError` 全局横幅；剩余 `.catch(() => {})` 8 处（刷新竞态丢弃、RefreshGate 丢弃这类语义正确的忽略）。

仍开放：session 三份事实来源（§2.5 上半）、`service.setSession()` 可变 identity、`MODELS` 硬编码、`localStorage` 旧偏好值迁移、SolarScene 巨型文件、§2.8 文案收尾，以及全部需要真实浏览器/Tauri 联调的视觉与生命周期验证。

---

## 1. 现状速览

| 指标 | 值 | 来源 |
|---|---|---|
| `src` 总行数 | 14,837 | `wc -l` |
| `styles.css` | 4,161 行，约 45 个 "pass/skin/v3.x" 段 | §2.6 |
| `localAgentServiceClient.ts` | 1,293 行，68 个 async 方法 | |
| `mockAgentService.ts` | 986 行，51 个 async 方法 | |
| `clientFactory.ts` | 379 行，其中约 51 个 `throw this.error` 桩方法 | |
| `types/service.ts` | 979 行，23 个 `*Client` 接口拼成 `DesktopAgentServiceClient` | `types/service.ts:970` |
| wire 方法数 | 56（ADR-0025 冻结时是 8） | `service/localTransport.ts:8` |
| `as never` | 47 处 / 15 个文件 | `grep -rc 'as never'` |
| `as unknown as` | 9 处（7 处在 `SunWorkspace.tsx`） | |
| `.catch(() => {})` | 11 处；带注释的空 `catch` 8 处 | |
| 组件内 `setInterval` 轮询 | 7 处（3s/8s/10s/30s 不等） | §2.5 |
| desktop 测试 | 49 个用例，全部在 service/controller 层；0 个 UI/DOM 测试 | `apps/desktop/tests/` |
| CI 是否跑 desktop | 否 | `.github/workflows/ci.yml` 只有 Python 步骤 |
| 近期 desktop 提交 | 30 feat / 6 fix；2026-11-05 一天约 25 个 feat | `git log -- apps/desktop` |

基线（2026-11-08 本地运行）：`npm run typecheck` 通过（7s）；`npm test` 退出码 0（local-client 16/16、clientFactory 6/6 等）；`pytest tests/test_desktop_local_protocol.py` 通过（白名单已改为对比 Python 枚举与 `localTransport.ts`，不再是第三份手写列表）。

---

## 2. 技术债清单（按对修复速度的影响排序）

### 2.1 Wire 合同多副本手工同步 —— 最主要的拖慢因素

一个 RPC 方法从后端到 UI 需要修改的位置：

| 层 | 文件 | 内容 |
|---|---|---|
| Py 合同 | `agent/service/contracts.py`（2,659 行） | Request/Result dataclass |
| Py 存储 | `agent/runtime_store/sqlite.py` | 持久化 |
| Py 服务 | `agent/service/service.py`（2,116 行） | 方法实现 |
| Py 协议 | `agent/service/local_protocol.py` | `LocalTransportMethod` 枚举 |
| Py 分发 | `agent/service/local_sidecar/dispatcher.py` | 方法表 + params 解析 |
| Py 测试 | `tests/test_desktop_local_protocol.py` | **第三份**方法白名单 |
| TS 协议 | `apps/desktop/src/service/localTransport.ts` | `LOCAL_TRANSPORT_METHODS` 数组 |
| TS 类型 | `apps/desktop/src/types/service.ts` | camelCase DTO + `*Client` 接口 |
| TS 映射 | `apps/desktop/src/service/localAgentServiceClient.ts` | camelCase ↔ snake_case 手写映射 |
| TS Mock | `apps/desktop/src/service/mockAgentService.ts` | 内存实现 |
| TS 桩 | `apps/desktop/src/service/clientFactory.ts` | `UnavailableDesktopAgentServiceClient` 的 throw 桩 |
| UI | 具体工作台组件 | 消费 |

证据：

- 提交 `0484b59d6`（2026-11-08）新增了 `list_directory` / `pick_folder`，但没有同步第三份白名单，导致 **当时 HEAD 上 `test_desktop_method_allowlist_is_frozen` 失败**。已修复：测试改为解析 `apps/desktop/src/service/localTransport.ts` 的数组并与 Python 枚举逐项对比，手写清单只剩 `localTransport.ts` + Python 枚举两处。
- ~~`assignSessionWorkspace` / `deleteWorkspace` / `pickFolder` / `listDirectory` 不在 `types/service.ts` 的任何接口里~~ 已并入 `WorkspaceClient`，Mock 与 Unavailable 客户端同步实现，`SunWorkspace.tsx` 的 `as unknown as` 已删除。

影响：每次改动都要人工对齐这些副本，漏一处就要等到运行时才暴露。方法白名单和接口缺失这两处已经收敛，剩下的是 contracts/service/dispatcher/client/mock 的逐方法手工同步。

### 2.2 UI 层绕过类型系统

- `as never` 47 处，几乎全部用在 service 请求对象上，例如 `SunWorkspace.tsx`、`delegate.ts:32,45,52`、`UranusWorkspace.tsx:25-26`。`as never` 能赋给任何类型，所以**请求 DTO 少字段、多字段、字段改名都能编译通过**。`typecheck` 全绿并不代表调用点和合同一致。
- ~~`SunWorkspace.tsx` 用 `service as unknown as { method?: }` 访问接口里没声明的方法~~ 已修复（方法并入 `WorkspaceClient`）。`as unknown as` 现在只剩 `runController.ts:302,329` 两处：`{ stop() }` 对象强转成 `setInterval` 句柄塞进 `pollTimers`，同一个 Map 里混了两种类型。

影响：类型系统本来是这类多副本合同唯一便宜的约束，现在在 UI 边界被关掉了。

### 2.3 错误被吞，Mock 与 Local 行为不一致

- 静默吞错：`GlobalSettings.tsx:51-66`（4 处）、`UranusWorkspace.tsx:25-26`、`SunWorkspace.tsx:39,104,152,221`、`AgentComposer.tsx:148`（取消运行失败也被吞），以及 `SunWorkspace.tsx:91,107`、`EarthWorkspace.tsx:29,43`（`catch { /* */ }`）、`SunOrchestration.tsx:36`、`TaskTray.tsx:28`。
- 后果：用户看到的是"空面板"或"没反应"，而不是错误。排查时只能接真实 sidecar、加日志、复现。这和 `README.md` 里"local 模式失败只显示 Backend unavailable，绝不静默回退"的原则相矛盾。
- Mock 一致性：Mock 是手写的约 1000 行内存实现，**Mock 模式通过不能说明 Local 模式可用**。

### 2.4 测试与 CI 覆盖空洞

- `apps/desktop/tests/` 只测 `LocalAgentServiceClient`、`clientFactory`、`RunController`；`SunWorkspace`、`AgentComposer`、`delegateToPlanet`、`GlobalChat` 等组件和 UI 逻辑没有测试。仓库没有 Vitest/Playwright/DOM 测试运行器（`docs/desktop_p0_audit.md:19` 已记录）。
- 自制的 `scripts/run-ts-test.mjs` 会用 esbuild 打包每个测试入口再交给 node 跑，`package.json` 里有 9 个零散的 `test:*` 脚本，没有统一入口，也没有 watch。
- `.github/workflows/ci.yml` 完全不跑 `apps/desktop`：TS typecheck、desktop 测试和 build 都不在门禁里。
- 最近 4 个 desktop fix（`b3aa8a42b`、`162f75372`、`8e5fbe182`、`c742f9ab1`）都是改 `delegate.ts` / `runController.ts` 的会话和委派逻辑，其中只有 controller 层的改动可能被现有测试覆盖。

### 2.5 状态有多份事实来源，轮询有多个循环

**Session 有三份事实来源：**

1. `SunWorkspace` 的本地 `useState` `sessionId`（`SunWorkspace.tsx:32`）；
2. `RunCtx.sessionId`（`app/runCtx.ts:10`，由 App 组装）；
3. 可变的 `service.identity.sessionId`，通过 `service.setSession()` 原地修改（`localAgentServiceClient.ts:552`）。

各处取用并不一致：

- `delegate.ts:19` 用 `ctx.sessionId ?? service.identity.sessionId`；
- `delegate.ts:48` 委派后启动子 Run 用的是 `service.identity.sessionId`，而 `docs/contracts/service-wire.md` 说子 Run 应落在 `main:subtask:<task_id>`。**[假设]** 这里可能把子 Run 放到了错误的 session，需要联调确认；
- `SunWorkspace.tsx:76` 查审批用 `service.identity.sessionId`，`:77` 查参与者用本地 `sessionId`。

Git 历史里连续的 session 相关修复提交（"first-class session switching"、"refreshRun can hydrate runs it has never seen"、"delegateToPlanet without an active run"、"live-run send guard"）都是这个问题的表现，每次只补了一个调用点。

**React 闭包过期（读代码即可确认）：**

- `SunWorkspace.tsx:72-92` 的 `refresh` 用 `useCallback(..., [service])` 包住，但函数体读取了 `sessionId` 和 `runs`。切换 session 后，8 秒一次的轮询仍按初始 `sessionId` 拉参与者，按旧的 `runs` 判断哪些 Run 需要 hydrate。
- `SunWorkspace.tsx:96-110` 的 effect 依赖只有 `[sessionId]`，但读取了 `runs`、`ctx.controller`。
- `SunWorkspace.tsx:119-123` 的 effect 没有依赖数组，每次渲染都会重新挂载 `tsagent:new-session` 监听。

**轮询循环（同一份数据被多个循环刷新）：**

| 位置 | 周期 | 刷新内容 |
|---|---|---|
| `RunController.startPollingIfLive` | 约 8s 的长轮询链 | 活跃 Run |
| `SunWorkspace.tsx:155` | 3s | 当前查看的 Run（与上一行重复） |
| `SunWorkspace.tsx:93` | 8s | sessions/approvals/participants，并触发 refreshRun |
| `TaskTray.tsx:33` | 8s | 审批 |
| `SunOrchestration.tsx:41` | 10s | tasks/handoffs/approvals |
| `PlutoWorkspace.tsx:27` / `SaturnWorkspace.tsx:22` | 8s / 10s | 各自域 |
| `App.tsx:73` | 30s | getSettings 当 ping |

同一个活跃 Run 同时被长轮询和 3 秒轮询刷新，审批被三个组件各拉一遍。出现竞态或请求量问题时很难定位是哪一个循环造成的。

**组件间通信走 window 事件总线：** `AgentComposer.tsx:60` 派发 `tsagent:run-attachments`，`SunWorkspace.tsx:116,121` 监听它和 `tsagent:new-session`。这条依赖没有类型，grep 组件 import 也找不到。

### 2.6 CSS 只追加、不重构，设计规范互相冲突

- `styles.css` 4,161 行，由大约 45 个依次追加的段落组成（`:424 readability pass`、`:1983 reskin pt.2`、`:2355 Blockade skin pass`、`:2451 depth pass`、`:2509 Restraint pass`、`:3380 luanyujia-workbench style borrow`、`:3422 liquid-glass`、`:3607-3720 Omni v3 / v3.1 / v3.2 / v3.3`、`:4083 DSH visual language pass`、`:4145 force left/right split` …）。后面的段落覆盖前面的段落，前面的旧规则不删。
- `.ws-root::before` 出现 5 次，`.chat-host` 6 次，`.mp ` 8 次，`!important` 12 处。
- 至少三份"权威"设计规范互相矛盾：
  - `apps/desktop/design.md`："Blockade skin"，`border-radius: 2px`，琥珀焦点，"Read this before adding any new page"；
  - `docs/ui/planet-workspace-ui.md` v1.0：声明取代 design.md，自己的 D10 已经指出"三轮皮肤叠加是不一致的根源"；
  - `styles.css:4083` 之后的 "DSH visual language pass"：大圆角、`0.5px` 边框、冷蓝色调（例如未提交改动里的 `.ac-modelmenu { border-radius: 10px; border: 0.5px solid rgba(160,190,255,.2) }`）。
- 影响：要改一个视觉问题，得先找出 N 条规则里最终生效的是哪一条。新增样式又只能继续追加到文件末尾，债务越滚越大。

### 2.7 巨型组件与内联服务调用

- `SunWorkspace.tsx` 422 行，单个组件里有 26 个 hook，混合了会话列表、工作区归档、拖拽、文件夹浏览、对话框、审批、参与者、Run hydrate 和渲染。服务调用和请求 DTO（含 `requestId`、identity 拼装）直接内联在 JSX 事件处理器里（例如 `:179-186`、`:242-256`）。
- `SolarScene.ts` 1,180 行的命令式 Three.js 场景。
- 影响：没有可以单独测试的单元，修 bug 只能改大组件，再人工点 UI 验证。

### 2.8 文案、本地偏好和小型一致性问题

- 中英混排：20 个文件含中文 UI 文案，未提交改动把 `PERMISSIONS` 改成了英文，但 `EFFORTS` 仍以 `"默认"` 作为哨兵值（`AgentComposer.tsx`）。没有 i18n 字典层。
- `localStorage` 迁移缺口：`tsagent:permission` 旧值是 `"询问"`，新 id 是 `"Ask"`，老用户读出的旧值匹配不到任何选项。
- `MODELS` 硬编码（`AgentComposer.tsx`）。`delegateToPlanet` 已经通过 `metadata.model/effort` 往后端传值，但 `agent/` 下没有任何代码读取这两个字段（grep 无匹配），所以选择器目前对执行没有效果。
- `AgentComposer` 的上下文占用环用可见文本长度估算 token，上限写死 1M。

### 2.9 文档与仓库卫生

- 文档状态滞后：`apps/desktop/README.md` 仍写着"协议只允许 8 个方法"、"当前协议尚无 `list_runs`"、"不包含 Tauri Rust 壳"；`docs/quality/known-debt.md §7` 写着"仓库没有 Tauri/Rust host"。实际上已有 56 个方法、`list_runs`，`src-tauri/` 也存在。ADR-0025 和测试名 `..._is_frozen` 里的"冻结"概念已经和现实不符。
- `docs/desktop_p0_audit.md:80` 记录过"types/clientFactory 并行冲突 → 恢复 HEAD"，说明多个会话并行改同一组共享文件时发生过冲突。
- 工作区长期有大量前后端混合的未提交改动（当前 13 个文件），`git log` 里有 "sync accumulated runtime, tools, and harness updates" 这种大杂烩提交。审查和回滚的粒度都很粗。
- 仓库跟踪了 3 个 `.pyc`（`git ls-files | grep -c '\.pyc$'`）。

---

## 3. 为什么修复进程慢：因果链

```
多副本合同(2.1) ──┐
                  ├─► 改一处漏一处 ──► 类型被 as never 关掉(2.2)，编译不报
Mock 手写(2.3) ───┘                     │
                                        ▼
                         错误被吞 / ?.() 静默 no-op(2.3)
                                        │
                                        ▼
              无 UI 测试、CI 不跑 desktop(2.4) ──► 离线反馈回路里看不到
                                        │
                                        ▼
                 只能在真实 sidecar 联调时由人发现（本项目约定不负责真实场景测试）
                                        │
                                        ▼
       session 三份来源 + 7 个轮询 + 过期闭包(2.5) ──► 现象难以复现，根因难以定位
                                        │
                                        ▼
            只修当前调用点 → 下一个调用点再出同类 bug（见 4 个连续的 session 修复）
                                        │
                                        ▼
         视觉问题叠加 CSS 覆盖层(2.6)，新功能继续追加 → 债务增长快于偿还（30 feat : 6 fix）
```

一句话：**缺少能在离线环境里失败的约束**。bug 不是修不好，而是发现得太晚、定位太贵、修完以后又没有东西防止它回来。

---

## 4. 建议的偿还顺序

原则：先恢复约束，再做重构；每一步都要有离线测试兜底。遵守仓库 `AGENTS.md`：单一路径、fail fast、不加兼容分支。

### P0 —— 恢复反馈回路（先做，成本低）

1. ~~**修复 HEAD 上的红测试**~~ 已完成：白名单测试改为解析 `localTransport.ts` 对比 Python 枚举，不再维护第三份手写列表。
2. ~~**把 desktop 纳入 CI**~~ 已完成（2026-11-17）：desktop job 跑 typecheck + vitest + build + `src-tauri` cargo test。
3. ~~**把 UI 实际用到的方法补进 `types/service.ts` 的接口**~~ 已完成：`assignSessionWorkspace`、`deleteWorkspace`、`pickFolder`、`listDirectory` 已并入 `WorkspaceClient`，Mock/Unavailable 同步实现，`SunWorkspace.tsx` 的 `as unknown as` 与 `?.()` 已删除。
4. ~~**清除 `as never`**~~ 基本完成（2026-11-17）：47→3（SolarScene、MissionControl 残留）；identity/requestId 由 client 统一注入。
5. ~~**HTTP 传输压平错误码**~~ 已完成（原对接文档 §10-1）：`rpcErrorToClientError` 由 stdio/HTTP 两种传输共用，HTTP 响应也经过信封校验。
6. ~~**Tauri sidecar 缺 `--database`**~~ 已完成（原对接文档 §10-10）：sidecar 默认 `~/.tsagent/runtime.db`。

### P1 —— 收敛事实来源

1. **Session 单一来源**：选定一个所有者（建议 App 或 RunController 持有 `selectedSessionId`），`RunCtx.sessionId` 只读暴露。删除 `service.setSession()` 的可变 identity，改为每次调用显式传 session 定位符；`service-wire.md` 已经说明 session 是定位符，不是身份。确认 `delegate.ts:48` 的子 Run session 语义，并补测试。
2. **请求构造下沉**：`tenantId/userId/requestId` 的拼装移到 client 内部，UI 只传业务参数，从而消除大部分 `as never` 和重复代码。
3. ~~**轮询收敛**~~ 已完成（2026-11-17）：共享 `app/usePoll.ts`（立即执行 + deps 重启 + 最新闭包）；组件内 `setInterval` 只剩 App 的 30s 心跳。
4. ~~**删除 window 事件总线**~~ 已完成（2026-11-17）：draft prefill 走 `draftStore.prefillDraft`，run 附件走 `runAttachments` 模块，无发送方的 `tsagent:new-session` 监听删除。
5. **吞错改为可见错误**：`.catch(() => {})` 统一改成在组件内显示错误态，或交给 controller 的 `setError`。只有导航中断这类"中止"错误可以忽略（`service-wire.md` 陷阱 7），并且要写明理由。

### P2 —— 可测试性与结构

1. ~~引入 DOM 测试运行器~~ 已完成（2026-11-17）：Vitest + Testing Library + jsdom，`npm test` 统一入口；`tests/dom/` 已覆盖 TaskTray/SessionRail/RunThread/ApprovalCards，delegateToPlanet 等路径待继续补。
2. ~~拆分 `SunWorkspace.tsx`~~ 已完成（2026-11-17）：596→101 行；`useSunSessions` / `SessionRail` / `RunThread` / `ApprovalCards` / `useSunDialogs`。
3. ~~**[可选]** 合同代码生成~~ 已完成（2026-11-17）：`agent/service/wire_schema.py` 为唯一来源，`scripts/gen_wire_types.py` 生成 `src/service/wire.generated.ts`，`tests/test_wire_schema.py` 做漂移检查。

### P3 —— 视觉层

1. ~~先定一份唯一的设计规范~~ 已完成（2026-11-17）：`design.md` 顶部声明由 `docs/ui/planet-workspace-ui.md` v1.0 接管版式/视觉；design.md 仅保留色板词表与历史上下文。
2. 部分完成（2026-11-17）：`styles.css` 已拆为 `src/styles/` 分文件（barrel 按原序 `@import` 保持级联），并删除 1307 条死规则（444 个失效 class）。`!important` 的逐条清理依赖截图/浏览器验证，仍为外部验证项。
3. 文案统一进字典，`localStorage` 偏好值改用稳定 id（与显示文案无关），并处理一次旧值迁移，或直接清理旧键。

---

## 5. 给接手模型的操作须知

- 新增或修改 RPC 时，按 §2.1 的表格逐行检查，直到 P0-1 / P0-3 落地为止；改完至少跑：
  `cd apps/desktop && npm run typecheck && npm test`，以及 `pytest -q tests/test_desktop_local_protocol.py`。
- **不要**新增 `as never`、`as unknown as`、`.catch(() => {})`、组件内 `setInterval`、window 自定义事件。
- **不要**往 `styles.css` 末尾再追加一个 "pass"。改样式时先找到已有规则修改它，并删除被覆盖的旧规则。
- `typecheck` 通过 ≠ 合同一致；Mock 模式可用 ≠ Local 模式可用。需要联调的改动必须在交付说明中列出未覆盖的真实场景项（见仓库 `AGENTS.md` 测试边界）。
- Service 语义陷阱先读 `docs/contracts/service-wire.md` 末尾的 "Traps already hit"。
