# P0 仓库审计 — TSAgent Implementation Plan v1.0 差异表

日期：2026-11-05 · 基线：`npm run build` ✓ / test:orbits ✓ / test:local-client ✓ / desktop4a 6/6 / 4b1 3/3 / 4b2 9/9

## 文档假设 vs 仓库现状

| 文档假设 | 现状 | 处理 |
|---|---|---|
| React + TS + Vite | React 19.1 / TS 5.9 / Vite 7.3（`apps/desktop`） | 一致，沿用 |
| Three.js + R3F | 命令式 Three 0.186（`components/solar/*`），无 R3F | 按选型阈值沿用现有实现 |
| Zustand / 等价 store | `RunController` 订阅 + `useState` | P1 不引入 |
| `/w/:planet` 可深链 | 无路由（`view: home|inspector` + `homeMode`） | 新增 `react-router-dom` HashRouter |
| 桌面壳 | Tauri 2 脚手架 `src-tauri/` + `TauriSidecarBridge` 契约 | 已有 |
| sidecar 入口 | `python -m agent.service.local_sidecar`（stdio JSONL；`--http` 浏览器开发态） | `tauriBridge.ts` 默认命令写错（`main.py --service`）→ 已修 |
| 全局 Chat/Settings 后端 | 无 Session 消息 / Settings 接口 | P2 阻塞项 |
| Task/Handoff/Approval | AgentService 只有 start/get/list/resume/cancel run、listArtifacts、streamEvents | P3+ 阻塞项；UI 不伪造 |
| Memory/History | 无 Jupiter 域查询接口；History 暂由 run 事件映射 | P5 阻塞项 |
| Run 状态机 | pending/active/cancelling/cancelled/timed_out/completed/failed/blocked | 与文档词表不同，沿用现有 DTO |
| 测试 | esbuild+node harness（service/client 层），无 Vitest/Playwright/DOM 测试 | 新增 harness 沿用同模式 |
| 事件流 | run 级 seq + cursor 去重（viewMapper/runController 已做） | SSE 订阅为 P3 项 |

## 风险清单

- 工作区有大量非本轮产生的未提交后端改动（agent/、main.py、tests/）——本轮不触碰。
- three.js 产物 >500kB chunk 警告（已知）；工作台路由用 React.lazy 分包。
- 现有 e2e 脚本只测 service 层，不改 DOM 即不破坏。
- DEMO 工作台为静态骨架：恒显 DEMO 徽章，不声称真实能力。
- 真实场景验证（Tauri 实机、真实 sidecar 联调）不在离线测试范围，见交付说明。

## 阻塞项（P2+ 需要后端）

`POST /sessions/{id}/messages`、`tasks/{id}/handoffs`、`approvals/*`、`settings`、`jupiter/history`、`jupiter/memory`、workspace 注册表、SSE 事件订阅。当前 service contract 见 `agent/service/contracts.py`。

## 进度增量（本轮会话）

**已落地 LIVE**：Sun 编排面板（sessions/tasks/handoffs/approvals + Delegate 发起）、Saturn 调度 CRUD、Jupiter artifacts+history、Uranus profiles、Venus/Neptune/Mars artifact 驱动视图、Global Settings（provider/model）、TaskTray 审批托盘、Tauri .app 可双击。

**Mock 已补齐**：sessions/tasks/handoffs/approvals/schedules/profiles/settings/history 全内存实现。

**仍阻塞（需新后端）**：
- `sessions/{id}/messages` — Global Chat 消息 RPC（当前 chat = startRun 替身）
- `spawn_subtask` transport 暴露 — 现为 agent-loop 内部 API；UI 委派走 `create_task` + `start_run(task_id)` 等价路径
- Mercury BrowserContext 适配器、Earth A/B 评测基建、Pluto 探索预算域
- SSE 订阅（现仅 `read_events` 拉取式）

## 进度增量（第三轮）

**新 RPC（协议 → dispatcher → client → UI 全链路，真后端已验证）**：
- `list_capabilities` — capability_registry 单例 → Uranus 能力卡 + Settings Tools/MCP + Mars Runtime Tools（16 项真实能力）
- `list_skills` — skills_index() SKILL.md → Uranus 技能卡 + profile 绑定
- `web_search` — agent/search provider 链 → Mercury 真实搜索（真 DuckDuckGo 结果已验证）
- `subscribe_events` — 长轮询事件推送（8s hold）→ controller 链式订阅替代 setInterval

**真实环境修复（真后端验证才暴露）**：
- `http_server.py` 补 CORS（OPTIONS + ACAO）——浏览器 local 模式此前完全不通
- `App.tsx` 空 catalog 全局门 → `ctx.activeRun` 可空 + 各面板 Empty 态（工作台路由不再被挡）
- 订阅重入死循环 → `pollingStopped` 终止标志

**真后端验证记录**：`--http 8765` + `VITE_AGENT_SERVICE_MODE=local`，badge 变 "Local AgentService"，10 工作台全渲染、真 DDG 搜索、CORS/RUN_NOT_FOUND fail-fast 正确。

**仍阻塞（后端新域设计决定）**：
- `sessions/{id}/messages` — 判定为 listHistory 的第二数据源，不建
- `spawn_subtask` transport — agent-loop 内部 API，UI 委派走 create_task+start_run 等价
- Playwright BrowserContext 域（Mercury 接管）、专用实验/探索 schema（Earth/Pluto）
- `web_fetch` 是 executor 内部工具，无 service 入口，不外露

## 进度增量（终盘）

**新后端域接入（pause/facts/participants/search/workspace_fs）**：
- `pause_run` → `AgentServiceClient.pauseRun` + LocalAgentServiceClient impl + Sun Pause 按钮（active 态）
- `list_facts`/`forget_fact` → Jupiter MEMORY tab（治理事实+删除）
- `search_artifacts` → Jupiter FILES tab 跨会话搜索框
- `list_workspace_files`/`read`/`write` → Mars 真 IDE（文件树+编辑+SAVE，MODIFIED 标记）
- `list_participants` → Sun Sessions roster
- `delegate_run` → GlobalChat @planet + Sun Delegate（task+handoff 原子 provision）
- `createSession` → GlobalChat "+ new session"
- Mock：全部对应内存实现（含 wsFiles 文件系统）

**修复**：
- types/clientFactory 并行冲突 → 恢复 HEAD（用户已接好层），仅做 UI 消费
- `http_server.py` CORS（OPTIONS+ACAO）——浏览器 local 模式打通
- 空 catalog 全局门 → ctx.activeRun 可空 + 各面板 Empty 态
- 订阅重入死循环 → pollingStopped 终止标志

**测试**：build ✓ / local-client 16 ✓ / 4a 6 ✓ / 4b1 3 ✓ / 4b2 9 ✓ / scenes ✓ / orbits ✓ / sidecar-e2e 22 ✓ / Python 协议 12 ✓
