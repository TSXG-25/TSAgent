# TSAgent 架构审计与修复交接档案

当前复核基线为 2026-11-10 的 `codex/github-upload`，提交 `0652d65658e3c8e804a7dddb5bf7836c4a6b33d4`。上一基线 `6beb0845c1cd20d201d274fbf8a9808d6fe57af1` 的实测单独归档。两个基线间唯一的生产改动是在 `create_service()` 的可选 bootstrap 分支加入 Web 注册，Desktop 实际入口仍跳过该分支。工单包含责任模块、影响、最小修复和合并验收。

## 架构判断

CLI、Python API 与 Desktop 已汇入 `AgentService`。普通执行经 Runtime、Intent/Goal、原生工具调用与效果校验。专用 Workflow 已接入持久 checkpoint 和恢复协调器。SQLite 已有 Run 预留、事件序列、终态事务、worker lease 和 fencing。应保留这条主链以及现有的崩溃、幂等和效果证据测试。[CLI](../../main.py) · [Python API](../../agent/api.py#L39) · [Service 工厂](../../agent/service/factory.py#L14) · [恢复入口](../../agent/service/runtime_launcher.py#L63)

目前最需要修的是**持久事实与控制路径分离后，没有为两者之间的失败定义统一语义**。审批结论写进 SQLite，执行者却等待进程内信号。定时任务先推进时间，再创建 Run。画布先分步写 Task/Handoff，再启动子 Run。服务有持久终态，但投影仍可由 Workflow 索引推断 Run 成功。这些缺陷分别影响执行授权、任务是否遗漏、重试一致性和用户看到的完成状态。

第二类问题来自**同一个决定由多个调用方拼装**。委派配置、workspace、会话、事件映射和 Provider 设置跨 Python、SQLite、Rust/TS 及应用入口传播，容易出现同名功能走不同语义。重构应按用例收回决策权，逐项迁移调用方，用可执行契约验收。文件行数只用于理解改动集中度。

当前复核把这两类问题落到了用户操作上。Mars 的编辑保存会把脱敏投影写回原文件，Earth 的表单选项全被真实后端拒绝，Pluto 在记忆提交失败后仍报告已解决，Artifact 预览可以对另一版本的正文标注为未过期。CLI 与 sidecar 共用 Service，冷启动后的工具目录依然不同。这些用例适合直接加入 A12 的跨端门禁。

### 派工总览

| ID | 优先级 | 工单 | 证据 | 责任模块 |
| --- | --- | --- | --- | --- |
| A01 | P0 | 审批绑定具体动作并以持久决定唤醒执行 | 离线复现 | Service / Runtime |
| A02 | P0 | 公开 Run 状态、输出和工件以已提交事实为准 | 状态离线复现、工件静态 | Runtime / Projector |
| A03 | P3 单机 / P0 共享或远程 | 确认本地 RPC 的信任边界 | 静态、需产品裁定 | Service / Sidecar / Desktop |
| A04 | P1 | 持久化已接纳 Run 的启动失败并收束暂停退出 | 暂停退出离线复现、启动静态 | Service / Runtime |
| A05 | P1 | 定时任务自启并消费目标路由 | 离线复现、静态 | Scheduler / Store |
| A06 | P1 | 统一委派创建、运行与重试 | 离线复现、静态 | Service / Desktop |
| A07 | P1 | 统一画布节点合同和边语义 | 离线复现、需产品裁定节点与边 | Orchestration / Desktop |
| A08 | P1 | SQLite 在验证身份后执行原子迁移 | 离线复现 | Store |
| A09 | P1 | JSONL 长订阅与串行控制请求分开处理 | 离线复现、静态 | Sidecar / Desktop |
| A10 | P3 裁定 | 明确 Durable Facts 和 ReMe 的忘记范围 | 静态、外部契约待验 | Memory / MCP |
| A11 | P1 | Desktop 会话与 Run 事件有单一所有者 | 静态 | Desktop |
| A12 | P1 | 建立真正离线且覆盖双端的合并门禁 | 测试实测、静态 | QA / CI |
| A13 | P2 | 收归 Provider 设置并对齐 Desktop 表单 | 静态 | LLM / Composition |
| A14 | 待证据 | Runtime 状态和工具执行接口 | 静态 | Runtime / Executor |
| A15 | P1 | 启动和恢复共用接纳时记录的 workspace | 单进程离线复现 | Context / Store |
| A16 | P2 | 用跨端 fixture 约束 wire 合同 | 静态 | Service / Desktop |
| A17 | 待测量 | SQLite 锁竞争与历史增长 | 静态、容量待测 | Store / Operations |
| A18 | P2 | Desktop 可分发性与受管进程生命周期 | 静态、安装包待验 | Desktop / Release |
| A19 | P2 | MCP 发现状态与失败恢复有明确生命周期 | 静态 | MCP |
| A20 | P3 | 修订架构文档和前端样式权威 | 静态 | 架构负责人 / Desktop |
| A21 | P1 | 在 Desktop 真实入口注册 Web 工具目录 | 真实入口冷启动离线复现 | Bootstrap / Tool Registry |
| A22 | P1 | 保护 Forge 文档草稿、版本与原文件 | 当前 Service 离线复现、UI 静态 | Workspace / Desktop |
| A23 | P1 | 统一实验裁决的端到端合同 | 当前 Service 和 RPC 离线复现 | Evaluations / Desktop |
| A24 | P1 | 把母题汇报、裁决与记忆收录串成可核对的状态 | 当前 Service 离线复现、UI 静态 | Motif / Memory / Desktop |
| A25 | P2 | 让工作台声明和按钮反映真实能力 | 当前静态、产品需裁定范围 | Desktop / 产品 |
| A26 | P1 | 修复 Navigator 的停止、结果归属与错误呈现 | 当前静态 | Search / Desktop |
| A27 | P1 | 让工件预览正文与版本校验消费同一次读取 | 当前故障注入复现 | Artifacts / Desktop |

P0 指执行授权或公开执行事实可能错误。P1 指已发生或可复现的功能损坏、中断与数据不一致。P2 为后续结构改进，P3 为文档或产品裁定。A03 的优先级取决于实际发布边界。待证据和待测量项先做定位或测量，再决定是否立项。

## 第一批 · 执行安全与公开事实

### A01 · 审批绑定具体动作并以持久决定唤醒执行

**问题和影响。** 两份缺少工具调用 ID 的合成 Provider 响应经现有解析器均得到 `call-0`。在同一 Run 中批准第一个假动作后，参数不同的第二个假动作通过了真实审批门，SQLite 仍只有第一条审批。根因是审批 ID 按 Run 和 Provider call ID 拼接，Store 重用同键记录时未核对动作和参数。仅把放行条件改为 `APPROVED` 仍会接受这条旧批准。合成用例证明了缺 ID 时的行为。本机 Ollama 是否返回 ID 待独立验证。[OpenAI 解析](../../agent/llm.py#L118) · [Ollama 解析](../../agent/llm.py#L346) · [审批门](../../agent/loop/runner.py#L595) · [Store 重用](../../agent/runtime_store/sqlite.py#L1519)

**等待故障。** 已复现两实例共库时，B 的决定不会唤醒 A。同一实例里两租户复用审批 ID 时，也会唤醒错误 waiter 并返回 `PENDING`。Loop 目前只拦 `DENIED`，缺少审批能力也直接放行。ADR 0020 明确支持同机多进程共享 SQLite。[等待与唤醒](../../agent/service/service.py#L1901) · [多进程合同](../adr/0020-durable-sqlite-runtime-store-contract.md#L27)

**修复范围。** 仅对同一动作的明确 `APPROVED` 放行，审批能力缺失时失败。用 Run 内独立的调用身份区分新动作，Provider call ID 继续用于协议关联。同键重放时核对持久动作与结构化参数，不一致时报现有幂等冲突。waiter 键与 Store 对齐为 `(tenant_id, run_id, approval_id)`，等待者观察 SQLite 已提交决定。复用现有 Store 决定冲突规则。

**验收。** 假 Provider 同一 Run 分两轮发出无 ID、参数不同的假动作。批准第一轮后，第二轮必须单独进入待审批。两租户同名审批互不唤醒，同库另一 Service 提交决定可唤醒正确 waiter。拒绝或缺少审批能力时假动作不执行。

### A02 · 公开 Run 终态与输出原子可见

**问题和影响。** `RunProjector` 在 Run head 仍非终态时可从 Workflow index 投影 `COMPLETED`，而 Launcher 随后才用 Run 事务提交终态、输出和工件。恢复期间两个事务之间可能读到先于正式发布的成功。用真实 SQLite 读快照替换其中的 Workflow index，投影实测得到 `head=RUNNING, public=COMPLETED, output=None`。该探针验证投影规则，完整恢复中的竞态窗口仍需入口测试。保留既有终态 head 优先逻辑和 Launcher 的原子提交。[投影分支](../../agent/service/run_projector.py#L226) · [恢复发布](../../agent/service/runtime_launcher.py#L306) · [新 Run 发布](../../agent/service/runtime_launcher.py#L489)

**修复范围。** 让公开 Run 状态只取 head，移除从 active Workflow 和已完成索引推导 Run 状态的两条分支。同时移除公开工件的 `durable_artifacts or indexed_artifacts` 回退。已提交的空工件列表也是有效结果。Workflow 索引继续用于阶段与恢复进展，复用 Launcher 已有的终态事务。只有发现受支持的历史数据确实缺正式事实才考虑迁移。[工件回退](../../agent/service/run_projector.py#L259)

**验收。** 通过 `get_run` 读取含已完成 Workflow 和索引工件、尚未提交 Run 终态的快照，仍显示真实 head 状态和已提交工件列表。正式提交后一次读取取得一致的状态、输出和工件。

### A03 · 确认本地 RPC 信任边界

**问题和影响。** HTTP 明确无认证，只做 Host/Origin 检查。Service 通过 DTO 中的 tenant、user、session 定位数据，`start_run` 接收并解析显式 `workspace_root`，`get_run` 的 Projector 会比对请求中的 user ID 和持久所有者记录，身份值仍由调用方提交。Native 文件选择、附件和 Run root 由不同路径处理。当前机制支持可信单机客户端的数据分区。共享客户端或远程服务所需的调用方身份及资源权限，在此实现中缺少同一权威。[HTTP 边界](../../agent/service/local_sidecar/http_server.py#L43) · [Run root](../../agent/service/service.py#L230) · [Run 查询](../../agent/service/service.py#L349) · [所有者匹配](../../agent/service/run_projector.py#L204) · [附件](../../agent/service/service.py#L566) · [Run context](../../agent/service/context_factory.py#L95)

**修复范围。** 确认发行版使用 stdio，写明可信单机边界与开发 HTTP 的启用方式。若产品承诺共享设备或远程使用，再立项设计调用方身份和 workspace 授权。

**验收。** 核对发行配置与实际启动参数。开发 HTTP 和打包 Desktop 的启用条件可分别验证。

### A04 · 接纳后启动失败与暂停退出

**问题和影响。** start reservation 先落库，deferred context 构造或 Launcher 出错时，`_run_start` 仅写进程内 `_task_errors`，公开查询仍读 durable snapshot。暂停依赖当前实例的 `asyncio.Event`，进程消失后同一恢复入口转向 Workflow checkpoint，普通 Loop 没有这种 checkpoint。Service close 等待当前 Run，未在本处统一收束 scheduler 和辅助任务。现有 orphan sweeper 可收敛部分异常，延迟错误与本地暂停仍需要明确语义。[Start](../../agent/service/service.py#L280) · [后台错误](../../agent/service/service.py#L310) · [暂停](../../agent/service/service.py#L2054) · [恢复](../../agent/service/service.py#L2102) · [关闭](../../agent/service/service.py#L2346) · [无 checkpoint](../../agent/service/runtime_launcher.py#L88)

**修复范围。** 已接纳 Run 的 Context 或启动失败由当前持有执行权的入口落持久结果。关闭时结束 scheduler 和本实例的暂停等待，沿用现有退出与恢复约定。用临时库和假执行器已复现 `close()` 等待本地暂停 gate。此修复保留进程内暂停语义。

**验收。** 注入 deferred Context 失败，重开后能读到失败原因。暂停中的 Run 允许正常退出，退出不产生隐式用户取消。

## 第二批 · 持久编排、数据和传输

### A05 · 调度自启和目标路由

**问题和影响。** Scheduler 只由 create/list/update schedule 启动。临时库实测，重建 Service 后等待一个 tick，已到期计划仍未触发，scheduler 未启动。Store claim 会先推进 `next_fire_at` 并增加 `fire_count`。在创建 Run 前停下，重读得到 fire_count=1 且没有 Run ID。运行冲突及其他异常当前仅输出 traceback。`target_planet` 已存储，当前触发构造的 StartRunRequest 未携带该配置。[启动调用点](../../agent/service/service.py#L1757) · [执行循环](../../agent/service/service.py#L1819) · [认领事务](../../agent/runtime_store/sqlite.py#L1964)

**修复范围。** 在启用调度的 host 启动和关闭 scheduler。把已保存的 `target_planet` 接到实际消费路由的入口，复用现有 `schedule_id + fire_count` 触发请求 ID。当前 claim 批量推进时间，异常可丢多次触发。若产品接受尽力触发，明确该行为并记录失败。若要求认领后必有 Run，优先让 claim 和 Run 预留共用事务。

**验收。** 无需打开计划列表，重启后一个已到期的计划按保存的目标启动。若承诺认领不丢失，用认领后的中断用例验证。

### A06 · 统一委派创建、执行与重试

**问题和影响。** Runtime 的 `spawn_subtask` 创建 Task/Handoff、传递 parent workspace 和 profile、启动子 Run 并回写目标 Run。Desktop 的 `delegate_run` 只写 Task/Handoff，后续由客户端组装 `start_run`。图执行再拼一次且只传 `delegation_depth`，容易丢掉 profile 的 permission/model/skills 和上下文。A07 的同请求重试已实测留下额外 Handoff，证明分步创建缺少统一幂等归属。[Runtime 委派](../../agent/service/service.py#L765) · [UI 委派](../../agent/service/service.py#L1688) · [桌面启动](../../apps/desktop/src/app/delegate.ts#L41) · [图节点启动](../../agent/service/service.py#L1359) · [Profile 消费](../../agent/loop/runner.py#L282)

**修复范围。** 把 Runtime、Desktop 和画布的真实调用迁到一个 Service 委派用例，统一传递 profile、workspace 和上下文引用。接纳失败或同请求重试后应能找到同一委派事实，复用现有请求身份。

**验收。** 用带 profile 和 workspace 的同一假任务比较三条入口，确认子 Run 得到等价配置。重复同请求不得新增 Handoff。

### A07 · 画布节点合同和边语义

**问题和影响。** Uranus 当前保存工具级节点，如 `read_file`、`llm`、`approval`，不带 `request_text`，边为二元数组。后端只执行带 `request_text` 的 `task` 节点，边按 `{from, to}` 读取。真实 sidecar dispatcher 接收两节点的 UI payload，实测保存成功、执行返回 `INTERNAL_ERROR`。若只改边形状，当前过滤会静默跳过所有工具节点并返回空列表。对能被执行的任务图，旧探针还复现了父节点未完成时子节点已接纳、同请求重试多一条 Handoff。[Uranus 保存](../../apps/desktop/src/workspaces/uranus/UranusWorkspace.tsx#L30) · [边读取](../../agent/service/service.py#L46) · [画布执行](../../agent/service/service.py#L1322)

**修复范围。** 先由产品决定节点代表工具步骤还是委派任务，以及边表示启动顺序还是成功依赖。按选定模型对齐 UI payload 与后端，遇到未知节点类型或无可执行节点时给出明确输入错误。在启动第一个 Run 前完成整图预检。选委派任务时复用 A06；图版本和运行中重启按实际需求另议。

**验收。** 用 UI 生成的两节点图走真实 wire，得到符合选定语义的执行结果。未知节点或非法图在产生 Task/Handoff/Run 前拒绝。重复同请求不新增 Handoff。

### A08 · 统一 SQLite 迁移入口

**问题和影响。** Store 在检查 `schema_version` 和 `store_generation` 前执行 DDL。Workspace 读写还会尝试重复 `ALTER TABLE` 并吞掉全部 `OperationalError`。构造未知版本且缺少一列的临时库，实测 `open` 返回 `SCHEMA_INCOMPATIBLE` 后缺列已被补上。版本拒绝未保护数据库 schema。[初始化与版本检查](../../agent/runtime_store/sqlite.py#L733) · [重复 DDL](../../agent/runtime_store/sqlite.py#L2189) · [调用点](../../agent/runtime_store/sqlite.py#L2295)

**修复范围。** 在执行 DDL 前检查已有库的版本和 generation，删除正常 workspace 读写中的重复 `ALTER TABLE`。保留仓库已经支持的迁移。确需修改事务边界时，将其限制在本次涉及的 schema 和元数据。

**验收。** 不兼容的库打开被拒，schema 与元数据保持原样。现有迁移回归继续通过。

### A09 · Sidecar 的长轮询、控制请求和关闭

**问题和影响。** JSONL server 对一行完成 `await dispatch` 才读下一行。订阅等首个事件最多约 8 秒，后续每次事件间隔窗口为 0.25 秒，累计等待取决于批次。用内存流与假订阅实测，后排 health 未被分发。Cancel、审批决定等控制请求也走该队列。HTTP 另外使用线程、300 秒 future wait，结束时只在该处请求 loop stop，协议和关闭语义与 JSONL 分叉。[JSONL 读取](../../agent/service/local_sidecar/server.py#L31) · [订阅窗口](../../agent/service/local_sidecar/dispatcher.py#L522) · [HTTP 生命周期](../../agent/service/local_sidecar/http_server.py#L71) · [TS correlation](../../apps/desktop/src/service/tauriSidecarTransport.ts#L46)

**修复范围。** 只将 `subscribe_events` 交给后台 task。普通 RPC 仍由主循环顺序读取和处理，主循环负责 EOF 与 shutdown，退出前取消并等待后台订阅，再关闭 Service。响应都走同一个同步 writer，确保完整写出一行。若实现引入跨线程或可挂起写入，再加锁串行化。

**验收。** 内存流模拟挂起订阅，随后发控制 RPC 和 shutdown。控制响应先于订阅返回，shutdown 清理后台 task，输出仍可逐行解码。

### A10 · 两层记忆统一身份与删除语义

**问题和影响。** Durable Facts 走 tenant/user 命名空间、证据与策略。ReMe 搜索只发送 query/limit，后台蒸馏发送原始回合、session ID、request ID 与提示，没有传递相同的 tenant/user 范围或复用确定性的事实写入门禁。Service 的 forget 仅删除 Durable Facts。TSAgent 端无法证明共享 ReMe 实例时的检索隔离、敏感信息处理、删除传播和重启投递；实际行为取决于外部服务和部署方式。[事实删除](../../agent/service/service.py#L1432) · [ReMe 投递](../../agent/runtime.py#L1099) · [ReMe 接口](../../agent/mcp/reme.py#L34) · [工具入口](../../agent/loop/runner.py#L774)

**修复范围。** 明确用户点「忘记」时删除的范围，并在界面和文档使用同一口径。若产品要求连带删除 ReMe 数据，先向 ReMe 验证可用的删除和隔离接口，再决定实现。

**验收。** 用测试替身验证 Durable Facts 删除范围及 UI 文案。只有承诺跨存储删除时才增加对应契约测试。

## 第三批 · 客户端与质量门禁

### A11 · Desktop 会话和事件状态归一

**问题和影响。** App 维护选中 session，Local client 可变 identity 保存 session，Sun 还维护本地 session。Sun 的轮询闭包依赖仅 `service`，却读取 `sessionId` 和 `runs`，一次刷新可从不同来源获得审批和参与者的 session。RunController 已维护长轮询，Sun 另有 3 秒刷新。RunController 首次 hydrate 只读一批 events，之后仅对 live Run 开轮询，重开已完成且事件数超过单批上限的 Run 可能看不到完整时间线。[App 选择](../../apps/desktop/src/App.tsx#L199) · [Sun 刷新](../../apps/desktop/src/workspaces/sun/SunWorkspace.tsx#L105) · [组件轮询](../../apps/desktop/src/workspaces/sun/SunWorkspace.tsx#L181) · [Controller hydrate](../../apps/desktop/src/features/runs/runController.ts#L252) · [事件批次](../../agent/service/local_sidecar/dispatcher.py#L540)

**修复范围。** 把选中 session 和查询 key 收归单一状态所有者。业务请求传显式 session，异步结果按发出时的 key 合并。RunController 管理 Run 快照、cursor 和订阅，UI 订阅投影。给历史事件分页，明确终态页面的读取停止条件与 retention gap。把跨组件 window 事件、请求异常和不安全类型转换按本用例逐步迁移，建立用户可见错误状态。

**验收。** 测试 A→B→A 快速切换、旧响应晚到、审批和参与者范围、两个同时运行的会话、断线重连及超过单批上限的已完成 Run。验证同一 Run 只有一个刷新拥有者，事件去重并单调推进。

**同批处理的工作台用例。** Saturn 列表刷新后应按 ID 派生详情，暂停后按钮改为恢复。当前详情仍持有刷新前的 Schedule 对象。Jupiter 忘记事实后应清除或更新详情，失败时应保留列表并显示错误。当前详情持有旧对象，初次加载事实失败时直接清空。检查 RunController 在另一操作进行时直接返回当前 Run 的分支，发起方应收到本次请求的 Run 或明确的忙碌结果。[Saturn 刷新和详情](../../apps/desktop/src/workspaces/saturn/SaturnWorkspace.tsx#L22) · [Jupiter 查询与选择](../../apps/desktop/src/workspaces/jupiter/JupiterWorkspace.tsx#L17) · [忘记事实](../../apps/desktop/src/workspaces/jupiter/JupiterWorkspace.tsx#L59) · [Controller Start](../../apps/desktop/src/features/runs/runController.ts#L110)

### A12 · 真正离线、双端和跨端的 CI

**问题和影响。** Python CI 含回归、架构和基准门禁，未运行 Desktop 的 typecheck/test/build。上一基线普通回归有 7 个 MCP setup 错误，fixture 试图删除真实用户目录下的缓存。一个记忆断言因调用不可达 Provider 失败，另有 3 个 loopback 或 Seatbelt/宿主权限用例未通过。当前 Desktop npm test 本地通过，套件集中在 client/controller，未覆盖 A22、A23、A24 的真实表单和持久化组合。[CI](../../.github/workflows/ci.yml) · [MCP fixture](../../tests/test_mcp_tools.py#L76) · [记忆测试](../../tests/test_rb_contracts.py#L13) · [Desktop 测试入口](../../apps/desktop/package.json#L21)

**修复范围。** 建立无 key、无网络、无用户目录写入的默认离线门禁。为事实学习注入确定性 Provider，缓存与文件路径用临时 fixture。把需 loopback、宿主 sandbox 或打包环境的测试独立分组。加入 Desktop typecheck/test/build，再加入少量 Python sidecar + TS 客户端的协议契约，以及会话切换和审批的 UI 行为覆盖。保持 CI 和本地文档使用同一命令及配置。

**验收。** 无 Ollama、无 DeepSeek、空应用数据目录仍可跑完默认门禁。任意一侧故意引入错误 wire 字段或状态、双端门禁应失败。真实 Provider、宿主和安装包验收各自归档，故障原因区分运行时、Provider 和环境。

### A13 · Provider 设置归属与可复现性

**问题和影响。** Service 设置修改全局 router 私有字段、模块级模型名并清空实例缓存。Planner、Loop 和记忆选择都取全局 router。不同 Service 对象处于同进程时共享这些设置。模型切换与在途 Run、恢复后所用模型、fallback cooldown 的范围没有应用级与 Run 级的明确合同。部分配置在导入时读取环境变量。[设置读写](../../agent/service/service.py#L954) · [环境配置](../../agent/llm.py#L138) · [Planner](../../agent/orchestrator/planner.py#L258) · [记忆 Provider](../../agent/memory/learning_provider.py#L285)

**修复范围。** 由 router 的公开配置方法更新模式、模型与实例缓存，让当前调用方遵循下一次请求生效的既有约定。Desktop 只提供后端支持并会保存的字段。

**验收。** 用假 Provider 选择非首项模型，保存、重开设置，并确认下一次 Run 使用同一个有效配置。

**Desktop 切片。** Settings 允许选择多个 API Provider、编辑 API Base，保存请求只传 `providerMode`、`deepseekModel` 和 `ollamaModel`。重新打开时模型选择框设为 Provider 清单首项。先让表单只提供后端接受且会保存的字段，再按需要增加 Provider。以假 Provider 验收选择非首项、保存、重开和下一次 Run 采用同一有效配置。[Settings 初始化与提交](../../apps/desktop/src/app/GlobalSettings.tsx#L40) · [设置 Service](../../agent/service/service.py#L954)

### A14 · 收紧 Runtime 状态与动作执行接口

**问题和影响。** `AgentState` 是跨层可变投影，容纳 Goal、执行、Verifier、恢复、对话与终态字段。Loop 为工具调用拼 Task/Plan，调用 `_orch._executor.execute_action` 并按多个结果形态解释成功。原生 executor、builtin/MCP 与专用 Workflow 各有分派分支，授权、取消和证据入口随路径变化。过去的缺口预算与 verifier 补丁继续依赖这些共享字段。重构应减少调用者需要了解的状态字段和执行规则。[状态投影](../../agent/state.py#L27) · [Runtime 状态循环](../../agent/runtime.py#L831) · [Workflow 分派](../../agent/loop/runner.py#L793) · [动作执行](../../agent/loop/runner.py#L852)

**修复范围。** 先保留为待证据项。若同一动作在两条执行路径出现不同的授权、取消或结果判定，沿已存在的 `ActionResult` 和效果证据合同收拢该用例的调用。记录私有接口访问与重复结果解释的位置。

**验收。** 出现具体失败后，用一条入口级用例锁定差异，据此验收局部修复。

### A15 · 复用接纳时记录的 workspace

**问题和影响。** `ApplicationContext` 的 registry 用字符串 session ID，`ServiceContextFactory` 另建 tenant/session 字典，同时写入应用的私有 registry。Service 删除只修改 Store，缓存清理由 ContextFactory 关闭处理。Run admission 和 context 创建分别计算 workspace，后者也可能在后台执行。一个 workspace 绑定变更可能跨越接纳和 context 构造，需要测试同一 Run 的记录与执行根是否一致。[应用 registry](../../agent/runtime_context.py#L70) · [Service registry 与 workspace](../../agent/service/context_factory.py#L64) · [删除入口](../../agent/service/service.py#L518) · [预留根目录](../../agent/service/service.py#L230)

**修复范围。** 让 Start 和 Resume 共用现有 `_recorded_workspace_root` 读取路径，执行和工件读取采用接纳时记录的 root。注意当前 helper 将历史记录缺键与明确记录空 root 都视为假值，新接纳记录的空值应当保持为空。会话缓存清理另按已复现的用例处理。

**验收。** 单个 Service 在接纳后暂停 Context 创建，重新绑定 session。当前实测从记录 A 变成执行 B。修复后已接纳 Run 执行仍使用 A，下一次接纳才使用 B。

### A16 · 跨语言 wire 合同

**问题和影响。** Python DTO、方法枚举、dispatcher 和 TS 方法表、mapper、Mock 要手工同步。已有测试比对两份方法列表，字段形态、枚举、错误和缺省值仍主要靠各侧各测。桌面接入负担源于调用者需要知道多个位置的映射规则。[Python DTO](../../agent/service/contracts.py) · [分发表](../../agent/service/local_sidecar/dispatcher.py#L25) · [TS 方法表](../../apps/desktop/src/service/localTransport.ts#L8) · [TS mapper](../../apps/desktop/src/service/localAgentServiceClient.ts) · [既有审计](desktop-frontend-debt.md)

**修复范围。** 以现有 Python wire DTO 为来源，让 TS 客户端和 Mock 共用少量契约 fixture。先覆盖 Run 和审批，再随工作台修复补充已发生的字段漂移。

**验收。** 故意改动一处已支持字段或枚举值，跨端测试应失败。

## 第四批 · 运行环境与长期维护

### A17 · 测量 SQLite 锁竞争与历史增长

**问题和影响。** Store 使用同步 SQLite 和连接锁，Service 异步入口直接调用多个 Store 方法，`busy_timeout` 默认 5 秒。争抢写锁时可能阻塞事件循环中的控制请求或 lease 更新，需要定量测量。已有 retention floor 可以约束事件读取窗口，其写入路径只更新游标元数据。还需要为事件、模型增量、Run 输出和工件定义物理保留、归档与恢复引用。单文件 Store 同时管理运行、调度、审批、会话和工作区，变更应按实际事务分区。[SQLite 配置](../../agent/runtime_store/sqlite.py#L55) · [写事务](../../agent/runtime_store/sqlite.py#L900) · [Service 直接读](../../agent/service/service.py#L349) · [Retention floor](../../agent/runtime_store/sqlite.py#L6846)

**修复范围。** 先测量。用两连接锁竞争测控制请求与 lease 延迟，用代表性历史样本测数据库增长。根据结果决定是否调整执行线程或留存策略。

**验收。** 提交可复跑的样本规模、锁持续时间和延迟或增长数据。

### A18 · Desktop 分发和子进程生命周期

**问题和影响。** Tauri/Rust host 已存在。当前 bridge 默认调用系统 `python3`，依赖构建时配置仓库根或 venv。Tauri bundle 配置列出图标和目标，没有随包声明 Python sidecar 运行产物。源码环境可跑并不能证明干净机器可安装运行。Rust、TS transport 与 Python 关闭路径分担了进程清理。[启动 bridge](../../apps/desktop/src/service/tauriBridge.ts#L9) · [Tauri host](../../apps/desktop/src-tauri/src/main.rs#L21) · [打包配置](../../apps/desktop/src-tauri/tauri.conf.json)

**修复范围。** 明确支持的发行形态。若交付安装包，选择随包运行环境或可检查的前置依赖，并统一可执行路径、数据目录、升级和诊断。由 host 管理 ready、退出、超时、重启和 app 关闭。若产品继续为源码开发形态，完善前置检查和文档即可。

**验收。** 离线 fake process 覆盖崩溃、EOF、重复连接、关闭后未决请求、配置缺失。发行方用干净机器确认安装、启动、健康检查、运行中退出、重新打开历史及卸载行为，记录 OS、包版本和数据目录。

### A19 · MCP 发现缓存和恢复

**问题和影响。** 发现结果缓存于进程及磁盘，失败结果也被写成空工具集。查找以 key 是否存在决定要不要重试，暂时失败可能使工具一直消失。HTTP sidecar 启动失败也可进入进程级 `_spawn_failed`，在后续探测前直接拒绝。磁盘缓存是全局路径且写失败静默。这里需要区分未安装、短暂不可用、无导出工具和协议失败。[发现与缓存](../../agent/mcp/tools.py#L59) · [启动状态](../../agent/mcp/client.py#L107)

**修复范围。** 先修 `list_tools`，将失败与成功返回空列表区分。只缓存成功结果，短暂失败后允许下一次发现。缓存不存在视为正常，读写异常可诊断。测试使用临时缓存路径。[发现接口](../../agent/mcp/client.py#L229)

**验收。** 假 MCP 第一次发现失败、第二次成功，下一次工具目录恢复。成功的空结果仍可缓存。

### A20 · 文档、样式与交接权威

**问题和影响。** Known Debt 仍列 Direct API 绕过 Service、仓库没有 Tauri/Rust host，两处已被当前代码推翻。前端样式与设计文档多轮叠加，具体诊断已在 Desktop 技术债审计记录。当前 `AgentService` 约 2,361 行、SQLite Store 约 6,931 行，新增能力若继续横跨两处加事务与状态分支，会提高接手者定位成本。[旧债务说明](known-debt.md) · [Python API](../../agent/api.py#L39) · [Rust host](../../apps/desktop/src-tauri/src/main.rs) · [前端债务](desktop-frontend-debt.md)

**修复范围。** 更新当前入口和状态所有权地图，明确 ADR 的时间层次和当前合同。先用本档案中的业务修复收拢责任，之后沿真实事务或 UI 功能划分代码。前端以现行设计为权威，按工作台清除被覆盖的规则并限制作用域。

**验收。** 新维护者能从地图定位接纳、审批、调度、恢复、事件、配置和发行入口。入口描述与 smoke 或结构检查对应。样式变更以主要工作台、现有状态及支持窗口尺寸的截图验收。

## 当前 HEAD 补充工单

### A21 · Desktop 真实入口的 Web 工具目录

**问题和影响。** `0652d6565` 把 `web` 加进 `create_service()` 的可选分支，但 Desktop 使用的 `python -m agent.service.local_sidecar` 在 `__main__` 传 `bootstrap_tools=False`。在干净 Python 进程调用该入口，只替换 stdio serve 以观察启动后的目录，并固定 Web 依赖为可用。首个 Run 前，`web_search` 与 `web_fetch` 均未注册，也未进入目录。Planner 在识别为研究请求时会按需加载 Web，因此冷启动证据不能推断所有 Run 都缺 Web。上一版档案误把直接调用工厂的探针当成真实入口。[Sidecar 入口](../../agent/service/local_sidecar/__main__.py#L30) · [可选 bootstrap](../../agent/service/local_sidecar/lifecycle.py#L86) · [Planner 按需加载](../../agent/orchestrator/planner.py#L592) · [目录](../../agent/loop/toolset.py#L167)

**修复范围。** 先让真实入口在首次需要工具目录前完成 Web 注册，复用 ToolRegistry 与现有可用性检查。若测得 Web 导入影响启动，再分离 schema 注册和耗时实现。其它 builtin 的入口差异按实际支持范围另排，不作为本单前置。

**验收。** 用新进程走 `__main__` 和真实 Run 入口，在依赖可用的替身条件下检查首轮模型收到的目录包含 `web_search` 与 `web_fetch`，并验证缺依赖时的目录和 JSONL health。测试须能区分启动注册与 Planner 研究分支触发的注册。

### A22 · 保护 Forge 文档草稿与原文件

**问题和影响。** Mars 在读请求完成前切换 `sel`，继续显示旧 `content`。晚到的旧请求可覆盖新选择的内容，保存却用当前 `sel`。逐行 `textarea` 还以行号为 key 并使用 `defaultValue`，DOM 草稿和 React 内容可能分离。独立的后端问题已实测。Service 读取正常文件时返回为工具输出设计的脱敏文本，保存时整文件覆盖。临时文件仅修改普通行，未改动的合成凭据值也被覆盖为 `[REDACTED]`。[Mars 读写与编辑](../../apps/desktop/src/workspaces/mars/MarsWorkspace.tsx#L29) · [Service 文件操作](../../agent/service/service.py#L1124) · [脱敏读取和整文件写入](../../agent/services/workspace_service.py#L131)

**修复范围。** 把路径、workspace、读取状态、基线版本和草稿绑定成一个编辑文档。通过文档身份约束异步读写结果。编辑器仅对可安全往返的内容启用覆盖保存，脱敏或不可编辑内容明确为只读。保留现有脱敏和路径规则。保存时比较读取版本，避免覆盖另一编辑或 Run 的新内容。

**验收。** 模拟 A/B 文件乱序返回、打开失败、未保存切换、保存期间继续输入、workspace 切换和磁盘内容变化。通过 Service 使用带合成敏感值的普通文件，编辑其它行后磁盘原值必须保留，或保存被明确拒绝。覆盖普通 UTF-8、受保护路径和已有文件的版本冲突。

### A23 · 统一 Earth 实验裁决合同

**问题和影响。** Earth 提交 `keep_candidate`、`keep_baseline`、`inconclusive`，Store 只接收 `pass`、`fail`、`neutral`。Mock 原样接收三个选项。当前 HEAD 用临时 SQLite 验证三个 UI 选项均被拒绝，`neutral` 成功。真实 dispatcher 对首项返回 `INTERNAL_ERROR`，UI 又吞掉写入异常。页面显示的滑块参数只进入裁决标签与备注，没有传给 Composer 的 Run。[Earth 表单](../../apps/desktop/src/workspaces/earth/EarthWorkspace.tsx#L28) · [Mock 裁决](../../apps/desktop/src/service/mockAgentService.ts#L494) · [Store 枚举](../../agent/runtime_store/sqlite.py#L2475) · [定时任务裁决](../../agent/service/service.py#L1872)

**修复范围。** 先确认 Earth 的人工裁决与 Saturn 的执行结果是否表达同一语义。若共用枚举，由一个权威合同约束 UI、Mock 和 Store。若业务语义不同，显式表达类别，并沿 A08 处理现有记录。将校验错误映射为稳定的用户错误。明确滑块是运行输入还是记录元数据，按确认的用途连接对应入口。

**验收。** 通过表单和真实 sidecar 提交每个公开裁决，重开后读到同一值和所属会话。非法值返回可诊断的输入错误，UI 保留草稿。已有 schedule 记录可继续读取。若参数承诺参与执行，验证 fake launcher 收到提交时的参数快照。

### A24 · 母题汇报、裁决与记忆收录

**问题和影响。** Pluto 的「记录汇报」和「认可」均直接调用 `resolveMotif`，「不认可」只清本地选择。Service 先更新母题，后调用记忆持久化且忽略返回证据。临时库使用返回 `committed=False` 的测试替身，Service 仍把已有母题标为 resolved 并接受不存在的 Run ID。传入不存在的母题也进入记忆提交调用。另有可达性缺口，桌面创建母题后没有调用 `assignMotif` 的动作。[Pluto 裁决](../../apps/desktop/src/workspaces/pluto/PlutoWorkspace.tsx#L32) · [委派与解决](../../agent/service/service.py#L1206) · [Store 更新](../../agent/runtime_store/sqlite.py#L2603) · [记忆提交证据](../../agent/memory/persistence.py#L81)

**修复范围。** 明确母题从创建、委派、汇报、人工裁决到记忆收录的允许转换。先验证母题和关联 Run 再更新，委派复用 A06。明确收录失败时的可见状态及恢复责任，消费现有 commit evidence。若产品允许手工汇报，显式区分它与 Run 证据。

**验收。** 覆盖不存在的母题、不匹配 Run、重复委派、拒绝或退回、同一裁决重放，以及记忆失败后恢复。重复操作最多创建一个子 Run 和一条记忆事实。UI 按持久状态展示汇报、裁决和收录结果，刷新后仍一致。

### A25 · 工作台能力与 UI 承诺

**问题和影响。** 十个场景都标为 `live`，但各场景的介绍覆盖的能力粒度大于实际接口。Venus 的风格和预设仅存页面状态，Composer 不消费它们。Neptune 只渲染 SVG，二进制显示占位，MP4 按钮却可直接发一段委派提示。Uranus 保存线性节点和边，点击运行时丢弃返回的节点结果。Jupiter 权限栏为固定文案。功能是否可用由场景文案、固定徽标和实际 RPC 分别决定。[场景声明](../../apps/desktop/src/app/scenes.ts#L23) · [Venus 风格和 Composer](../../apps/desktop/src/workspaces/venus/VenusWorkspace.tsx#L55) · [Neptune 预览与操作](../../apps/desktop/src/workspaces/neptune/NeptuneWorkspace.tsx#L48) · [Uranus 运行](../../apps/desktop/src/workspaces/uranus/UranusWorkspace.tsx#L74) · [Jupiter 权限](../../apps/desktop/src/workspaces/jupiter/JupiterWorkspace.tsx#L91)

**修复范围。** 逐场景列出已接通的操作及支持格式，先修正现有文案、按钮和错误反馈。已能由 Service 报告的能力复用该事实。只有产品确定要交付的风格应用、二进制预览或节点追踪才另开实现工单。

**验收。** 在 Local 与 Mock 的工作台用例中逐项点击主操作。按钮可用时验证对应请求、结果和错误。未安装能力显示明确状态，已完成操作显示真实结果。键盘可以进入工作台、选中条目和触发主操作。

### A26 · Mercury 搜索的停止与结果归属

**问题和影响。** 「Stop」只清 `running`，在途 Promise 仍会写入列表并执行 `finally`。启动下一次搜索后，上一请求可覆写结果或清除新请求的运行状态。新结果少于当前 pane 数时，`srcs[i] ?? v.source` 会把旧查询来源留在当前结果。手动搜索直接在 dispatcher 调用 Provider 链，Agent Loop 的 `web_search` 另受 DuckDuckGo 安装状态过滤，两个入口的可用性和结果形态应分别核对。[搜索与 Stop](../../apps/desktop/src/workspaces/mercury/MercuryWorkspace.tsx#L25) · [手动搜索 RPC](../../agent/service/local_sidecar/dispatcher.py#L323) · [Agent 工具可用性](../../tools/web.py#L121)

**修复范围。** 为当前搜索明确请求身份，Stop 至少使本次响应失效。若已支持传输取消，将取消向 Provider 传递。每次按本次结果重建 pane 来源。查询边界返回可区分的取消、无结果与不可用状态，能力判定与 A21、A25 对齐。

**验收。** Fake Provider 以任意顺序返回两次请求。取消后旧请求不得更新视图或清除新请求状态。四条变成一条时其它 pane 清空。空结果、不可用、切换会话和退出工作台各有明确表现。

### A27 · 工件预览正文与版本校验

**问题和影响。** `read_artifact` 先读取脱敏正文，再独立读取原始字节计算 digest。沿现有 Artifact 测试 fixture，在两次读取间切回原版本，实际返回另一版本正文且 `stale=False`。Venus 和 Neptune 因此可能把错误版本显示为已记录版本。现有协议提供的是带版本检查的工作区预览，历史文件内容能否重开取决于文件仍在并且版本仍匹配。[Service 读取与校验](../../agent/service/service.py#L2324) · [现有 Artifact 用例](../../tests/test_desktop_sidecar.py#L730) · [Venus](../../apps/desktop/src/workspaces/venus/VenusWorkspace.tsx#L23) · [Neptune](../../apps/desktop/src/workspaces/neptune/NeptuneWorkspace.tsx#L20)

**修复范围。** 对同一受保护读取结果计算原始字节 digest，再解码、脱敏并投影给 UI。为缺失、已改动和不支持格式定义独立结果。只有产品明确要求历史版本可重开时，再评估已验证工件的版本存储。

**验收。** 用故障注入让文件在预览时发生替换，返回正文与 `stale` 必须来自同一读取。覆盖相同字节、不同字节、缺失文件、二进制、脱敏内容以及 session 重新绑定，保留历史 Run 已记录的 workspace 归属。

## 功能覆盖与入口验收

以下场景用于分配负责人和补回归。检查等级以各工单标注为准。

| 功能 | 已核查的入口和当前范围 | 派工与最小入口验收 |
| --- | --- | --- |
| 全局壳、星图和设置 | App 路由、星图入口、TaskTray、GlobalChat、Settings、Tauri host | A11/A12/A13/A18/A20。切换场景和会话，键盘导航，设置重开，后端断开后显示真实状态 |
| Sun / Omni | 会话、workspace、附件、Run/Task、思考链、审批、委派 | A01/A03/A04/A06/A09/A11/A15。选目录、建会话、传附件、发送、审批、取消、重开时间线 |
| Mercury / Navigator | 手动搜索 RPC、来源 pane、通用 Composer。接管与截图控件显示未安装 | A21/A25/A26。搜索、停止、重新搜索、结果为空、依赖缺失 |
| Venus / Muse | 文本/SVG 文件列表和文本预览，风格 UI 与通用 Composer | A25/A27。选工件、查看过期或错误状态。已声明的风格操作必须传入实际生成请求 |
| Earth / Organic Lab | 参数表单、手动评估记录、通用 Composer | A23。每个裁决值写入并重开，失败保留输入 |
| Mars / Forge | 工作区文件列表、文本读写、通用 Composer，工程模式选择 | A15/A22/A25。异步切文件、编辑、冲突保存、脱敏文件无损 |
| Jupiter / Archive | Durable Facts、删除、跨会话工件索引、当前会话历史 | A03/A10/A11/A25/A27。忘记后重开、切换历史、故障显示、授权范围可核对 |
| Saturn / Chronos | 周期间隔、计划列表、暂停/恢复、后台触发与自动评估 | A05/A11/A23。创建后重启、暂停后恢复、注入触发失败与重试、重开状态一致 |
| Uranus / Laboratory | 线性画布、保存与启动节点，读取 Profiles/Skills | A06/A07/A21/A25。保存图、预检、用合法宿主运行、读取结果、失败后重试 |
| Neptune / Media Studio | SVG 预览、其它格式占位、生成类委派 | A06/A25/A27。预览与缺失状态、委派指向所选工件所属范围 |
| Pluto / Genesis | 母题创建、列出、提交解决，后台委派和记忆写入 | A06/A10/A24。发放、委派、汇报、裁决、记忆失败与重开 |
| Python CLI/API 与执行 | Bootstrap、AgentService、Intent/Goal、工具、Workflow、checkpoint/resume、Verifier | A01/A02/A04/A14/A21。各入口能力目录一致，Run 重开能解释状态、输出与效果 |
| 持久化、集成与发布 | SQLite、Provider、ReMe/MCP、Wire、Sidecar、CI 与安装包 | A03/A08/A09/A10/A12/A13/A16–A19。迁移、断流恢复、无网络门禁、部署隔离与安装运行 |

## 排期与协作约束

1. **先做已验证的局部修复。** A01 审批绑定与放行、A02 投影、A08 初始化和 A12 最小离线门禁并行。A22 文件覆盖与 A23 裁决失败独立派工。A01 的跨实例等待可单独交付。
2. **修正常用入口。** A04 和 A15 可沿现有生命周期和已记录 root 直接修。A05 先修自启与路由。A07 先裁定节点模型，选择任务节点时复用 A06。A03 和 A10 按产品范围裁定。
3. **收紧传输和客户端契约。** A09 先拆订阅，A11 去掉重复刷新，A16 补跨端 fixture。A21 从真实入口验 Web 工具。A18 在目标发行形态下验收，A24 复用已确定的委派与记忆约定。
4. **后续以证据立项。** A13 和 A19 修当前可见故障，A25 校正能力声明。A14 等具体失败，A17 等测量。A20 随改动更新现状。

每个 PR 引用工单号。默认以一条旧代码会失败的入口用例或受控测量验收，补一两条直接相关的回归。只有改变已有状态、数据或执行合同时才扩展测试。优先迁移真实调用方并删除旧路径。跨进程的终态、取消、效果语义和分布式执行变更沿用仓库 ADR 与 Dataset 要求。

## 验证记录

### 当前 HEAD

- **本轮补充探针。** A01 用合成 Provider 响应复现第二个动作复用已批准记录。A21 在独立进程走 `__main__`，替换 serve 观察首个 Run 前的工具目录，确认真实入口未预注册 Web。
- **前轮复核。** A01 复现单实例同名审批唤醒错误 waiter，A04 复现暂停导致关闭等待，A07 复现 Uranus 真实 payload 的 sidecar 错误，A15 复现接纳和执行的 workspace 不一致。审批、SQLite 和持久事件 39 项定向测试通过。
- **初次当前 HEAD 核查。** A22 通过 Service 在临时 workspace 往返写入合成文件。A23 通过 Service 试全部裁决值并验证 sidecar 的错误映射。A24 以记忆提交失败替身观察母题状态和无效引用。A27 在现有工件测试 fixture 的正文与摘要读取之间注入内容变化。
- **后端定向回归。** 28 通过，1 项 HTTP loopback 测试未选。覆盖 sidecar、AgentService 和委派。Architecture Verification 返回 PASS。
- **Desktop。** TypeScript 检查、40 项测试、轨道与场景脚本及生产构建通过。JSONL smoke 实际拉起 Python 子进程。构建报告 Three.js 分块超出 Vite 默认体积提示，交互性能待测。
- 当前 HEAD 的结果覆盖本地契约和合成故障。全量 Python 回归、桌面真实交互、Tauri 安装包、真实 Provider/MCP 与生产环境验收未纳入本轮。

从仓库根目录复跑定向测试和构建：

```sh
(
  export OPENAI_API_KEY= LLM_API_KEY= P2_SECONDARY_API_KEY=
  export TSAGENT_LLM_PROVIDER=ollama OLLAMA_BASE_URL=http://127.0.0.1:1/v1
  export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
  export TSAGENT_PYTHON="$PWD/.venv/bin/python"
  "$TSAGENT_PYTHON" -m pytest -q tests/test_desktop_sidecar.py \
    tests/test_v23c_agent_service.py tests/test_delegation.py \
    -k 'not test_http_sidecar_enforces_host_and_origin'
  "$TSAGENT_PYTHON" evaluation/architecture_verification.py
  cd apps/desktop && npm test && npm run test:orbits && npm run test:scenes && npm run build
)
```

### 上一基线 `6beb0845c`

- **6 组本地探针复现问题。** 使用临时 SQLite、测试替身和内存流。A01 复现两实例审批不唤醒，A02 复现公开投影提前报告成功，A05 复现重启未启动调度及 claim 后没有 Run，A07 复现依赖节点提前接纳及重试多一条 Handoff，A08 复现 schema 拒绝前已变更，A09 复现挂起订阅阻塞 health 分发。探针使用既有测试 Launcher 与临时 fixture，生产代码未改。
- **架构静态门禁通过。** `.venv/bin/python evaluation/architecture_verification.py` 返回 PASS。当前脚本的检查范围与上述生命周期问题不同。
- **Desktop npm test 通过。** TypeScript app/test typecheck 和 40 个 client/factory/controller 用例通过。未运行 WebView、安装包和真实用户操作。
- **Python 回归未全绿。** `870 passed, 17 skipped, 4 failed, 7 errors`。MCP 7 个 setup 错误源于测试清理受保护的用户缓存。失败包括 HTTP loopback 端口绑定被拒、两个 Seatbelt/宿主能力用例失败、一个事实记忆断言依赖不可达的 Provider。排除了 `test_tools_execution.py`，以避免运行该文件里的联网用例；DeepSeek 凭据被屏蔽，测试强制 Ollama。未调用 DeepSeek 或运行真实模型验收。

上一基线使用的 Python 回归和 Desktop 门禁：

```sh
OPENAI_API_KEY= LLM_API_KEY= P2_SECONDARY_API_KEY= TSAGENT_LLM_PROVIDER=ollama \
  HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python -m pytest -q \
  --ignore=tests/test_tools_execution.py
(cd apps/desktop && npm test)
```

真实环境验收由对应负责人单独完成。A01/A04 在可控的双进程环境暂停或终止 worker 并验证 lease、取消和外部效果；涉及外部写入只用测试服务、幂等键及合成数据，验收同一效果只提交一次。A10 用两个测试身份和真实 ReMe 实例验证搜索范围与删除传播。A09/A18 用目标 OS 的打包客户端验证长订阅期间控制请求、断开后的重启和历史恢复。Provider 能力、网络服务可用性和安装包体验的结果各自归档。

## 交付与复查记录（2026-11-10）

### 本批提交

- `118581d8` A01/A02：审批绑定具体动作并以持久决定唤醒；公开 Run 状态/输出/工件只取已提交事实。
- `3fff8824` A08：SQLite 在验证身份后执行原子迁移。
- `076c6411` A12：默认离线门禁 + Desktop gate。

### 复查后补漏（同批修正）

- A01：`_json_text_equal` 改用 canonical JSON 比较，`true`/`1`、`false`/`0` 不再判同；同键重放不同参数仍报 `IDEMPOTENCY_CONFLICT`。手工传入序号的 gate 测试替换为档案要求的假 Provider 两轮用例（无 ID、参数不同；批准第一轮后第二轮单独进入待审批）。
- A08：身份检查、建表、补列和元数据更新收进同一写事务；新增触发器注入回归——迁移中途失败时表与元数据均不留半成品。
- A02：同一夹具改经 `service.get_run` 入口验收（真实 bootstrap 索引 + 未提交终态）。
- A12：离线环境变量提升至 workflow 级，Python job 与 Desktop job 共享；LC16 smoke 拉起真实 Python sidecar 时继承同一屏蔽配置。
- 风格：`run_id`、`record.status`、`read.artifacts` 的默认回退已移除，按既定不变量直接访问。

### 复跑命令

```sh
OPENAI_API_KEY= LLM_API_KEY= P2_SECONDARY_API_KEY= TSAGENT_LLM_PROVIDER=ollama \
  OLLAMA_BASE_URL=http://127.0.0.1:1/v1 HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  .venv/bin/python -m pytest -q tests -m "not host_integration" \
    --ignore=tests/test_tools_execution.py
.venv/bin/python -m pytest -q tests -m host_integration   # 宿主能力分组
(cd apps/desktop && npm test && npm run test:orbits && npm run test:scenes && npm run build)
```

### 延期项

- A16 承接 wire 漂移合同 fixture；A11 承接会话切换、审批等 UI 行为用例。
- `host_integration` 在 ubuntu CI 上只实跑 HTTP loopback 用例；两条 Seatbelt 用例保留 macOS 宿主验收。
- A22（Forge 文档保护）按档案排期为下一单；A23 待 Earth 裁决语义产品裁定。
- 验证范围为本地离线检查；GitHub Actions 实跑未覆盖。
