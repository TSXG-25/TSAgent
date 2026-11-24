# MB-01 探针报告：同页显示与输入

日期：2026-11-24。工单：实施方案 MB-01（[mercury-browser-implementation-plan-2026-11-24.md](mercury-browser-implementation-plan-2026-11-24.md)）。性质：本机离线验证（真实 Chromium + 本地站点），非 Tauri 内实测。

## 交付物

- `agent/browser/display.py` — `DisplaySession`：现有单个 Playwright `Page` 的 CDP 通道，`Page.startScreencast` 出帧（jpeg + metadata）、`Input.*` 注入人工输入。每帧 ack 流控；帧只在内存中转发，不落盘、不产生截图产物或来源证据。
- `scripts/probe_browser_display.py` — 可运行探针：本地测试站点 + websockets 流通道（帧与输入同一条连接）+ 裸 HTML 查看器。`--selfcheck` 无人值守跑完全序列并打印指标；默认模式供人工查看。
- `tests/test_browser_display.py` — 6 项离线契约（真实 Chromium，经出口代理）。

## 验证项 → 结果

| 验收要求 | 结果 |
| --- | --- |
| 帧来自真实页面渲染 | 真实 Chromium screencast 帧，JPEG 尺寸与 viewport 一致（1280×720）；经 WS 通道送达真实客户端（校验 JPEG magic + metadata） |
| 用户输入 → Playwright 读回同值 | CDP click + `Input.insertText` 输入 `uni-input-Δ中文-42` / `selfcheck-输入Δ-7`，`input_value` 读回完全一致 |
| Agent 改动 → 前端看到 | Playwright 侧 DOM 变更后 5s 内出现内容不同的新帧 |
| 点击 / 滚动 | wheel 注入 → `scrollY` 变化，后续帧 metadata `scrollOffsetY` 同步 |
| 窗口缩放 | `Emulation.setDeviceMetricsOverride` → 新帧携带新 deviceWidth/Height（800×600） |
| 三窗口不串 | 三 Page 各挂 DisplaySession；向窗口 B 输入，A/C 字段保持空 |

复现：`DDG_ALLOW_PRIVATE_URLS=1 .venv/bin/python scripts/probe_browser_display.py --selfcheck`（loopback 地址需既有开发放行开关；SSRF 代理仍处理全部流量）。

## 实测数据（selfcheck，3 窗口，M 系列本机）

- 首帧：18–44ms（三窗口各一次）。
- 输入 → 下一帧：n=22，p50≈18ms，p95≈39ms——远低于 200ms 体验目标。
- 活动窗口帧率：DOM 以 10Hz 变更时约 11.5–11.7fps；静态页按需出帧（无变更无帧，符合"静态页不要求持续刷帧"）。
- 资源：3 窗口 Chromium 合计 RSS ≈ 92–99MB；单帧 JPEG 均值 ≈ 60KB。
- viewport/DPR 映射：headless 下 deviceScaleFactor=1，帧 deviceWidth/Height 即 CSS viewport 尺寸；输入坐标为 CSS 像素，客户端按 `deviceWidth / img.clientWidth` 换算；metadata `scrollOffsetX/Y` 即页面滚动偏移（CSS px）。

## 承载方案结论

**CDP screencast + Input 通道在本项目 Chromium 上实际可用，建议作为正式通道的基础。** 对应 MB-03 的形态：sidecar 进程内一条本地 WS 流通道，显示帧与高频人工输入走同一连接；每窗口一条 `DisplaySession`；慢端丢帧（latest-wins）而非积压重放。证据与显示分离成立——本探针中帧全程内存转发，截图留证仍走现有 workspace/artifact 路径。

已知边界（移交 MB-02/03/06）：当前探针每连接一窗口、无窗口注册表与身份（MB-02）；查看器是验证件不是 Mercury UI（MB-04）；输入通道未接 R2 审批与人/Agent 抢控（MB-06）；Playwright `new_cdp_session` 是稳定 API，`Page.startScreencast` 为 CDP 实验域——本项目 Chromium 已实测可用，跨 Chromium 升级需在 MB-08 门禁回归。

## 未验收项

自动化部分全部通过，但按方案，**Tauri 内中文 IME、剪贴板、真实手感属独立实测清单**，本轮未跑——MB-01 工单不据此宣布完成，仅"同页显示与输入"的可行性已获离线证据。真实环境验收建议步骤：`npm run tauri:dev` 起 Desktop，将本探针 WS 通道嵌入 MB-04 界面后逐项人工核对。
