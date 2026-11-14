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

## FE-04A — 嵌入链路验证 · 阻塞（待前置条件）

- 本机未安装 `code-server`（`which`/`mdfind`/`brew list` 均无二进制，仅 Homebrew formula 存在）。计划约定"用户安装 code-server"，故验证链路的第一步不可进行。
- Tauri 宿主现状：仅 `sidecar_spawn`/`sidecar_write`/`sidecar_close`（stdio JSONL 专用），尚无 `ide_start/status/stop`。宿主适配器属 FE-04B，按计划在链路验证通过后实施。
- **解除条件**：用户安装 code-server 后可执行探针 `code-server --bind-addr 127.0.0.1:0 --auth none <workspace>`，验证 Tauri host 启停 + WebView 加载/终端/快捷键/剪贴板。

## FE-03 — 审批、产物与可发现性 · 已交付

### 范围

- **单一审批状态**：新增 `src/app/approvals.ts`——按 `tenantId:sessionId` 一份轮询存储（8s）+ 一个 `decideSessionApproval` 入口。TaskTray 与 Sun 内联审批卡片共享同一状态与操作路径，原有各自身份的重复维护删除。TaskTray 审批卡与任务卡均可跳转所属 Run（`ctx.openRun`）。
- **审批卡片**：当前任务内每条 pending 审批一张卡（原先仅显示首条），含动作、目标范围、风险等级与参数详情；decide 失败留在原卡片（`apprErr`/`cardError`），成功才清除。TaskTray 同构。
- **产物交付**：完成/失败/取消后的任务均显示 artifacts（原条件已覆盖 terminal 态，保留）；每项含名称、类型、验证状态（`a.status` 原样）+ 三个真实操作——预览走 `readArtifact` 合同（含 stale/missing/unsupported 提示）、「复制引用」复制 opaque `artifactId`、仅 `a.path` 存在时提供「在访达中显示」（复用 `revealPath`，不拼接本地路径）。移除原无操作的假「预览 · Forge」文本。
- **可发现性**：PlanetSwitcher 增加关键词搜索（id/name/workspace/desc/mvp 命中）+ 最近访问组（localStorage 前 4 项），保留星图项与 ⌘0–9；`@`/`/` 菜单维持只列真实星球目标、选择仅预填不发送；附件 chip 增加类型后缀。
- **会话列表**：沿用 `titleOf` 标题推导 + 相对时间 + 本地化状态标签；搜索仍为已加载会话客户端过滤（无新增后端搜索）。

### 证据

- `tests/frontendApprovals.test.ts`（2 项）：同会话双订阅者收到同一存储的同步发射；decide 经 Mock service 写回后共享状态刷新。
- `npm test`：91 项 subtest 全绿；`test:orbits`/`test:scenes`/`npm run build` 通过。

### 未覆盖（外部验证）

- TaskTray/审批卡/产物预览/Switcher 搜索的真实交互与布局 → 浏览器实测。
- `readArtifact`/`revealPath` 在 HTTP/Tauri 宿主下的实际行为已沿用既有合同，未改 wire。

## FE-05 — 工作台内容与视觉整合 · 已交付

### 范围

- **去除假状态**：移除全部 10 处固定 `● ONLINE`/`● SYSTEM ONLINE`（earth/jupiter/mars/mercury/neptune/pluto/saturn/sun/uranus/venus），保留真实计数；`.sys-ok` 样式不再被任何工作台引用。
- **Mercury**：搜索结果来源 URL 恢复真实导航（`target="_blank" rel="noreferrer"`），原先 `preventDefault` 使来源链接是死链。
- **Earth**：裁决表单上方新增「已提交参数（被评价 Run）」与「当前滑块」两行——前者由 `parseRunParams(activeRun.request)` 反解（单一事实源，A23 修正的同一口径），后者为当前滑块值；两组参数的可区分性可视化。
- **Jupiter**：forget 成功后显示反馈文案并清除失效选中项；选中 artifact 时提供「预览」按钮走 `readArtifact`（sessionId/runId/artifactId 取自记录自身），切换选中项时清空预览。
- **Saturn**：任务详情新增「下次触发」（`nextFireAt`）与「最近执行」（`lastRunId` → 刷新并跳转所属 Run）；暂停态沿用 `chron-card.paused` 真实状态类。
- **Uranus**：节点执行状态的 `runId` 改为可点击链接——`refreshRun`（使用 step 自带的 `sessionId`，支持跨会话子 Run）后 `openRun`；`SKIPPED`/`FAILED_*` 状态文本照旧原样显示（合同即失败/跳过原因）。
- **Neptune**：画布底部新增产物元信息行（`kind`·`size` + Run 跳转链接）；空态/二进制提示本地化。
- **Pluto**：已裁决母题显示「已收录进记忆」+ `resolutionRunId` 跳转链接（收录证据）。
- **弹层行为**：Sun 三类弹层（文件夹浏览/工作区编辑/通用对话框）统一 Escape 关闭 + 关闭后焦点还原到触发控件（原先仅 mask 点击/输入框内 Escape）。
- **可访问性**：新增 `.ven-link`（按钮式链接，`:focus-visible` 有焦点环）；新交互均为原生 button。

### 审核修正（170f21e…3209b2 代码核对，五项；横跨 FE-01/03/05 缺口）

- **[P1] 跨会话 Run 导航**：`openRun(runId, sessionId?)` 收拢为单一入口——`refreshRun` 加载目标 → `selectSession` 切到所属会话 → `selectRun` → 导航。原先仅 `selectRun`+路由，不加载、不切会话，且 App 闭包中的旧 `runs` 列表会挡住首载 Run；Pluto 历史证据链接甚至未先加载。Uranus/Saturn/Jupiter/Neptune/Pluto/TaskTray 全部走同一入口。
- **[P1] GlobalChat 附件丢失**：委派分支传 `draft.files`（`contextRefs`），直发分支 `createRun` 传 `contextRefs: requestDraftState.files`；原先附件在共享草稿中存在却从不进入请求，成功后还被一并清空。
- **[P2] 在途输入被吞**：新增 `clearDraftIfUnchanged(key, submitted)`——仅当存储草稿仍等于提交快照时清除；提交等待期间输入的新内容（含共享草稿另一输入框）保留。AgentComposer/GlobalChat/createRun 三处统一。
- **[P2] Jupiter 预览串扰**：`previewFor` ref 绑定请求归属，响应提交前校验仍是当前选中项；选中变化作废在途请求（覆盖成功与失败两条回调）。
- **[P2] 中英切换**：App 根 `useLang()` 订阅——语言变更即整树重渲染（原先仅 GlobalSettings 订阅，其余组件靠偶遇状态更新零散切换）。九个工作台正文文案接入字典：Mercury/Saturn/Uranus/Earth/Jupiter/Mars/Neptune/Pluto/Venus 的标题、按钮、空态、提示、placeholder、prompt/confirm；领域术语（参数名、字段名、Run/ID）与状态计数保留原文。Neptune 页签由中文键改为 id 键 + `TABS` 映射。

### 证据

- `npm run typecheck` 通过；`npm test` 94 项 subtest 全绿。
- 新增回归：`frontendDraftI18n` 延迟 Promise 用例（在途输入不被清除 + 未变草稿正常清除）；`runController` 跨会话用例（`refreshRun` 带 sessionId 加载 foreign run → `selectRun` 接受；加载前 `selectRun` 拒绝）。
- `test:orbits`/`test:scenes`/`npm run build` 通过。

### 未覆盖（外部验证）

- 十个工作台在浏览器/WebView 的实际布局、焦点可见性、Escape 交互与 zh/en 切换覆盖 → 浏览器实测。
- Mercury `target="_blank"` 在 Tauri WKWebView 下打开外部浏览器的行为 → macOS 实测。
- Mars 工作台维持现有 textarea 编辑器（FE-04 未进行，见阻塞记录）。
