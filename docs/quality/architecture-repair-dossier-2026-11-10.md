# TSAgent 架构审计与修复交接档案

审计基线为 2026-11-10 的 `codex/github-upload`，提交 `6beb0845c1cd20d201d274fbf8a9808d6fe57af1`。问题和行号均以此版本为准。档案面向负责修复的工程师，每张工单给出责任模块、影响、改动方向和合并验收。

## 架构判断

CLI、Python API 与 Desktop 已汇入 `AgentService`。普通执行经 Runtime、Intent/Goal、原生工具调用与效果校验。专用 Workflow 已接入持久 checkpoint 和恢复协调器。SQLite 已有 Run 预留、事件序列、终态事务、worker lease 和 fencing。应保留这条主链以及现有的崩溃、幂等和效果证据测试。[CLI](../../main.py) · [Python API](../../agent/api.py#L39) · [Service 工厂](../../agent/service/factory.py#L14) · [恢复入口](../../agent/service/runtime_launcher.py#L63)

目前最需要修的是**持久事实与控制路径分离后，没有为两者之间的失败定义统一语义**。审批结论写进 SQLite，执行者却等待进程内信号。定时任务先推进时间，再创建 Run。画布先分步写 Task/Handoff，再启动子 Run。服务有持久终态，但投影仍可由 Workflow 索引推断 Run 成功。这些缺陷分别影响执行授权、任务是否遗漏、重试一致性和用户看到的完成状态。

第二类问题来自**同一个决定由多个调用方拼装**。委派配置、workspace、会话、事件映射和 Provider 设置跨 Python、SQLite、Rust/TS 及应用入口传播，容易出现同名功能走不同语义。重构应按用例收回决策权，逐项迁移调用方，用可执行契约验收。文件行数只用于理解改动集中度。

### 派工总览

| ID | 优先级 | 工单 | 证据 | 责任模块 |
| --- | --- | --- | --- | --- |
| A01 | P0 | 审批以持久决定唤醒执行并要求明确批准 | 离线复现、静态 | Service / Runtime |
| A02 | P0 | 统一公开 Run 终态与输出的提交语义 | 投影离线复现、静态 | Runtime / Projector |
| A03 | P0 条件 | 明确本地 RPC 的身份、路径和能力授权 | 静态、需产品裁定边界 | Service / Sidecar / Desktop |
| A04 | P1 | 启动失败、取消、暂停与退出按 Run 生命周期收敛 | 静态 | Service / Runtime |
| A05 | P1 | 调度启动与每次触发持久化 | 离线复现 | Scheduler / Store |
| A06 | P1 | 统一委派创建、运行与重试 | 离线复现、静态 | Service / Desktop |
| A07 | P1 | 明确画布边语义及图级运行事实 | 离线复现、需产品裁定边语义 | Orchestration / Desktop |
| A08 | P1 | SQLite 在验证身份后执行原子迁移 | 离线复现 | Store |
| A09 | P1 | 让控制 RPC 绕开长订阅阻塞并统一关闭 | 离线复现、静态 | Sidecar / Desktop |
| A10 | P1 | 两层记忆统一身份与删除语义 | 静态、外部契约待验 | Memory / MCP |
| A11 | P1 | Desktop 会话与 Run 事件有单一所有者 | 静态 | Desktop |
| A12 | P1 | 建立真正离线且覆盖双端的合并门禁 | 测试实测、静态 | QA / CI |
| A13 | P2 | 把 Provider 配置和路由状态收归应用及 Run | 静态 | LLM / Composition |
| A14 | P2 | 收紧 Runtime 状态和工具执行接口 | 静态 | Runtime / Executor |
| A15 | P2 | 把 Session 生命周期与 workspace 解析收归同一模块 | 静态 | Context / Store |
| A16 | P2 | 为跨语言 wire 定义单一权威契约 | 静态 | Service / Desktop |
| A17 | P2 | SQLite 竞争、历史增长与可观测性 | 静态、容量待测 | Store / Operations |
| A18 | P2 | Desktop 可分发性与受管进程生命周期 | 静态、安装包待验 | Desktop / Release |
| A19 | P2 | MCP 发现状态与失败恢复有明确生命周期 | 静态 | MCP |
| A20 | P3 | 修订架构文档和前端样式权威 | 静态 | 架构负责人 / Desktop |

P0 指安全授权或公开执行事实可能错误，应优先处理。A03 在 HTTP 被用于共享设备、跨用户或被当成可信身份边界时为 P0。在明确的单机开发模式下，先完成边界文档和发布隔离，再按计划补足权限。P1 指中断、重试、调度或客户端一致性。P2 是有实证的结构性债务。P3 用于降低日后交接成本。标记静态的条目，应先把证据转换成失败测试或小型测量，再决定改动大小。

## 第一批 · 执行安全与公开事实

### A01 · 审批以持久决定唤醒执行

**问题和影响。** `request_approval` 先持久化记录，再等待 `self._approval_waiters` 内的事件。`decide_approval` 只唤醒同一 Service 实例内的 waiter，键也只有 approval ID。临时库实测，B 实例把批准写入 SQLite 后，A 实例仍在等待；经 A 重放同一决定才唤醒。另有静态边界，Loop 只检查 `DENIED`，并在缺少 delegation 时直接返回允许路径。此点要结合实际入口的 R2+ 工具可达性确认，审批模块本身应要求明确的 `APPROVED`。[请求与等待](../../agent/service/service.py#L1901) · [决定](../../agent/service/service.py#L1999) · [工具审批门](../../agent/loop/runner.py#L595)

**修复范围。** 以已提交的审批记录作为权威，等待者用 tenant/session/run/approval 的完整范围观察。进程内通知可加速，跨实例正确性由持久状态保证。收拢审批记录和等待状态的发布，明确重复决定、取消、超时及重启后的待批处置。仅对同一持久操作的明确批准放行，缺失能力时返回可诊断的错误。

**验收。** 两个 Service 连接同一临时库，B 批准后 A 的假动作恰执行一次。拒绝、未决、错误绑定、无审批能力、取消先到、重复或冲突决定均不启动假动作。中断后原操作有明确定义的恢复或终止结果。

### A02 · 公开 Run 终态与输出原子可见

**问题和影响。** `RunProjector` 在 Run head 仍非终态时可从 Workflow index 投影 `COMPLETED`，而 Launcher 随后才用 Run 事务提交终态、输出和工件。恢复期间两个事务之间可能读到先于正式发布的成功。用真实 SQLite 读快照替换其中的 Workflow index，投影实测得到 `head=RUNNING, public=COMPLETED, output=None`。该探针验证投影规则，完整恢复中的竞态窗口仍需入口测试。保留既有终态 head 优先逻辑和 Launcher 的原子提交。[投影分支](../../agent/service/run_projector.py#L226) · [恢复发布](../../agent/service/runtime_launcher.py#L306) · [新 Run 发布](../../agent/service/runtime_launcher.py#L489)

**修复范围。** 明确 Run head、终态事件、RunOutput 和 Artifact facts 的提交边界。Workflow 索引提供阶段与恢复进展。若需支持现存数据中缺少正式终态的记录，在确定支持范围后用显式迁移或诊断处理。检查公开输出与 verifier 语义是否能从同一快照解释。

**验收。** 在 Workflow index 提交后、Run terminal bundle 提交前冻结执行，通过 `get_run` 读到真实 Run 状态。此窗口插入取消或模拟终态提交失败，成功不得提前出现。正式成功后一次读取获得一致 revision、终态和输出。覆盖普通运行、Workflow 恢复和空输出的约定。

### A03 · 本地 RPC 身份和 workspace 授权

**问题和影响。** HTTP 明确无认证，只做 Host/Origin 检查。Service 通过 DTO 中的 tenant、user、session 定位数据，`start_run` 接收并解析显式 `workspace_root`，`get_run` 的 Projector 会比对请求中的 user ID 和持久所有者记录，身份值仍由调用方提交。Native 文件选择、附件和 Run root 由不同路径处理。当前机制支持可信单机客户端的数据分区。共享客户端或远程服务所需的调用方身份及资源权限，在此实现中缺少同一权威。[HTTP 边界](../../agent/service/local_sidecar/http_server.py#L43) · [Run root](../../agent/service/service.py#L230) · [Run 查询](../../agent/service/service.py#L349) · [所有者匹配](../../agent/service/run_projector.py#L204) · [附件](../../agent/service/service.py#L566) · [Run context](../../agent/service/context_factory.py#L95)

**修复范围。** 先决定支持可信单机、共享设备还是跨进程多用户。由可信 host 或 transport 建立 principal，由 Service 用它检查操作和 workspace 授权。以明确授予的 workspace 或附件导入授权作为文件范围输入。开发 HTTP 独立声明并限制使用。把批准权、Profile policy、MCP 工具能力和诊断数据纳入同一权限矩阵。与 A06/A15 协调 workspace 继承，避免各自定义规则。

**验收。** 只用测试 principal、临时文件和假工具，覆盖允许和拒绝的跨租户、跨用户、错误 session、越界路径、附件授予及审批决定。开发 HTTP 与打包 Desktop 的启用条件有自动检查。授权失败返回稳定错误且不改变持久状态。

### A04 · Run 的启动、暂停、取消与关闭

**问题和影响。** start reservation 先落库，deferred context 构造或 Launcher 出错时，`_run_start` 仅写进程内 `_task_errors`，公开查询仍读 durable snapshot。暂停依赖当前实例的 `asyncio.Event`，进程消失后同一恢复入口转向 Workflow checkpoint，普通 Loop 没有这种 checkpoint。Service close 等待当前 Run，未在本处统一收束 scheduler 和辅助任务。现有 orphan sweeper 可收敛部分异常，延迟错误与本地暂停仍需要明确语义。[Start](../../agent/service/service.py#L280) · [后台错误](../../agent/service/service.py#L310) · [暂停](../../agent/service/service.py#L2054) · [恢复](../../agent/service/service.py#L2102) · [关闭](../../agent/service/service.py#L2346) · [无 checkpoint](../../agent/service/runtime_launcher.py#L88)

**修复范围。** 明确接纳后的启动失败属于哪种持久终态，由拥有对应 fence 的生命周期入口提交。统一取消和退出的协调规则。公开区分同进程暂挂、待审批、可从 checkpoint 恢复及应终止的状态。Python API `run()` 返回字符串的便捷合同可保留，文档需给出需要 Run ID、失败或恢复信息的调用入口。[Python API 结果](../../agent/api.py#L92)

**验收。** 注入 context、Launcher、进度投影错误，已接纳 Run 有可重读的终态和原因。覆盖同进程 pause/resume、无 checkpoint 重启、两次相同取消、暂停中关闭。关闭后有界回收任务和 Store。EOF 的业务语义沿用现有合同，由测试约束。

## 第二批 · 持久编排、数据和传输

### A05 · 调度启动与每次触发持久化

**问题和影响。** Scheduler 只由 create/list/update schedule 启动。临时库实测，重建 Service 后等待一个 tick，已到期计划仍未触发，scheduler 未启动。Store claim 会先推进 `next_fire_at` 并增加 `fire_count`。在创建 Run 前停下，重读得到 fire_count=1 且没有 Run ID。运行冲突及其他异常当前仅输出 traceback。`target_planet` 已存储，当前触发构造的 StartRunRequest 未携带该配置。[启动调用点](../../agent/service/service.py#L1757) · [执行循环](../../agent/service/service.py#L1819) · [认领事务](../../agent/runtime_store/sqlite.py#L1964)

**修复范围。** 将调度归入明确启用调度的 Service host 生命周期。为每次触发定义稳定 ID、待接纳与已接纳记录、失败/跳过状态。将触发记录与 Run 预留放进同一事务，或将待接纳记录持久化并幂等消费。定义重叠、暂停、关机期间漏触发及目标路由语义。记录自动评估的独立结果和失败。

**验收。** 使用假时钟及两个独立 Store，测试无 UI 的重启到期任务、认领后中断、重复处理、占用中的目标 session、暂停和进程退出。每次触发都有可查询结果，同一触发最多产生一个 Run。

### A06 · 统一委派创建、执行与重试

**问题和影响。** Runtime 的 `spawn_subtask` 创建 Task/Handoff、传递 parent workspace 和 profile、启动子 Run 并回写目标 Run。Desktop 的 `delegate_run` 只写 Task/Handoff，后续由客户端组装 `start_run`。图执行再拼一次且只传 `delegation_depth`，容易丢掉 profile 的 permission/model/skills 和上下文。A07 的同请求重试已实测留下额外 Handoff，证明分步创建缺少统一幂等归属。[Runtime 委派](../../agent/service/service.py#L765) · [UI 委派](../../agent/service/service.py#L1688) · [桌面启动](../../apps/desktop/src/app/delegate.ts#L41) · [图节点启动](../../agent/service/service.py#L1359) · [Profile 消费](../../agent/loop/runner.py#L282)

**修复范围。** 由 Service 定义一个持久委派用例和稳定请求身份，统一子 session、workspace、选定 profile、上下文引用与 handoff 关联。明确用户提供的模型偏好与服务端限制的优先级。若产品保留「先准备、后运行」，两个阶段各有状态和幂等契约。调用方继续控制页面展示以及是否等待结果。

**验收。** 对同一个 parent 和约束，从 Runtime、Desktop wire 和画布路径创建的子 Run 获得等价权限与 workspace。跨进程重试、网络断开、在 Task/Handoff 后故障、父 Run 取消与子 Run 完成竞态，均有可查询的一致状态。

### A07 · 画布边语义和图级运行事实

**问题和影响。** `run_graph` 注释描述按依赖顺序执行，代码只做拓扑排序并逐次启动。用被阻塞的 Launcher 实测，父节点未完成时已返回两个子 Run。相同请求重试返回 `IDEMPOTENCY_CONFLICT` 且多一条 Handoff。晚节点的空文本验证发生在前面节点已经启动后。图编排与专用 Workflow checkpoint 是不同执行类型。[排序](../../agent/service/service.py#L46) · [画布执行](../../agent/service/service.py#L1322)

**修复范围。** 先明确画布边表示启动顺序还是成功依赖，按产品语义修改 UI 文案和执行规则。整个图预检后再写执行事实。以图版本和请求 ID 标识一次执行，稳定记录各节点、依赖、输出及停止原因。复用 A06 的委派用例和现有 Run contract。专用 Workflow 保留现有恢复模型。

**验收。** 两节点用假执行器卡住第一个节点，验证选定边语义。覆盖循环、无效节点、失败依赖、重复请求、执行中重启，以及图编辑后旧执行仍使用原版本。相同请求不产生额外 Task/Handoff/Run。

### A08 · 统一 SQLite 迁移入口

**问题和影响。** Store 在检查 `schema_version` 和 `store_generation` 前执行 DDL。Workspace 读写还会尝试重复 `ALTER TABLE` 并吞掉全部 `OperationalError`。构造未知版本且缺少一列的临时库，实测 `open` 返回 `SCHEMA_INCOMPATIBLE` 后缺列已被补上。版本拒绝未保护数据库 schema。[初始化与版本检查](../../agent/runtime_store/sqlite.py#L733) · [重复 DDL](../../agent/runtime_store/sqlite.py#L2189) · [调用点](../../agent/runtime_store/sqlite.py#L2295)

**修复范围。** 先区分空库、可迁移版本、不兼容版本及 generation 不符，再在 Store 启动的一条路径中事务化 DDL 和元数据更新。删除正常读写的 DDL。迁移失败保留真实错误，历史兼容由仓库已支持的版本范围决定。

**验收。** fresh DB、每个声明支持的旧版、未知版本、错误 generation、迁移中故障和两个连接同时打开。验证未知版本的 schema/元数据保持原样，失败可重试，已迁移数据的 Run、工件、事件与 workspace 保真。

### A09 · Sidecar 的长轮询、控制请求和关闭

**问题和影响。** JSONL server 对一行完成 `await dispatch` 才读下一行。订阅等首个事件最多约 8 秒，后续每次事件间隔窗口为 0.25 秒，累计等待取决于批次。用内存流与假订阅实测，后排 health 未被分发。Cancel、审批决定等控制请求也走该队列。HTTP 另外使用线程、300 秒 future wait，结束时只在该处请求 loop stop，协议和关闭语义与 JSONL 分叉。[JSONL 读取](../../agent/service/local_sidecar/server.py#L31) · [订阅窗口](../../agent/service/local_sidecar/dispatcher.py#L522) · [HTTP 生命周期](../../agent/service/local_sidecar/http_server.py#L71) · [TS correlation](../../apps/desktop/src/service/tauriSidecarTransport.ts#L46)

**修复范围。** 在同一 host 调度下区分可等待订阅和控制请求。限制 in-flight 数、订阅总时限及请求体尺寸，序列化输出写入并按 request ID 关联。为重试、客户端中止、EOF 和 shutdown 明确由谁关闭迭代器、结束任务和 Service。复用现有 RPC envelope。

**验收。** 用内存流或 fake sidecar 同时发挂起订阅、health、cancel、approval 和 shutdown，控制请求在规定的本地时间预算内完成。覆盖乱序响应、未消费事件重连、断流、超时、重复请求及输出仍为有效 JSONL。HTTP 用例在允许 loopback 的独立作业验证。

### A10 · 两层记忆统一身份与删除语义

**问题和影响。** Durable Facts 走 tenant/user 命名空间、证据与策略。ReMe 搜索只发送 query/limit，后台蒸馏发送原始回合、session ID、request ID 与提示，没有传递相同的 tenant/user 范围或复用确定性的事实写入门禁。Service 的 forget 仅删除 Durable Facts。TSAgent 端无法证明共享 ReMe 实例时的检索隔离、敏感信息处理、删除传播和重启投递；实际行为取决于外部服务和部署方式。[事实删除](../../agent/service/service.py#L1432) · [ReMe 投递](../../agent/runtime.py#L1099) · [ReMe 接口](../../agent/mcp/reme.py#L34) · [工具入口](../../agent/loop/runner.py#L774)

**修复范围。** 先定义哪些数据允许进入两层，哪个层支持查询、删除和重置，各自的身份和保留期是什么。把授权、脱敏和 namespace 确定放在 TSAgent 发送前。若 ReMe 无可靠的隔离接口，按明确的单用户启用边界部署或隔离实例。若需要保证写入，使用持久投递记录与幂等 ID。若只保证尽力写入，提供可观察的丢弃及失败状态。

**验收。** Fake ReMe 捕获 payload，覆盖同名 session 的双租户、两用户、敏感样例、取消、请求重放、忘记与重置。需要验证部署侧的真实隔离和删除时，独立使用测试账号和合成数据。

## 第三批 · 客户端与质量门禁

### A11 · Desktop 会话和事件状态归一

**问题和影响。** App 维护选中 session，Local client 可变 identity 保存 session，Sun 还维护本地 session。Sun 的轮询闭包依赖仅 `service`，却读取 `sessionId` 和 `runs`，一次刷新可从不同来源获得审批和参与者的 session。RunController 已维护长轮询，Sun 另有 3 秒刷新。RunController 首次 hydrate 只读一批 events，之后仅对 live Run 开轮询，重开已完成且事件数超过单批上限的 Run 可能看不到完整时间线。[App 选择](../../apps/desktop/src/App.tsx#L199) · [Sun 刷新](../../apps/desktop/src/workspaces/sun/SunWorkspace.tsx#L105) · [组件轮询](../../apps/desktop/src/workspaces/sun/SunWorkspace.tsx#L181) · [Controller hydrate](../../apps/desktop/src/features/runs/runController.ts#L252) · [事件批次](../../agent/service/local_sidecar/dispatcher.py#L540)

**修复范围。** 把选中 session 和查询 key 收归单一状态所有者。业务请求传显式 session，异步结果按发出时的 key 合并。RunController 管理 Run 快照、cursor 和订阅，UI 订阅投影。给历史事件分页，明确终态页面的读取停止条件与 retention gap。把跨组件 window 事件、请求异常和不安全类型转换按本用例逐步迁移，建立用户可见错误状态。

**验收。** 测试 A→B→A 快速切换、旧响应晚到、审批和参与者范围、两个同时运行的会话、断线重连及超过单批上限的已完成 Run。验证同一 Run 只有一个刷新拥有者，事件去重并单调推进。

### A12 · 真正离线、双端和跨端的 CI

**问题和影响。** Python CI 含回归、架构和基准门禁，未运行 Desktop 的 typecheck/test/build。测试依赖仍泄漏到机器环境。本次普通回归有 7 个 MCP setup 错误，fixture 试图删除真实用户目录下的缓存；一个记忆断言失败，因为测试仍调用了不可达 Provider。另有 3 个 loopback 或 Seatbelt/宿主权限用例未通过。Desktop 的 npm test 本地通过，但现有套件集中在 client/controller。[CI](../../.github/workflows/ci.yml) · [MCP fixture](../../tests/test_mcp_tools.py#L76) · [记忆测试](../../tests/test_rb_contracts.py#L13) · [Desktop 测试入口](../../apps/desktop/package.json#L21)

**修复范围。** 建立无 key、无网络、无用户目录写入的默认离线门禁。为事实学习注入确定性 Provider，缓存与文件路径用临时 fixture。把需 loopback、宿主 sandbox 或打包环境的测试独立分组。加入 Desktop typecheck/test/build，再加入少量 Python sidecar + TS 客户端的协议契约，以及会话切换和审批的 UI 行为覆盖。保持 CI 和本地文档使用同一命令及配置。

**验收。** 无 Ollama、无 DeepSeek、空应用数据目录仍可跑完默认门禁。任意一侧故意引入错误 wire 字段或状态、双端门禁应失败。真实 Provider、宿主和安装包验收各自归档，故障原因区分运行时、Provider 和环境。

### A13 · Provider 设置归属与可复现性

**问题和影响。** Service 设置修改全局 router 私有字段、模块级模型名并清空实例缓存。Planner、Loop 和记忆选择都取全局 router。不同 Service 对象处于同进程时共享这些设置。模型切换与在途 Run、恢复后所用模型、fallback cooldown 的范围没有应用级与 Run 级的明确合同。部分配置在导入时读取环境变量。[设置读写](../../agent/service/service.py#L954) · [环境配置](../../agent/llm.py#L138) · [Planner](../../agent/orchestrator/planner.py#L258) · [记忆 Provider](../../agent/memory/learning_provider.py#L285)

**修复范围。** 在 composition root 加载、校验 Provider 设置和凭据，作用域明确为应用或经确认的用户设置。每个 Run 接纳时固定有效配置，记录非敏感的 provider/model 和实际 fallback；恢复按原配置或明确的替换政策。让 Planner、Loop、记忆走已选 Provider；把 cooldown 放在它实际保护的连接范围。

**验收。** 用两个假的 Provider 和两个 Service 验证隔离、在途切换、恢复、并发 fallback、配置非法与实际路由的可观测性。保留强制 Ollama 和既有错误分类语义。

### A14 · 收紧 Runtime 状态与动作执行接口

**问题和影响。** `AgentState` 是跨层可变投影，容纳 Goal、执行、Verifier、恢复、对话与终态字段。Loop 为工具调用拼 Task/Plan，调用 `_orch._executor.execute_action` 并按多个结果形态解释成功。原生 executor、builtin/MCP 与专用 Workflow 各有分派分支，授权、取消和证据入口随路径变化。过去的缺口预算与 verifier 补丁继续依赖这些共享字段。重构应减少调用者需要了解的状态字段和执行规则。[状态投影](../../agent/state.py#L27) · [Runtime 状态循环](../../agent/runtime.py#L831) · [Workflow 分派](../../agent/loop/runner.py#L793) · [动作执行](../../agent/loop/runner.py#L852)

**修复范围。** 先选取一条原生工具写入和一条专用 Workflow 作特征测试。收拢动作执行入口的必需输入和结果，沿已有 `Task`、`ExecutionPlan`、`ActionResult`、`EffectAuthorization` 和 evidence 类型迁移。让 Goal 状态、动作结果和公开 Run 输出各有写入方。保留只读并发、写入屏障、取消点和 Workflow 原子恢复。

**验收。** 用 Service 入口覆盖只读并发保序、写入和执行授权、工具错误、未确认效果、缺口消耗预算、目标纠正及终态验证。移除迁移范围内的跨模块私有访问和重复结果判定。

### A15 · Session、workspace 与上下文生命周期

**问题和影响。** `ApplicationContext` 的 registry 用字符串 session ID，`ServiceContextFactory` 另建 tenant/session 字典，同时写入应用的私有 registry。Service 删除只修改 Store，缓存清理由 ContextFactory 关闭处理。Run admission 和 context 创建分别计算 workspace，后者也可能在后台执行。一个 workspace 绑定变更可能跨越接纳和 context 构造，需要测试同一 Run 的记录与执行根是否一致。[应用 registry](../../agent/runtime_context.py#L70) · [Service registry 与 workspace](../../agent/service/context_factory.py#L64) · [删除入口](../../agent/service/service.py#L518) · [预留根目录](../../agent/service/service.py#L230)

**修复范围。** 由一个上下文模块管理 tenant/session key、创建、释放、删除和重开。由 Service 在接纳时解析并记录有效 root；执行、子 Run 继承和恢复消费该事实。明确会话删除、仍活跃 Run 与历史保留的关系。配合 A03 使用授权过的 workspace 范围。

**验收。** 覆盖双租户同名 session、同 session 并行构造、删除后重开、带活跃 Run 的删除、启动与绑定变更竞态、委派继承和恢复。已接纳 Run 的文件操作及工件读取使用同一记录的 root。

### A16 · 跨语言 wire 合同

**问题和影响。** Python DTO、方法枚举、dispatcher 和 TS 方法表、mapper、Mock 要手工同步。已有测试比对两份方法列表，字段形态、枚举、错误和缺省值仍主要靠各侧各测。桌面接入负担源于调用者需要知道多个位置的映射规则。[Python DTO](../../agent/service/contracts.py) · [分发表](../../agent/service/local_sidecar/dispatcher.py#L25) · [TS 方法表](../../apps/desktop/src/service/localTransport.ts#L8) · [TS mapper](../../apps/desktop/src/service/localAgentServiceClient.ts) · [既有审计](desktop-frontend-debt.md)

**修复范围。** 选定已有 Python wire DTO 或显式 schema 作为权威，导出方法、请求、响应、错误和事件 fixture。先覆盖 Start/Get/Resume/Cancel、审批和事件，再处理其它工作台功能。有明确收益时生成 TS transport 类型；保留 View 层领域映射。Mock 实现同一合同或通过同一组 fixture，减少手写 unavailable 桩和调用点强转。

**验收。** 错误字段、枚举、版本或字段缺省能在提交时检出。未知字段和前后兼容行为有明确用例。新 RPC 的同步步骤和单个流程触及的文件数可复核。保持 A09 的 transport 语义和 A11 的 view contract 清晰独立。

## 第四批 · 运行环境与长期维护

### A17 · SQLite 竞争、历史增长与诊断

**问题和影响。** Store 使用同步 SQLite 和连接锁，Service 异步入口直接调用多个 Store 方法，`busy_timeout` 默认 5 秒。争抢写锁时可能阻塞事件循环中的控制请求或 lease 更新，需要定量测量。已有 retention floor 可以约束事件读取窗口，其写入路径只更新游标元数据。还需要为事件、模型增量、Run 输出和工件定义物理保留、归档与恢复引用。单文件 Store 同时管理运行、调度、审批、会话和工作区，变更应按实际事务分区。[SQLite 配置](../../agent/runtime_store/sqlite.py#L55) · [写事务](../../agent/runtime_store/sqlite.py#L900) · [Service 直接读](../../agent/service/service.py#L349) · [Retention floor](../../agent/runtime_store/sqlite.py#L6846)

**修复范围。** 先用受控锁竞争和历史样本测 get/cancel 延迟、lease 更新与数据库增长。根据测量，把确有阻塞的操作交给有界数据库执行线程或等价的单一执行模块，保护事务顺序。定义保留期与活跃 checkpoint/审批的引用约束。用结构化字段关联 request、run、lease、revision、error code，区分用户事件和诊断日志。

**验收。** 两连接持锁时控制请求和 lease 有明确时限，`SQLITE_BUSY` 可归因，恢复不会双写。高事件量下分页、清理、重开和 resume 仍一致，保留窗口外的客户端得到可处理的 gap。记录基准和测试数据规模。

### A18 · Desktop 分发和子进程生命周期

**问题和影响。** Tauri/Rust host 已存在。当前 bridge 默认调用系统 `python3`，依赖构建时配置仓库根或 venv。Tauri bundle 配置列出图标和目标，没有随包声明 Python sidecar 运行产物。源码环境可跑并不能证明干净机器可安装运行。Rust、TS transport 与 Python 关闭路径分担了进程清理。[启动 bridge](../../apps/desktop/src/service/tauriBridge.ts#L9) · [Tauri host](../../apps/desktop/src-tauri/src/main.rs#L21) · [打包配置](../../apps/desktop/src-tauri/tauri.conf.json)

**修复范围。** 明确支持的发行形态。若交付安装包，选择随包运行环境或可检查的前置依赖，并统一可执行路径、数据目录、升级和诊断。由 host 管理 ready、退出、超时、重启和 app 关闭。若产品继续为源码开发形态，完善前置检查和文档即可。

**验收。** 离线 fake process 覆盖崩溃、EOF、重复连接、关闭后未决请求、配置缺失。发行方用干净机器确认安装、启动、健康检查、运行中退出、重新打开历史及卸载行为，记录 OS、包版本和数据目录。

### A19 · MCP 发现缓存和恢复

**问题和影响。** 发现结果缓存于进程及磁盘，失败结果也被写成空工具集。查找以 key 是否存在决定要不要重试，暂时失败可能使工具一直消失。HTTP sidecar 启动失败也可进入进程级 `_spawn_failed`，在后续探测前直接拒绝。磁盘缓存是全局路径且写失败静默。这里需要区分未安装、短暂不可用、无导出工具和协议失败。[发现与缓存](../../agent/mcp/tools.py#L59) · [启动状态](../../agent/mcp/client.py#L107)

**修复范围。** 区分 schema 缓存和可用性状态，对暂时失败采用受控重试并暴露原因，明确配置/版本变化时何时失效。将缓存放到应用数据目录并让测试注入临时路径。复用已定义的 tool allowlist，保持错误分类和探测预算。

**验收。** 假 MCP 第一次发现失败、第二次可用时，能力在规定窗口内恢复。覆盖无工具、schema 更新、无可执行文件、缓存损坏、并发首次发现及重启。默认离线测试不会改用户缓存。

### A20 · 文档、样式与交接权威

**问题和影响。** Known Debt 仍列 Direct API 绕过 Service、仓库没有 Tauri/Rust host，两处已被当前代码推翻。前端样式与设计文档多轮叠加，具体诊断已在 Desktop 技术债审计记录。当前 `AgentService` 约 2,361 行、SQLite Store 约 6,931 行，新增能力若继续横跨两处加事务与状态分支，会提高接手者定位成本。[旧债务说明](known-debt.md) · [Python API](../../agent/api.py#L39) · [Rust host](../../apps/desktop/src-tauri/src/main.rs) · [前端债务](desktop-frontend-debt.md)

**修复范围。** 更新当前入口和状态所有权地图，明确 ADR 的时间层次和当前合同。先用本档案中的业务修复收拢责任，之后沿真实事务或 UI 功能划分代码。前端以现行设计为权威，按工作台清除被覆盖的规则并限制作用域。

**验收。** 新维护者能从地图定位接纳、审批、调度、恢复、事件、配置和发行入口。入口描述与 smoke 或结构检查对应。样式变更以主要工作台、现有状态及支持窗口尺寸的截图验收。

## 排期与协作约束

1. **先锁执行事实。** A01、A02 和 A03 的信任边界裁定先行。同步从 A12 抽出独立的 offline fixture 与 Desktop CI，供其余工单使用。A01 与 A04 都会改状态控制，分 PR 且共享等待、取消和退出测试。
2. **先确定事务，再合并编排。** A08 先提供可验证的 schema 迁移路径。A06 决定统一委派接口后实施 A07。A05 可并行设计触发状态，但凡涉及 SQLite schema 的 PR 应按迁移版本串行合并。A03、A06、A15 一起确认 workspace 权威。
3. **并行推进传输与 Desktop 状态。** A09 先定订阅、错误和关闭合同，A11 据此移除重复刷新，A16 同步建立可复用的 wire fixture。A18 等 host 语义稳定后验发行形态。
4. **收紧后续维护成本。** A10 单独明确记忆治理；A13 与 A19 分别收归 Provider 和 MCP 状态。A14 依赖 A01/A02 明确的执行不变量，A17 依据测量确定实现，A20 随各批改动更新现状。

每个 PR 引用工单号，包含一条能在旧代码失败的入口级用例或受控测量、变更后的结果、状态或 schema 迁移影响。优先迁移真实调用方并删除旧路径。跨进程的终态、取消、效果语义和分布式执行变更依仓库要求先落 ADR 与 Dataset。现有的预留幂等、事件序列、取消与超时区分、fencing、已提交效果不可随意重放、Artifact 证据均作为回归不变量。

## 本次验证记录

- **6 组本地探针复现问题。** 使用临时 SQLite、测试替身和内存流。A01 复现两实例审批不唤醒，A02 复现公开投影提前报告成功，A05 复现重启未启动调度及 claim 后没有 Run，A07 复现依赖节点提前接纳及重试多一条 Handoff，A08 复现 schema 拒绝前已变更，A09 复现挂起订阅阻塞 health 分发。探针使用既有测试 Launcher 与临时 fixture，生产代码未改。
- **架构静态门禁通过。** `.venv/bin/python evaluation/architecture_verification.py` 返回 PASS。当前脚本的检查范围与上述生命周期问题不同。
- **Desktop npm test 通过。** TypeScript app/test typecheck 和 40 个 client/factory/controller 用例通过。未运行 WebView、安装包和真实用户操作。
- **Python 回归未全绿。** `870 passed, 17 skipped, 4 failed, 7 errors`。MCP 7 个 setup 错误源于测试清理受保护的用户缓存。失败包括 HTTP loopback 端口绑定被拒、两个 Seatbelt/宿主能力用例失败、一个事实记忆断言依赖不可达的 Provider。排除了 `test_tools_execution.py`，以避免运行该文件里的联网用例；DeepSeek 凭据被屏蔽，测试强制 Ollama。未调用 DeepSeek 或运行真实模型验收。

复跑本次 Python 回归和 Desktop 门禁：

```sh
OPENAI_API_KEY= LLM_API_KEY= P2_SECONDARY_API_KEY= TSAGENT_LLM_PROVIDER=ollama \
  HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python -m pytest -q \
  --ignore=tests/test_tools_execution.py
(cd apps/desktop && npm test)
```

真实环境验收由对应负责人单独完成。A01/A04 在可控的双进程环境暂停或终止 worker 并验证 lease、取消和外部效果；涉及外部写入只用测试服务、幂等键及合成数据，验收同一效果只提交一次。A10 用两个测试身份和真实 ReMe 实例验证搜索范围与删除传播。A09/A18 用目标 OS 的打包客户端验证长订阅期间控制请求、断开后的重启和历史恢复。Provider 能力、网络服务可用性和安装包体验的结果各自归档。
