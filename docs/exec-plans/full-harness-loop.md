# 完整 Harness 闭环实现范围

日期：2026-09-27。状态：实现与入口级离线验收完成；真实 Provider、DuckDuckGo 网络和媒体播放等真实场景按 AGENTS.md 留待独立环境验收，离线结果不等同真实能力验证。按用户要求，完整功能是交付范围；分批只表示工程依赖顺序，不能以单场景演示或局部测试通过宣布交付。

本计划补充并调整 mcp-and-goal-contract-implementation.md 的交付范围。保留当前工作区已有 DuckDuckGo MCP 适配及其它修改；已有 mocked/offline 覆盖，并新增受控本地 MCP stdio 子进程与 AgentService fresh-research 入口回归。当前环境未安装 `duckduckgo-mcp-server`，因此没有执行真实 DuckDuckGo server 或联网搜索验证。

2026-09-27 进度：版本化目标/交付验收及 DuckDuckGo MCP 客户端已有离线覆盖；本轮将只读无效果失败接入 Runtime 有界重规划，并拆分 tenant/session 对话与 tenant/user learned memory。针对性组合回归：重规划/MCP/目标契约 165 passed；记忆与 workspace 隔离 119 passed；隐式记忆查询与上下文策略 133 passed。九项能力及入口级完整矩阵仍未全部验收，本计划保持进行中。

2026-09-27 补充：自动历史归档改为仅保存用户原话；Intent、Planner、Direct Answer、NextAction/Workflow Selector 和 ContextService 将动态资料放到用户级数据消息；RepositoryIndexer 已从进程全局迁移到 ApplicationContext 的 workspace-root cache，由 RunContext 注入规划与执行；Workspace contract 错误不再静默清空，可选 memory-resolution 错误记录诊断。新增提示边界、双 workspace、Scoped repository search、诊断/fail-fast 回归，并补 CLI 在一轮 Run 完成后继续接收下一条请求的入口回归。ToolRegistry 新增动态 availability/reason；工具选择、Compiler、ContextService 与 fresh-research Planner 按实际 MCP server 状态阻止不可用调用；failure detail 经 Runtime、Service durable event/output 及 CLI 展示。AgentService→Runtime 的缺少搜索 server 离线入口回归通过。此前完整离线套件为 964 passed、5 subtests passed（排除 5 个显式 real/live 测试文件）；本轮 checkpoint resume 与终态提交边界已接入，正在重跑回归。用户确认使用 DuckDuckGo 后，将 server、browser 后端和解析依赖补入 README 快速安装对应的 `requirements-lock.txt`，并增加锁文件回归；另有 Service 重启后读取上一轮输出、不进入 Planner 的入口测试。当前执行测试的环境尚未安装 server，mocked/offline 测试不验证真实 MCP 进程或联网搜索。AgentService/CLI 完整矩阵、真实模型与真实 DuckDuckGo 连通性仍未完成，因此计划继续进行。

2026-09-27 checkpoint 收口：Service resume 已从 durable Run index 恢复 active Workflow，不再重新启动 UniversalAgent；持久化目标/输入绑定，Coordinator 的 Workflow checkpoint 提交与 Service 的 Run 终态/RunOutput 提交分离。`BLOCKED → run_resumed` 开启下一次尝试，事件 Oracle、SQLite terminal projection 和 event stream 均按最近一次恢复解析；`FAILED_TERMINAL` 在 Service 入口 fail-fast。缺少 `terminal_outputs_verified` 现在按未验证处理，不能完成 Run index 或成功返回。Service 重启恢复、事件续接、显式核验门控离线回归通过；完整离线套件 **971 passed、5 subtests passed**，排除 5 个 real/live 测试文件。全场景入口矩阵仍未完结；真实 Provider、真实 DuckDuckGo MCP/联网及跨进程同时存活 worker 仲裁仍未验证，计划保持进行中。

2026-09-27 安全修复补充：代码生成 Workflow 3.1 删除用 `run_python` 做“语法检查”的执行阶段，改为写入后由 `PythonSyntaxValidator` 对 workspace 文件做 AST 检查；校验器现在接收真实输出路径，而非 Artifact 文件内容。Workflow capability 降为 read/write，选择器 Dataset v2 覆盖缺少执行能力时仍可实例化。有效/无效 Python、带文件写入副作用的源代码均有离线回归，证明只做语法解析；静态验证失败不会将 Workflow/Run 标记成功。最新完整离线套件 **973 passed、5 subtests passed**（同样排除 5 个 real/live 测试文件）。

2026-09-27 失败效果与安全修订：`ActionResult` 增加结构化 `side_effect_state`，执行器把首步参数 schema 验证错误、调用前环境拒绝标为 `NONE`；计划后续步骤或一般工具错误无法证明未产生效果时保持 `UNKNOWN`。Runtime 仅允许白名单错误在只读或有明确“无效果”事实时进入有界 REPLAN；同一门控也阻止效果未知的写操作被同工具重试。Checkpoint 对缺少效果证据的失败写入/执行记录 `UNKNOWN`，而非推断 `FAILED_BEFORE_COMMIT`。相关回归 **52 passed**；完整离线套件 **978 passed**（排除 5 个真实 Provider/联网测试文件）；`compileall` 与 `git diff --check` 通过。DuckDuckGo MCP 仍使用本地 stdio 且不需要 API key；真实 server/联网及 Provider 纠错能力不在离线验证范围。

2026-09-27 跨 Service worker 仲裁：AgentService start/resume 现在在 SQLite fence 上配套持有 45 秒 worker lease，并每 15 秒续租；另一个 Service 只能在 lease 过期后取得新 fence，`RUNNING` 但没有 lease 证据时 fail closed。续租失败会取消本地 launcher task，旧 fence 的 durable 写入仍由现有 fencing 拒绝。新增双 SQLite 连接、双 Service 活跃恢复冲突、过期接管、旧 heartbeat 拒绝、缺失租约 fail-closed 与 v2.3H3→H4 migration 回归。目标服务外部在途效果无法由 SQLite lease 撤销，OS 级暂停/终止及真实外部效果仍待测试；本实现不宣称 exactly-once 外部副作用。详见 ADR-0037。

2026-09-27 验收缺口修复：Runtime 现在在成功执行后的整体交付/验收门发现缺口时，只有在缺失项均为可验证的文件交付或验收条件、所有既有任务已成功/跳过、effect truth 允许完成且尚有目标轮次时，才进入现有有界 REPLAN。结构化文件与验收证据交给 Planner；新计划继续经过目标契约、效果授权、重规划进展检查与单任务 ID 所有权约束。任务所有权历史按 ID 保持不变，同时允许重规划引入全新 ID。另修复字面文件写入解析器把“内容必须包含……”误当完整文件正文的问题，并让验收重规划提示明确区分“没有失败任务”与执行失败。AgentService 临时 workspace 端到端回归覆盖先写出不合格文件、验收失败、只修改授权文件并通过复验；相关组合 **99 passed**，完整离线套件 **985 passed**（排除 5 个 real/live 测试文件），`compileall` 和 `git diff --check` 通过。真实 Provider 是否能稳定提出有效修订，以及真实 DuckDuckGo MCP/联网能力，尚未验证；九项能力与完整入口矩阵仍开放。

2026-09-27 同 session 目标纠正：新增 AgentService→Runtime 临时 workspace 端到端回归，第一轮创建文件，第二轮以 `conversation_relation=correction` 修改同一目标；durable `run_completed` 证据保留原 `goal_id`、递增 `goal_version`，继承新的 `CODE_EXECUTION`/`COMMAND_EXECUTION` 禁止效果，且事件中没有 execute Task。该场景通过；完整离线套件 **986 passed**（排除 5 个 real/live 测试文件），`compileall` 和 `git diff --check` 通过。checkpoint `RESUME_EXACT` 保留原 Workflow 输入是既有契约；但对未知外部副作用要求用户确认时，Service `ResumeRunRequest` 尚无可传入并与 effect record 绑定的结构化新状态证据。不能把自由文本当成外部事实或据此解锁重放，因此这类 WAITING_USER 续接仍未完成。

2026-09-27 同 session 活跃 Run 串行化：SQLite `reserve_service_start` 与恢复时的 worker-fence 获取均在写事务中检查 tenant/session 的其他活跃 Run 或有效 worker lease，start-vs-resume 竞争因此在 Store 原子边界 fail-fast；Service 对外返回 `RUN_ALREADY_ACTIVE` 和冲突 Run ID，不排队。终态提交同步释放 worker lease，同 session 后续顺序启动仍可用。新增双 SQLite 连接并发启动、恢复与活跃 Run 冲突，以及 Service 两实例/顺序 Run 回归；完整离线套件 **988 passed**（排除 5 个 real/live 测试文件），`compileall` 和 `git diff --check` 通过。DuckDuckGo MCP 仍是本地 stdio、无需 API Key；真实 server 与联网搜索未运行。WAITING_USER 的结构化外部状态证明、完整 AgentService/CLI 场景矩阵及真实 Provider 仍开放。

2026-09-27 DuckDuckGo MCP 入口级离线验收：新增真正启动 MCP SDK stdio 子进程的合成 server fixture，验证工具发现/参数调用、来源文本回传及客户端关闭后 server 收尾；再从 AgentService 的 fresh-research 入口走完规划、`web_search`、来源综合、`RUN_COMPLETED` 与子进程关闭。另覆盖工具调用等待中取消客户端任务，确认 MCP 子进程退出。完整离线套件 **991 passed**（排除 5 个 real/live 测试文件），`compileall` 与 `git diff --check` 通过。此处使用的是本地受控 MCP server，不是实际 DuckDuckGo server 或联网查询；完整 AgentService/CLI 矩阵、真实 Provider 与网络验收仍开放。

2026-09-27 取消部分结果收口：补齐 `CancellationCoordinator.mark_safe_to_interrupt` 到 Store finalization 的证据传递，Runtime 在取消边界收集已完成步骤、仍有效的验证制品及当前步骤效果状态；Service 在一个 SQLite 事务中发布 `CANCELLED`/`TIMED_OUT` 事件、部分 `RunOutput` 与制品事实。入口回归确认取消检索任务时已核验文件继续保留、输出披露该文件并明确列出尚未完成的步骤，未完成操作不会伪报完成，公开制品引用仍为不透明 `artifact://` URI。事务故障注入覆盖阶段推进、revision、事件和 head 提交点，保证输出/制品与终态事件原子回滚。完整离线套件 **992 passed**（排除 5 个 real/live 测试文件），`compileall` 与 `git diff --check` 通过。实际 DuckDuckGo server/网络和真实 Provider 未运行；完整入口矩阵仍未完成。

2026-09-27 外部 Guard 绑定、Service 传递与审计持久化：Checkpoint ResumeValidator 不再把任意一条 `VERIFIED` 外部观测视为满足历史 Guard；必须逐项匹配资源 ID、Guard 类型和 checkpoint 绑定的预期值，提供有时区且晚于历史检查时间的当前观测，并确认 observed value 与预期一致。无关/重复观测、状态与观测值自相矛盾 fail closed，缺失或旧证据要求澄清。公开 `ResumeRunRequest` 增加通用 Guard 观测 DTO，内容进入请求 digest，并已由 Service 重启续接 E2E 贯通至 Workflow Checkpoint 校验；验证通过的 Guard 和检查时间随恢复 Checkpoint 持久化，重启后仍可审计。Checkpoint/Workflow 定向测试 **26 passed**；最新完整离线套件 **997 passed**（排除 5 个 real/live 测试文件），`compileall` 与 `git diff --check` 通过。该 DTO 还没有确认 unknown effect 对应的 task/idempotency key 和“已提交/未提交”结果；未知副作用仍安全阻塞，Service 级 WAITING_USER effect-resolution 仍开放。

2026-09-27 未知副作用的 WAITING_USER 续接：公开 `ResumeRunRequest` 新增结构化 `NoEffectConfirmation`，进入请求 digest 并贯通 RunResumeCoordinator → WorkflowCheckpointRequest → ResumeValidator。仅 `RESUME_EXACT` 可以消费；确认必须唯一匹配当前 active task 的 uncertain effect、非空 idempotency key 与 external reference，且 `checked_at` 带时区并晚于最新 Checkpoint；其它未决效果、错误绑定、过期时间、已提交效果均不能执行。通过后以 `ATTESTED` evidence 写入恢复 Checkpoint，明确这是 caller attestation，不是系统对外部服务的自动读取。ResumeValidator 的澄清现在生成 WAITING_USER Checkpoint；Service/SQLite 发布非终态 `run_waiting_user` 与用户可见问题，restart 后可带绑定确认续跑。Service E2E 覆盖首次等待、重建 Service、确认后执行一次并完成；Checkpoint 单测覆盖错键、错引用、过期证据、其他未决效果和已提交效果不解锁执行。checkpoint/Service/SQLite 定向回归 **83 passed**；完整离线套件 **1004 passed**（排除 5 个真实 Provider/联网测试文件），`compileall` 与 `git diff --check` 通过。用户确认仍需由调用方先查询原外部系统，不能证明该查询过程真实发生；没有外部读取能力时不得把此 attestation 宣称为外部事实。

2026-09-27 CLI 等待/续接回归：CLI watcher 在收到非终态 `RUN_WAITING_USER` 后停止等待并返回输入循环；增加 `/resume <run_id> [JSON 证据对象]`，只把明确提供的结构化 Guard/no-effect evidence 映射进公开 Resume DTO，不从自由文本推断证明。新增 watcher、CLI Service 请求和命令分发回归；相关 CLI/Service 组合 **49 passed**。完整离线套件 **1007 passed**（排除 5 个显式 real/live 测试文件），`compileall` 与 `git diff --check` 通过。README 补充 CLI 启动与 DuckDuckGo MCP 免 API key 说明；当前测试环境没有安装 DuckDuckGo MCP server，因此真实 server/联网与真实 Provider 验收仍未执行。

2026-09-27 检索到报告的 AgentService 入口闭环：新增临时 workspace + 本地受控 MCP stdio 子进程场景，验证 fresh-research 查询、来源事实传递、Markdown 报告落盘、Artifact digest/verified 发布、最终输出路径披露与 MCP 子进程关闭；全程无真实 Provider 或公网调用。完整离线套件 **1008 passed**，覆盖研究/CLI/MCP/Service 的组合回归 **123 passed**；`compileall`、`git diff --check` 通过。README 快速开始改为显式排除可能访问公网、调用真实 Provider 或执行本地工具的 live 文件，并说明 DuckDuckGo 搜索不需 API key 但需要网络、LLM 仍需单独配置。完整入口矩阵及真实 Provider/网络验收尚未结束。

2026-09-27 多交付项数量的 Service 闭环：把原来将“做两份报告”放在单文件用例里的参数化假阳性改为独立 E2E，明确 goal quantity=2，要求两条不同 output 路径各自落盘、Artifact digest 与实际字节一致、Service 逐项发布 verified 写入并在输出中列出两份报告。既有 GoalVerifier 缺一份即拒绝 COMPLETE 的离线单测继续覆盖负例。组合回归 **132 passed**，最新完整离线套件 **1008 passed**（排除 5 个可能触发真实服务/本机执行的测试文件）；`compileall` 和 `git diff --check` 通过。剩余完整入口矩阵及真实 Provider、DuckDuckGo 网络验收仍未完成。

2026-09-27 Bad Apple 综合场景与禁止运行披露：AgentService 临时 workspace E2E 覆盖生成本地 HTML 播放器和说明、素材缺失时不伪造来源或下载、不运行程序，并要求最终答复披露播放效果未验证。该回归先发现 `ExecutionNeed` 的主体到动作正则把“程序……不要运行”误判为要求执行，快捷路径因而丢掉用户要求的两个文件；现将否定范围限定到动作前当前子句，且 Finalizer 在目标交付可验证时依据 `CODE_EXECUTION` prohibition 和实际代码 Artifact 明确补充“未运行/未验证”，不依赖模型措辞。另加两份报告中第二次写入失败的 Service E2E：只保留第一份的 digest/verified evidence，Run 不完成并披露剩余数量。定向回归 **92 passed**；完整离线套件 **1011 passed**（排除 5 个可能触发真实服务/本机执行的测试文件），`compileall` 与 `git diff --check` 通过。该素材场景使用合成本地 fixture，不代表真实媒体授权、下载或播放验证；完整入口矩阵、真实 Provider 与 DuckDuckGo 网络验证仍开放。

## 1. 最终行为契约

系统应能接收自然语言目标，明确交付与约束，获取必要上下文，选择可用工具，执行并观察，根据真实结果修正计划，验证所有交付，输出真实成果与未完成部分。用户纠正、取消、进程重启、工具失败和效果未知均属于主流程。

完整不意味着任意任务保证成功。权限不足、能力缺失、必要输入缺失、预算耗尽必须明确收敛；不能用无限重试、伪造产物或擅自扩权获得“成功”。

## 2. 一条控制主链

```text
接收请求 / 用户纠正 / 恢复
  → 构建版本化目标与约束
  → 加载有来源的上下文和当前可用能力
  → 决定下一步：澄清 / 规划 / 执行 / 核验 / 结束
  → 执行前授权与参数检查
  → 执行并持久化实际观察、效果及未知状态
  → 验证交付要求
      ├─ 全部满足 → 最终交付
      ├─ 可修正 → 诊断 → 修订计划 → 下一轮
      ├─ 缺输入 → 等待用户 → 续接
      └─ 无法继续 / 取消 / 预算耗尽 → 诚实收敛部分结果
```

使用现有 Runtime、Goal、Task、Store、Service 迁移这条链路，不新建第二个 agent loop。原有只重试动作的恢复与未接入的 replan 逻辑合并到这条主链，删除迁移后的重复控制路径。

## 3. 必须交付的九项能力

| 能力 | 实现内容 | 完成标准 |
| --- | --- | --- |
| 目标理解与修订 | 一个权威目标，包含交付项、数量、范围、禁止效果和验收要求；区分生成文件、运行程序、展示代码 | 用户说“写程序，不运行”后，旧执行计划失效；禁止效果不因重规划而丢失 |
| 上下文与能力感知 | session/user/workspace 分层；工具可用状态与原因进入决策；引用绑定真实产物 | 工具不可用不继续宣称能调用；跨 session 不泄漏原始历史；缺必需上下文可见 |
| 规划与工具执行 | 内建工具和 MCP 共用目标、授权和效果路径；任务关联交付项与依赖 | 复杂任务可组合读、写、命令、搜索及 MCP；语义快捷路径不能覆盖目标 |
| 观察与诊断 | 保存实际 stdout、错误、来源、文件变化和外部对象；区分计划错、参数错、环境错、能力缺失、结果不合格 | 诊断引用具体观察，产生可执行修正或明确阻塞原因，不能只输出 failed task evidence |
| 有界重规划 | 按剩余目标修改步骤，保留已验证效果；控制轮数、时间和调用预算 | 能从错误计划和不合格产物中修正；重复无进展时停止；不重复未知外部写入 |
| 分层验证 | 参数/计划一致性、执行结果、产物事实、业务验收分别验证 | 文件存在不等于程序可用；两份交付逐项满足；无证据不得 COMPLETE |
| 用户交互与续接 | 澄清等待可持久化；用户纠正更新目标版本；同 session 修改有确定顺序 | 等待后续接原任务；旧版本任务不得在纠正后继续越界执行；CLI 清楚显示当前阶段 |
| 生命周期与恢复 | 持久化目标版本、计划、观察、效果与预算；取消与重启对账；终态唯一 | 重启不丢未完成目标、不重放已确认写入；取消披露部分效果；外部未知效果可追踪 |
| 最终交付与记忆 | 交付说明从证据生成；偏好学习有来源、独立生命周期与诊断 | 报告路径、运行结果、失败和未验证项准确；普通终态不等待无关事实抽取；摘要不洗成用户事实 |

## 4. 决策权限与错误恢复

模型负责理解一般自然语言、选择任务策略、解释观察和提出修正。确定性代码负责 schema、授权、预算、状态迁移和事实核验。模型不能绕过约束，关键词规则也不能重新定义用户目标。

修复当前两种具体错误属于整体迁移的一部分：

- 不再因为 requires_execution=true 就把“生成 Python 文件”转为“运行 Python 源码”。文件变更与代码运行是不同效果。
- “我叫你写程序”不能因为匹配“我叫”进入姓名学习。个人事实来源与任务纠正分开判定；学习失败不拖住交付。

恢复决策至少覆盖：修改计划、修正参数、选择已授权替代能力、重新验证、询问必要输入、结束。连接恢复与动作重试分开；权限拒绝不能通过换工具绕过；对执行结果未知的写入先对账。

## 5. 业务验证的边界

复用已有验证器并补齐交付关联，不制作万能“LLM 说通过就通过”的评分器。

- 文件/报告：实际路径、非空内容、数量、要求的结构、来源和相互区分的交付项。
- 程序：代码文件及依赖说明；按用户授权验证语法、测试与运行行为。“不运行”时不得偷偷执行生成代码，须明确运行未验证。
- 新闻/研究：实际搜索时间与来源、与目标相关性、资料提取和综合；无 fresh evidence 不生成当前事实结论。
- 外部 MCP 动作：实际对象身份和效果；选定服务支持时回读，不可确认时保持未确认。
- 媒体/交互产物：素材及依赖、可启动入口、可观察行为；只有静态检查时不得宣称播放或交互已经验证。

Bad Apple 任务属于综合验收：明确素材来源与播放形式，交付程序及使用说明；用户禁止运行时遵守；缺素材时指出缺口或请求必要输入，而不是把整个目标改成一次 python-source 执行。

## 6. 实施顺序与迁移要求

1. **统一数据契约**：目标修订、交付项、禁止效果、观察、验证结果；迁移 Intent/Planner/Goal/Store 调用者。删除自然语言多重解释产生的冲突路径。
2. **接通决策循环**：在当前 Runtime 接入观察、诊断和有界重规划；迁移 recover/replan，明确错误类别与进展判据。
3. **统一工具主链**：内建与 MCP 的 schema、身份、可用状态、授权、结果、调用生命周期接入；保留已有 DuckDuckGo 实现并核验接线。
4. **完成验证与交付**：交付项逐项匹配证据；失败修正可以再次进入循环；最终输出来自同一验证投影。
5. **完成跨轮与恢复**：目标纠正、持久化等待、取消、重启对账、同 session 并发顺序；补齐 session/workspace 隔离。
6. **收束辅助功能与验收**：记忆来源、摘要策略、非可信内容边界、诊断可见性；消除辅助工作阻塞终态；完成全矩阵回归。

每一步是完整交付的依赖，不是可以提前宣布“闭环完成”的独立替代品。开发中每批保持可运行与可回归；不同时长期保留新旧两条实现。

## 7. 完整验收矩阵

必须从 AgentService/CLI 公开入口验证，不能只调用内部方法证明已接线。

| 场景组 | 必测内容 |
| --- | --- |
| 基础交付 | 纯回答、原样命令 stdout、写入读取、多文件、代码、报告 |
| 目标与约束 | 无路径请求、否定执行、两份报告、指定路径、保留原内容 |
| 多轮 | 引用前一产物、纠正计划、修改后验证、等待用户后继续 |
| 自我修正 | 初始计划遗漏、工具参数错、执行失败、产物不合格，观察后形成不同且有效的行动 |
| 能力与来源 | 搜索可用/不可用、来源不足、MCP 工具错误、能力说明与实际可用性一致 |
| 生命周期 | 执行中取消、写入后断连、进程重启、重复请求、同 session 并发 |
| 隔离与信任 | 双 session、双 workspace、恶意 repo/tool 文本、摘要来源与临时信息 |
| 综合任务 | Bad Apple 程序、资料检索到报告、跨轮修改项目、多交付项部分失败 |

离线测试分别使用受控决策响应验证接线、真实临时文件/本地进程验证效果、受控服务模拟外部失败。这些不能代替真实 Provider 的理解与决策能力评测。

真实场景测试按 AGENTS.md 在独立环境完成：固定代码与 Provider/服务器版本、测试账号与合成数据，记录 Run ID、逐轮行动、实际产物、缺失要求、人工干预与耗时。开发样例和未进提示的留出样例分开；Provider Error 单列；失败后的定向复跑不覆盖首跑。

最终交付必须同时附上：九项能力的实现位置、入口级离线证据、尚未通过的真实验收项、已知限制。任何未接线或未实现能力都不能包装为“完整功能已实现”；真实验收未完成时只能声明实现与离线验证状态。

## 8. 离线验收收口（2026-09-27）

第 7 节全部场景组已由 AgentService/CLI 公开入口回归覆盖：基础交付与目标约束见 `tests/test_ha_r1_harness.py`、`tests/test_ha_r2_goal_pipeline.py`、`tests/test_ha_r3_file_evidence.py`、`tests/test_ha_r4_gate_a.py`；多轮纠正、等待续接与重规划见这些入口测试、`tests/test_v23c_service_e2e.py`、`tests/test_v23d_d4a_cli.py`、`tests/test_p2_lh3_replan_preservation.py`；能力可用性及 MCP 生命周期见 `tests/test_tool_availability.py`、`tests/test_duckduckgo_mcp.py` 和 `tests/test_v23c_service_e2e.py`；取消、重启、幂等与并发见 Service、SQLite finalization 及 cancellation 回归；隔离与非可信上下文见 `tests/test_runtime_context_isolation.py`、`tests/test_context_trust_boundary.py`、`tests/test_v24d_memory_h1a.py`、`tests/test_v24d_memory_learning_boundary.py`。综合任务覆盖本地受控 MCP 检索成报告、Bad Apple 缺素材/禁止运行和两份交付的部分失败。

按 README 快速开始命令运行离线套件：**1011 passed**；另 `python -m compileall -q agent tests` 与 `git diff --check` 通过。明确排除的 5 个测试文件可能访问真实 DeepSeek/公网或运行本机工具。当前环境未安装 `duckduckgo-mcp-server`，因此没有执行真实 DDG server/联网查询；也没有真实 Provider、媒体授权/播放、OS 级进程中断或 Desktop Tauri host 验证。这些留作独立真实场景验收，不属于本次离线结论。
