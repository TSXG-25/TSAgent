# Mercury 浏览器工作台 — 能力说明（MB-08 收口）

日期：2026-11-24　分支：`codex/github-upload`

本文件是实施方案（`mercury-browser-implementation-plan-2026-11-24.md`）
8 张工单完成后的现状记录，区分**已验证的能力**与**仍需真实环境
验证的项**。不要把自动化测试结果当成真机验收结论。

## 已实现

| 能力 | 实现 | 验证 |
|---|---|---|
| 窗口身份与生命周期 | `BrowserWindow`（view_id/owner/scope/doc_generation/历史）＋ `BrowserManager`（共享浏览器进程、workbench 按 `tenant:session` 共享 context、Run 窗口独立 context） | `test_browser_windows.py` 42 项 |
| 跨端窗口合同 | `list_windows/open_window/close_window/navigate_window/claim_window/release_window/display_ticket` 服务方法＋wire schema → 生成 TS 类型 | `test_browser_display_bridge.py`＋wire fixture |
| 显示通道 | loopback WebSocket：票据（session 作用域 token）＋ Origin/Host 校验；帧出/输入入同一条连接；慢端丢帧（latest-wins）；`view_id`+`doc_generation` 随帧 | 同文件 WS 端到端（真 Chromium） |
| 多窗口 UI | 1–4 固定布局（单栏/并排/左一右二/2×2）、展开、活动选中；每窗格有标题/URL/前进后退/刷新/关闭/地址栏/加载与错误态；CDP 帧 canvas 渲染；点击/拖动/滚动/键盘/IME 合成/粘贴经通道回注（剪贴板读取走页面自身逻辑）；任务发送只绑定选中或活动窗口；@星球委派走既有 handoff 链不带窗口；右栏接 Run 事件流显示工具动作与 view_id | vitest 23 文件 161 项（DOM 级）；真实渲染未在 Tauri 内验证 |
| 模型操作绑定 | `browser_*` 六个工具的 `view_id` 参数；执行器经 `resolve_browser` 解析——多窗口必须显式指定，未绑定 id 直接拒绝；prompt 注入白名单；`task_started/task_completed` 事件与结果携带 `view_id`；来源 URL 与读取文档同帧 | `test_browser_view_binding.py` |
| 接管/并行/审批 | `controller=="user"` 时一切 `browser_*` 拒绝执行；审批按 `(control_epoch, controller, doc_generation)` 快照（省略 view_id 时解析唯一绑定窗口），人工导航、接管或接管→交还均使旧审批作废（`APPROVAL_STALE`）；显示桥输入同样需要用户控制且文档代次一致；`acquire` 冲突点名持有 Run；同 scope 开窗串行化保证共享 context 与四窗上限；`release_run` 只释放本 Run | `test_browser_tools.py`＋`test_browser_view_binding.py`＋桥输入测试 |
| 结果与交接 | 读取正文与 URL 同帧绑定；`delegate_run` 将结论作为子 Run 请求文本移交 Mars——不交接浏览器会话/登录态 | `test_e2e_dual_window_research_then_mars_handoff` |

## 活窗口与历史产物 — 不要混淆

- **活窗口**（workbench `BrowserWindow`）：当前真实文档。它的 URL/
  标题反映当下；Agent 读它取的是此刻内容；帧通道是易失展示。
- **历史产物**：`browser_screenshot` 落盘的 PNG、`browser_extract`/
  `browser_snapshot` 记录的正文＋URL——任务结束后网页可能变化，
  重开记录的 URL 不保证内容与当时一致；历史内容以产物为准。

## 尚未真实验证（需要真机环境）

- Tauri WebView 内的显示帧渲染、坐标换算、滚动手感与多窗口
  缩放——CDP 与 WS 通道已在项目 Chromium 上实测；IME 合成、
  剪贴板与拖选已接实现但 WebView 端真实手感未跑。
- `Page.startScreencast` 属 CDP 实验域：本项目 Chromium 实测可用
  （首帧 18–44ms、输入→帧 p50≈18ms），Chromium 升级需回归。
- 真实模型（非 fake provider）在绑定窗口上的完整研究任务——目前
  离线契约与假 provider 覆盖执行链，真实模型端到端未复跑。
- 审批 UI 在真实运行中点击"通过/拒绝"后窗口动作的效果；
  `doc_generation` 随帧下发，`g` 标记的输入在代次不符时被丢弃，
  真实延迟下的感受未验证。

## 验证命令

```sh
# 浏览器/窗口/通道全部（真实 Chromium + 假 provider）
DDG_ALLOW_PRIVATE_URLS=1 OPENAI_API_KEY= LLM_API_KEY= P2_SECONDARY_API_KEY= \
  TSAGENT_LLM_PROVIDER=ollama .venv/bin/python -m pytest \
  tests/test_browser_*.py tests/test_desktop_sidecar.py -q

# MB-01 探针自检（帧/输入指标）
DDG_ALLOW_PRIVATE_URLS=1 .venv/bin/python scripts/probe_browser_display.py --selfcheck

# 桌面端
cd apps/desktop && npm test && npm run test:orbits && npm run test:scenes && npm run build
```

`DDG_ALLOW_PRIVATE_URLS` 仅供测试夹具访问本机页面，不改变生产
SSRF 策略。
