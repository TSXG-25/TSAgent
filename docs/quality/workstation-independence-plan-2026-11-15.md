# 工作台独立运行修改计划

日期：2026-11-15。代码基线：`6c6ed13ec`。状态：待实施。

本轮仅编写计划；未修改生产代码、重启服务或迁移用户数据。

## 目标与当前证据

每个工作台能独立选择目录、管理会话、发起并行任务、查看结果和控制自己的任务；Omni 提供全局总览，不再是其他工作台唯一的配置入口。

本计划取代 `workstation-repair-plan-2026-11-14.md` 中共享当前会话的产品口径。旧工单的已交付状态保留，不等于新的独立工作台合同已实现。

已确认：

- 当前浏览器配置连接 `http://127.0.0.1:8765`。该服务 health 正常，但调用 `list_graph_executions` 返回 unsupported；当前仓库协议与 dispatcher 已支持此方法。运行代码与仓库不一致，具体进程启动路径待核实。
- 对当前服务四个会话的只读查询返回四个 Run，全部缺失 `origin_planet` 字段。当前前端工作台按此字段过滤，因而无法归类。此证据不能证明升级后这些历史任务一定能恢复归属。
- App 只有一个当前 session，所有工作台共用；workspace 绑定由 session 决定。Sun 提供配置入口，其他台文件接口因此读到相同目录。
- 当前 App 构造的 RunCtx 未填 workspacePath；Sun 仅在自己 Composer 的局部 ctx 中填入。Mars 新增 IDE 启动却要求此字段，因此入口存在缺失数据链。
- Mercury 已接 webSearch，但不是浏览器；搜索结果也不是持久 Run。Mercury、Venus、Mars 尚有全局 activeRun 展示。
- Mars 已有 FE-04B 宿主和嵌入代码，不能再标记“没有接入代码”；真实 WebView 验收仍未完成。

## 实施合同

1. 工作台、会话、目录是三个不同概念。每台保存独立的当前 session；会话仍通过现有 session→workspace 绑定选择目录。
2. 默认创建不同工作台会话；切台恢复该台原会话，不复用 Omni 最近选中的会话。工作台内可新建/切换自己的会话。
3. 不同台可显式选择同一磁盘目录；此时文件共享是用户选择的结果，不能声称文件隔离。即使共享目录，任务归属和会话选择仍独立。
4. 每台的当前 session 选择按 tenant/user/planet 持久化。session ID 使用既有创建入口产生，不通过字符串拆分推断所属台。实现时为会话记录明确的工作台归属，优先复用已有 metadata 字段；仅在没有合适字段时增加字段并迁移调用方。
5. 普通提交始终带目标 planet 和明确 session。Task origin_planet、Run head、接纳时 root 分别是归属、状态、执行目录的权威。既有运行中任务不随目录重绑改变执行根。
6. Omni 汇总当前租户用户可见的各会话任务，支持工作台/会话筛选。其他台默认展示自己的当前会话任务；无归属证据的历史任务仅在 Omni 的“未分类”中展示。
7. 普通任务与显式委派分开：普通任务使用目标台的会话绑定；委派默认沿用现有父任务目录合同，并在界面明确展示继承目录。目标台必须能发现归属于自己的委派子会话任务，不得因其 session 不同而隐形。
8. 共用现有 Service、数据库和协议，不为每台启动 sidecar。调用以明确作用域为准，不能靠临时修改一个共享 client.identity 来模拟多个作用域。
9. Jupiter 全局检索、Pluto 租户资源等已有共享功能保留并标注范围；独立工作台不等于把所有领域数据改成会话私有。

## 工单与验收

### WI-01 / P1 — 对齐实际运行后端

范围：本地开发启动方式、运行记录、必要的离线启动回归。

- 核对 8765 监听进程的可执行文件、工作目录、启动参数和数据库路径，确认是旧进程还是另一份 checkout。
- 检查在途任务后安排更新；不得直接杀死有任务的进程。保留数据库及 workspace 配置，不以清库解决版本错配。
- 用当前源码与明确解释器启动。前后端启动文档记录同一 checkout、端口和数据库，不新增旧协议兼容或 fallback。
- 使用只读 RPC 验证方法注册；新任务归属通过隔离数据库和假 Provider 验证，不拿用户库创建测试任务。

验收：真实入口子进程能返回 list_graph_executions；假 Provider 启动 mars/neptune 任务，get_run/list_runs 都携带正确 origin_planet。用户环境更新后单独记录实际 RPC 结果，不能以仓库测试替代运行环境确认。

**实施记录（本轮）**：

- 进程核实：PID 4603，`.venv/bin/python -m agent.service.local_sidecar --http 8765`，cwd 同 checkout，解释器 anaconda 3.12，库 `~/.tsagent/runtime.db`——同代码库旧加载，已运行约 46 小时。
- 在途检查：`run_heads` 无非终态行（0 个在途 Run），确认后重启；库与 workspace 配置未动。
- 重启命令（记录为同一入口）：`cd /Users/tsxg/Desktop/TSAgent && .venv/bin/python -m agent.service.local_sidecar --http 8765`；DB 默认 `~/.tsagent/runtime.db`（`TSAGENT_RUNTIME_DB` 可覆盖）。Desktop dev `npm run dev` → `http://127.0.0.1:8765/rpc`。
- 实际 RPC 验证：`list_sessions` ok、`list_graph_executions` 返回 `{"ok":true,"result":[]}`（此前 unsupported）——方法注册与仓库一致。
- origin_planet：既有子进程回归已覆盖委派链归属（`test_desktop_sidecar -k "subprocess or origin_planet"` 7 项通过，真实 sidecar stdio 上 uranus→neptune 委派快照携带 origin_planet，普通提交字段贯通 get_run/list_runs）。
- 历史 4 个 Run 的 `origin_planet` 为空属持久数据现状——不做猜测迁移，归入 Omni 未分类。

### WI-02 / P1 — 建立各台独立上下文

范围：App、RunCtx、service client、RunController、会话 DTO/持久化（仅必要字段）。

- 将每台当前 session、当前任务、草稿选择从全局状态拆出，所有权集中在一处。
- client 的租户用户身份可共享；请求使用显式 session。迁移依赖 mutable identity 的 listRuns、refresh、文件、搜索、审批、图执行等调用，不保留两套隐式作用域路径。
- controller 的目录加载、轮询、在途响应按作用域区分；切台不取消后台 Run，也不让旧响应写入另一台目录。
- 为现有工作台会话添加明确归属记录与切换入口；历史无归属会话不自动认领，用户可显式选择接入。

验收：A/B 两台使用同名文件、不同 session；延迟返回 A 请求后 B 状态不变；切回 A 恢复选择和草稿；页面重载恢复各台当前会话；A 运行期间 B 可以提交和取消自己的任务。

**实施记录（本轮）**：

- `session_heads.planet` 列（DDL+ALTER 迁移，与 `workspace_id` 并列）：`create_session(planet)` 显式认领，`SessionSummary.planet` 贯通到 TS `SessionSummary`；`ensure_session` 普通写路径传空不覆盖既有归属。
- `workstationScopes.ts`：`tenant:user → planet → sessionId` 持久化（localStorage）；App `planetSessions` 状态 + `ensurePlanet`（首访建台属会话）+ `selectSessionFor`（认领未标注/本台会话；外台已认领会话仅切视图不夺权）。
- client 移除 `setSession` 可变身份；`listRuns`/`listTasks` 改显式 `SessionScope` 参数，全部工作区调用点迁到 `ctx.sessionId`。
- `RunController` 多作用域：`loadScope(sessionId)` 按会话序号门控、仅替换该会话的目录切片；store 为跨作用域并集（Run 自带 session 定位），外会话 refresh 落自己切片不污染他台；`refreshRun` 对未入目录的 Run 要求显式 session 定位；`openRun` 不再切会话——refresh→select→导航。
- `WorkspaceHost` 按 scene 绑定每台的 `sessionId`/`selectSession`；`WorkspaceTasks` 新增台级会话选择器（本台+未认领列表、显式认领、新建台属会话）；浮动 dock 使用所在台的 scope；GlobalSettings 改显式 sessionId prop。
- 回归：`test_session_planet_claim_and_adoption`（创建认领/未标注接入/写路径不夺权）；controller `loadScope` 切片隔离与跨会话 refresh 落位用例重写；Desktop `npm test` 113 subtest 全绿。
- 未做：跨台并行提交/取消的浏览器实测、页面重载恢复各台会话的实测——归入外部验收。

### WI-03 / P1 — 各台目录选择与文件入口

依赖：WI-02。

- 提取 Sun 现有目录选择/绑定能力，形成各台共用的目录栏，不复制九份绑定逻辑。
- 每台显示当前目录，可选择/更换；未绑定时使用现有应用管理默认目录合同，并明确显示实际路径。不得回退 cwd、Omni 最近目录或任意历史目录。
- 文件列表、附件选择、普通任务提交和 Mars IDE 从同一个已解析绑定读取 root。
- 保留 A22 文档 root/digest/redacted 合同；改绑后旧草稿不得保存进新目录，已接纳 Run 继续用原 root。

验收：Mars 选 A、Neptune 选 B；两台文件读写各在对应目录，修改 Omni 绑定不影响二者；同台重绑拒绝旧草稿保存；接纳后改绑仍执行在原 root。

**实施记录（本轮）**：

- `sessionWorkspace.ts::resolveBoundPath`：共用 `listSessions + listWorkspaces` 解析会话→workspace→path，未绑定返回 ""（不回退 cwd/历史目录）。
- `WorkspaceHost`：按每台 session 解析绑定；`wctx.workspacePath` = 生效根（绑定路径或经 `listDirectory("")` 解析的后端默认根）；sidecar 原生选目录对所有 scene 注册。
- `WorkspaceDirBar`（共用目录栏，非 Sun 各台顶栏）：显示生效根（未绑定标"默认"徽标）、选择目录（`pickFolder→upsertWorkspace→assignSessionWorkspace`）、解绑；同一绑定贯通文件列表（`listWorkspaceFiles` 会话域）、附件（`attachFiles` 会话域）、任务提交（接纳时后端解析绑定）与 Mars IDE（`ctx.workspacePath`）。
- 回归：`sessionWorkspace.test.ts`（绑定解析/改绑/解绑/他会话不共享）；既有 `documentSession` 同会话重绑拒写与接纳后原 root 合同不变。Mars A/Neptune B 双台目录实测归外部验收。

### WI-04 / P1 — 本台任务闭环与 Omni 总览

依赖：WI-02；文件任务同时依赖 WI-03。

- 统一普通提交、GlobalChat、全局新建按钮的目标来源：工作台内默认当前台，Omni 内默认 Omni；显式跨台发送展示目标。
- 本台标题、状态、输出、暂停/取消统一读取本台选中 Run，移除 Mercury/Venus/Mars 等残留全局 activeRun。
- Omni 增加跨会话目录查询与筛选。若现有 list_runs 只支持单会话，直接扩展合同并迁移 Python/TS/mock，不靠循环加载所有会话来长期维持双路径。
- 保留分页与显式 Run 定位；进入 Omni 查看详情不得改变源工作台的 session 和任务选择。
- 目标台提供可见的委派任务入口，包括属于该台但位于子会话的 Run；点击后按 Run 的真实 session 查看，不改写事实。

验收：从每台提交的入口检查请求归属；Mars/Neptune 并行任务分别出现在本台且都出现在 Omni；跨台委派在目标台可发现；历史未分类任务不被猜测归属；打开分页外 Run 和返回原台正常。

**实施记录（本轮）**：

- 租户级目录：`session_id=""` 作为租户范围直通 `list_run_heads`（新 `list_run_heads_tenant(tenant, origin_planet?)` 经 `task_heads.origin_planet` 连接过滤）；`ListRunsRequest`/`SessionScope` 增 `origin_planet`——子会话委派 Run 在目标台可发现，非循环加载所有会话的双路径。
- `RunController.loadTenantCatalog`：Omni 用租户级范围加载，不改写当前工作台作用域；`mock` 同步支持会话/归属过滤。
- 各台闭环：`WorkspaceTasks` 每行显示 status/失败摘要 + pause/cancel 走本台 scope + "在 Omni 打开"按 Run 真实 session 跳转（`openRun` 不改写事实）；Mercury/Venus/Mars/Neptune/Jupiter/Saturn/Pluto 本台标题/状态/控制改读 `useWorkspaceCurrentRun`（tenant+session+originPlanet 推导），清除全局 `ctx.activeRun` 残留。
- 提交归属统一：工作台内提交/委派带本台 `originPlanet`+明确 `ctx.sessionId`；Omni 提交归属 Omni（未分类）。
- Omni 会话栏增工作台筛选（全部/各台/未认领），`sun.filter-all`/`sun.filter-unclaimed` i18n 双语言；历史无归属任务归入"未认领"不猜测。
- 回归：`test_list_run_heads_tenant_scopes_and_origin_filter`（跨会话汇集/归属过滤含子会话委派/未分类只现于无筛选）；Desktop `npm test` 115 subtest 全绿。
- 未做：Mars/Neptune 并行任务的双台浏览器实测、打开分页外 Run 返回原台的实测——归外部验收。

### WI-05 / P2 — Mercury 明确搜索与浏览器能力

依赖：WI-02、WI-04。

- 本轮交付搜索工作台：明确搜索表单、结果、来源链接、错误与普通 Agent 任务是两条不同入口。
- 查询展示真实结果，来源打开真实链接；搜索不伪装成持久 Run。用户要基于搜索继续研究时，提供明确的“基于结果创建任务”，将选中来源经既有上下文合同传入。
- 搜索状态、来源和日志随 Mercury 自己的会话隔离；顶部展示本台任务。
- 浏览器导航、页面点击和登录状态不在本轮冒充交付；如确认需要完整浏览器工作台，单独制定适配器范围。

验收：假搜索 provider 覆盖成功、空结果、不可用；来源进入研究任务请求；Stop/切会话丢弃旧响应。真实联网搜索与页面访问另列外部验收，不调用真实模型替代离线测试。

### WI-06 / P1 — Mars IDE 接入正确的独立目录

依赖：WI-02、WI-03。复用 `e37292d1a` 的 FE-04B 实现。

- 修正 RunCtx.workspacePath 缺失链路；IDE 和文件编辑器使用同一权威绑定，Mars 本台即可选目录并启动。
- 审核 start/status/stop 与 iframe 的现有生命周期：目录切换、启动未完成时离开、启动失败、宿主退出。只修有实际遗漏的路径，不另建 IDE 服务框架。
- 使用独立 user-data-dir；不接管 brew 的常驻实例。端口/就绪使用已有探针结果，关闭时只处理本宿主拥有的进程。
- 浏览器 dev 无 Tauri bridge 时明确提示 IDE 不可用，基础文件编辑仍可使用。

验收：fake bridge 断言收到 Mars 的 root；延迟启动完成不能绑定旧会话或留下未管理实例；停止范围不涉及 brew 实例。Rust/TS 适当离线检查通过。真实 WebView、终端、剪贴板与退出验收单列。

### WI-07 / P1 — Uranus 在独立上下文中完整执行

依赖：WI-01、WI-02、WI-04。

- 图列表/执行列表/运行入口使用 Uranus 明确作用域，不依赖 Omni 已选中任务。
- 去掉“必须先在 Omni 创建父 Run 才能运行流程”的用户前置操作。实现前确认图执行的父 Task/Run 合同：优先由既有接纳入口创建真实的图执行归属，不创建假 COMPLETED Run 充数。
- 图定义编辑、历史摘要、叠加的既有身份约束保留；子 Run 的 profile/target_planet 与继承 root 贯通到目标台任务入口。
- 当前后端缺方法时保留真实错误，修启动版本，不返回空列表伪装成功。

验收：新会话直接从 Uranus 保存并运行两节点图，无需先去 Omni；假 Provider 让上游实际写文件、下游实际读取，不能只 mock 路径字符串替换；子 Run 出现在目标台，历史执行不叠加到不一致定义。

### WI-08 / P2 — 跨台验收与文档收口

依赖：WI-01～07。

- 更新旧计划关于共享会话、Mars 未接入等过时表述，保留变更历史。
- 建立一条基础离线跨台链路：各台独立绑定→并行接纳→状态/产物→Omni 汇总→跨台委派→返回源台。
- Python/TS/mock/wire fixture 同步变更；测试检验业务结果，不只检查纯函数返回。
- 交付记录逐项列出代码提交、实际执行检查和未覆盖项；禁止以测试总数宣称真实 UI 全部验收。

## 排期与交付边界

顺序：WI-01 → WI-02 → WI-03/WI-04 → WI-05/WI-06/WI-07 → WI-08。

每批提交前先跑受影响离线回归；最终一次完整离线门禁。屏蔽 DeepSeek key、强制 Ollama，并将外部 provider 指向确定性不可达端点或假端点。Desktop 使用当前可用 Node 22；默认 Node 的动态库问题属于环境修复，不在业务提交里绕过。

本轮不做：多 sidecar 架构、任务归属猜测迁移、通用权限平台、旧协议兼容、完整浏览器适配器、Dify 条件/循环引擎。

## 真实环境验收交接

1. 用更新后的同版本前后端，在各台选择不同目录、同时发起任务、切台查看进度；期望归属、目录、控制目标准确。
2. Tauri Mars：启动 IDE，确认实际目录；编辑文件、终端执行、快捷键/剪贴板；切目录、离页和退出后检查所拥有进程生命周期。
3. Mercury：真实搜索服务及来源导航；若外部服务失败，界面如实显示，不能与工作台作用域缺陷混为一谈。
4. Uranus→Neptune：真实媒体任务、产物读取与播放；需要可用模型、工具和 ffmpeg，另行提供环境结果。

以上不属于基础离线测试结论。实施方应完成可执行的代码与离线验收，再提供操作步骤交接，不以等待真实环境验收为由搁置独立的代码工作。
