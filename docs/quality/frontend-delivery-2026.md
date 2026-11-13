# 前端体验优化交付档案

依据 `TSAgent 前端体验优化计划`（用户指令，2026）执行。每批一个独立提交，记录范围、证据与剩余外部验证。离线测试不等同于真实环境验收。

## FE-01 — 操作真实性、中英切换、输入体验 · 已交付

### 范围

- **假权限移除**：`AgentComposer` 的 `Read-only/Ask/Smart/Plan/Bypass` 选择器整体删除（UI + localStorage `tsagent:permission` + `.ac-perm*` 样式）；其位置改由真实审批状态占据——`activeRun.status === "blocked"` 时显示「等待审批」脉冲 chip（`.ac-approval`），否则不渲染。本地偏好不再冒充执行权限。
- **状态一致**：`statusLabels`/`stageLabels` 常量改为 `statusLabel()`/`stageLabel()` 本地化函数（`ui.tsx`）；Sun 会话列表由 `latestStatus.toUpperCase()` 改为 `tk("status.*")` 字典查找（class 用原始值，显示本地化）；`blocked`/`cancelling`/`cancelled`/`failed` 在徽章、会话行、执行面板使用同一词表。Composer 中 `cancelling` 状态的停止按钮禁用并标注「正在取消」。
- **输入统一**：Enter 发送、Shift+Enter 换行；`compositionstart/end` + `nativeEvent.isComposing` 双路径保证 IME 组合态不触发发送（`AgentComposer`、`GlobalChat` 一致）；`busy` 受理前禁止重复提交；提交失败时草稿不动、显示本地化摘要 + 原始错误。
- **草稿契约**：新增 `src/app/draftStore.ts`——`draftKey(tenantId, sessionId, target)` 键控，`{text, files}` 存 sessionStorage；同键实例经监听器同步；`clearDraft` 仅在请求受理成功后调用。`AgentComposer`（按 `planet ?? "sun"`）、`GlobalChat`（按 `delegateTo || "sun"`）、App 级 `requestDraft`（即 sun 键 → MissionControl 新建任务输入、GlobalChat 直发、Sun Composer 天然共享同一草稿）。
- **i18n**：新增 `src/app/i18n.ts`——`zh`/`en` 字典、`getLang()`（localStorage `tsagent:lang` 优先，否则 `navigator.language` 以 zh 开头判中文，其余英文）、`useLang()`（useSyncExternalStore）、`t()`（含 `{param}` 插值）与 `tk()`（缺键返回 undefined）。`GlobalSettings` 新增「界面 → 界面语言」选择。已覆盖：状态/阶段词表、Composer、GlobalChat、Sun 会话栏/审批条/执行面板/页脚、OmniLanding、设置页主标签、服务错误横幅（本地化摘要 + `code: message` 原始详情）。用户内容、模型输出、文件名保持原文。

### 证据

- `tests/frontendDraftI18n.test.ts`（新增 6 项）：键域隔离、sessionStorage 持久化、clearDraft 受理清空、目标隔离、系统语言默认与持久化、插值与未知码回退。
- `npm test`：typecheck（src + tests）+ 12 个测试文件全绿（84 项断言级 subtest）。
- `test:orbits`、`test:scenes`：未受影响（未改场景/合同）。

### 未覆盖（后续批次/外部验证）

- 其余九个工作台的正文文案 i18n → 随 FE-05 工作台整合批次推进。
- `SYSTEM ONLINE` 固定文案移除 → FE-05「移除不能反映真实状态的文案」条目。
- 浏览器/WebView 输入法、键盘、语言切换的实际操作 → 需真实环境验收。
- Mock 环境下 `draftStore` 行为已测，HTTP/Tauri 宿主为同一代码路径。

## FE-02 — 统一消息阅读与执行反馈 · 已交付

### 范围

- **共享 Markdown**：`MarkdownText` 升级为共享渲染器（原已支持标题/GFM 表格/链接/列表/围栏代码）——新增 `highlightCode` 轻量 token 高亮（注释/字符串/数字/共享关键字集，非逐语言语法）与 `MdCodeBlock` 复制按钮（copied 反馈 1.4s）。全部经 React 文本节点渲染，模型输出中的 HTML 永不执行（有回归）。复用点：Sun 请求/流式/终态输出、GlobalChat 消息与输出、Venus `.md` 预览（`.txt` 仍纯文本）。
- **滚动跟随**：新增 `useScrollFollow(viewKey)`——距底 ≤80px 才跟随新内容；向上阅读保持位置并显示「有新内容 · 回到底部」pill；`viewKey` 切换时按 Run 保存/恢复 scrollTop。接入 Sun `omni-thread3`（键 = `viewRun.runId`）与 GlobalChat `chat-thread`（键 = `activeRun.runId`）。
- **任务顶部条**：Sun 线程顶部 `.task-head3` 显示 StatusBadge + runId + 当前运行中的工作流阶段（`wfSteps` 中 `running` 项的真实名）+ 耗时（createdAt → output/最新事件/now，无虚构百分比）+ `blocked` 时待处理审批数。
- **ThinkingChain 保留并强化**：完成阶段默认折叠；live/含失败/含待审批行的 seg 保持展开（`attention` 标记）；`task_started` 工具调用按 `p.tool` 归类为文件 📄/搜索 🔍/命令 ⌨ 卡片，未知工具 `other` 原样显示（无假 diff）；`run_failed` 行带 attention 样式，链尾在 `canResume` 时给出真实 `resumeRun` 恢复按钮（resume 是 Service 同 Run 操作，非"重跑"包装）。
- **GlobalChat**：消息与输出改 `MarkdownText` 渲染；保留纯文本原貌（代码块仍 `<pre>` 等宽）。

### 证据

- `tests/frontendMarkdown.test.ts`（5 项）：token 分类、HTML 不执行（`<script>`/`<b>` 原样成文本）、代码块组件存在、GFM 表格、`toolKind` 归类含 other。
- `npm test`：89 项 subtest 全绿（含 FE-01 的 6 项）。
- `test:orbits` / `test:scenes` / `npm run build`：通过。

### 未覆盖（外部验证）

- 滚动跟随/恢复、`MdCodeBlock` 复制、sticky pill 的实际布局与手感 → 浏览器/WebView 实测。
- `useScrollFollow` 依赖 DOM 测量，Node 回归无法覆盖 → 已有同键草稿测试模式证明 store 正确，滚动属 UI 实测项。

## 后续批次占位

- FE-03 审批、产物与可发现性 — 未开始
- FE-05 工作台内容与视觉整合 — 未开始
- FE-04A/B code-server IDE — 未开始（先做嵌入链路验证）
