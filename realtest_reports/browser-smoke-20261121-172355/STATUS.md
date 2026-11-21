# browser-smoke-20261121-172355 — 执行器路径真实冒烟

- 时间：2026-11-21 17:23（本地）
- 代码：browser_* 接入执行器的工作树（提交见本次 commit）
- Provider：本地 ollama `qwen3:8b`（真实模型调用）
- 环境：项目 `.venv`（playwright 1.64.1），真实 Chromium 经 EgressProxy 启动；
  页面为 loopback `http://127.0.0.1:8899/`，因本机 DNS 抖动使用显式开发放行
  `DDG_ALLOW_PRIVATE_URLS=1`（仅本次冒烟，生产边界未改）。
- 任务：`用浏览器打开 http://127.0.0.1:8899/ 然后把页面截图保存到 shots/page.png`

## 已验证证据（eval.db + 工作区地面真相）

- `task_started`/`task_completed`：`browser.navigate`（verb=read，succeeded）、
  `browser.screenshot`（verb=write，succeeded）——执行事件统一走执行器。
- 产物：`artifact_metadata` 一条 `file` 记录，`exists_flag=1`、`verified=1`，
  `digest=765864bbe95e15ef1119b5da8930a6f82426ee13523e88ba9ff86b60652b108c`，
  与落盘 PNG（1280x720）的 sha256 一致。
- `run_completed`：`deliverable_verifier` 报 "1 distinct verified file(s)"、
  `execution_evidence` verified、`task_set` verified → `COMPLETED`。
- 运行结束前 `browser_session` 由 `AgentLoop.run` finally 关闭（离线用例覆盖取消路径）。

## 结论

截图经真实模型调用落盘 + 执行事件 + 文件证据 + 可读产物 + 目标校验通过——本验收项在真实模型下成立。
本次只覆盖截图链；正文来源证据、点击审批、双 Run 隔离由离线契约测试覆盖，未在本次真实模型下逐一复验。
