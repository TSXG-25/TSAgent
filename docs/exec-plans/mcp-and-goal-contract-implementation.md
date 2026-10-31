# MCP 接入与目标契约修复实现大纲

日期：2026-09-26。状态：实施中。首个 MCP 服务改为免 API Key 的 DuckDuckGo MCP；搜索与提取适配器已迁移，35 项相关离线测试通过。本轮不连接真实 MCP 服务。

2026-09-27 补充：ToolRegistry 已记录动态 availability 与失败原因；可用工具列表、ContextService 资源说明、Compiler 和 Planner 不再把“已注册”当作“可调用”。fresh-research 请求按需加载 web 模块并在规划前检查 DuckDuckGo MCP server 命令；缺失时记录 `RESEARCH_TOOL_UNAVAILABLE` 并阻止无来源回答。专项离线回归已通过，真实 server/网络仍未验证。

2026-09-27 补充：用户选定 DuckDuckGo 后，补齐 README 快速安装使用的 `requirements-lock.txt`：MCP server 0.6.1、browser 后端 `curl-cffi` 及解析依赖均固定版本；增加锁文件回归。还验证 Service 进程重启后“输出呢”读取持久化 RunOutput 而不进入 Planner。完整离线套件 964 passed、5 subtests passed；运行环境本身尚未重装锁依赖，因此真实 server/网络仍未验证。

## 1. 范围与当前事实

暂按 TSAgent 作为 MCP host/client，调用外部工具来设计。若目标是让其他助手通过 MCP 调用 TSAgent，则需要另写 server 暴露方案，不与本次混合。

当前 agent 源码中未找到生产 MCP client 接线；requirements.txt 有 fastmcp，但依赖存在不等于能力接通。现有个人项目规划已提出 MCP 方向，本大纲将它拆成可实施的批次。

本轮静态核对：

| 位置 | 当前实现 | 要解决的问题 |
| --- | --- | --- |
| agent/registry/tool_registry.py | callable 生成 schema；模块级 registry | 外部 schema 接入、工具身份、可见范围 |
| agent/executor/plan_executor.py | 读取全局 registry 并调用工具 | MCP 必须经过现有执行与授权链，不能另开绕行路径 |
| agent/cognition/intent_engine.py | 词法 outcomes 与模型 outcomes 无条件合并 | “不创建文件”仍可能被加上 FILE_MUTATION |
| agent/goal.py | 现有 GoalVerifier 与完成证据 | 多份交付、外部效果需要逐项核验 |
| agent/service/context_factory.py | memory_namespace=tenant:user、scope=user | session 原始记录与长期事实混用 |
| agent/runtime_context.py / agent/repository/indexer.py | ApplicationContext 按 workspace root 持有 indexer，RunContext 显式注入 | 已移除进程级 RepositoryIndexer 单例及其 service/memory 包装入口 |
| agent/orchestrator/context_builder.py | Workspace contract 失败向上暴露；可选 memory-resolution enrichment 记录诊断 | 必需 repository grounding 无 index 时显式失败；入口级 diagnostics 展示仍待验证 |

上述是代码现状与既有审计依据，不是本轮真实场景验证结果。HA-R4 的 397 项通过是历史离线成绩，不证明当前真实语义或业务完成率。

## 2. 推荐首批范围

只安排三项：统一目标契约、MCP 最小接入、外部调用的授权与证据闭环。首个 MCP 服务改为 [DuckDuckGo MCP Server](https://github.com/nickclyde/duckduckgo-mcp-server)，采用本地 stdio 子进程，不需要 API key。TSAgent 锁定 `mcp==1.27.2`，因此 server 固定为兼容 MCP 1.x 的 `duckduckgo-mcp-server[browser]==0.6.1`，避免新版 MCP 2.x 依赖冲突。第一步只接搜索与网页提取，不建设多传输适配。

首场景建议为“读取指定资料 → 整理 → 生成本地报告”。它能检查 MCP 到现有文件证据链的完整接线。外部写入随后用合成对象验证，再接个人账号。

不在首批建设插件市场、通用 OAuth 平台、动态工具搜索、多 Agent 或调度平台。首个适配器只发现并调用 DuckDuckGo MCP 的 `search` 与 `fetch_content`；TSAgent 继续通过现有 web 工具入口进入授权和执行链。因 server 进程按工具调用启动，关闭结果 URL 缩短以避免跨进程失效的 `ref://` token。服务没有精确时间过滤，工具结果会如实披露这一限制。

## 3. 批次 A：统一目标契约（P0）

目的：解决 Gate A 漏识别，以及词法规则反向覆盖正确语义的问题。

实现：

1. 在现有 IntentResult / Goal 契约上表达交付要求和禁止效果。交付项包含稳定 ID、类型、数量，以及用户明确的路径/对象约束；不另建平行目标系统。
2. 一般自然语言由一次结构化语义分析形成目标；确定性解析只处理已定义且完整匹配的命令语法。删除对一般自然语言的无条件词法 outcomes 并集，迁移相关调用者与旧测试。
3. requires_execution 与 artifact_required 从交付要求推导，domain 只描述话题。模型不能同时维护三份相互矛盾的最终决策。
4. 授权层负责检查执行效果是否符合用户范围与禁止约束；目标分析不授予任意路径或外部对象权限。模型解析失败或目标冲突须显式失败/澄清，不退回自由回答假装完成。
5. Planner 的任务与交付项关联；Verifier 按交付项关联实际证据。两份报告必须有两个满足对应要求的产物，不能靠两个重复引用或任意两个文件凑数。
6. Finalizer 列出已完成与未完成交付项；现有 HA-R3 实际文件证据继续作为 Gate B 的权威事实。

主要修改：cognition/intent_schema.py、intent_engine.py、effect_authorization.py、state.py、goal.py、orchestrator/planner.py、finalizer.py，以及必要的 Store 序列化调用者。

离线验收：

- 六条无路径交付请求在受控正确语义下进入执行；这仅验接线。
- “只展示代码、不创建文件”在正确语义下零文件变更，旧词法结果不得覆盖。
- 需要两份却只产出一份、路径不符、缺少要求的运行证据，均不能 COMPLETE。
- 制作计算器与一次算术回答分离；模型结构错误不进入虚假完成路径。
- 保留原始措辞、解析目标和缺失证据，供真实语义评测独立比较。

真实验收：原六句、未进入提示的变体、否定句及多份交付；分别评估语义正确率和最终业务产物。不能用固定语义响应替代模型识别验收。

## 4. 批次 B：MCP 最小接入（P0）

目的：让外部工具进入同一条主执行链。

```text
Service / Application 管理连接
  → 当前作用域的工具目录
  → Planner → Compiler → 授权 → Executor
  → MCP 调用结果 → 效果验证 → Store → Finalizer
```

首个落地切片：`tools/web.py` 的搜索/提取入口经 `agent/mcp/duckduckgo.py` 调用本地 DuckDuckGo MCP；每次调用发现目标工具、执行后关闭 MCP 会话和子进程。该切片仅覆盖 DuckDuckGo MCP 的只读搜索/提取，不代表任意 MCP server 的动态工具目录或多租户凭据隔离已经完成。

实现：

1. 新增具体 MCP client 模块，负责所选传输的启动、初始化、发现、调用和关闭；Service 关闭时收束连接与子进程。协议操作交给选定 SDK。
2. 配置只包含 server ID、连接参数、启用工具与凭据引用。连接失败显示独立诊断；必需工具不可用时明确阻塞，不能偷偷切成模型自由回答。
3. ToolRegistry 支持同一个工具描述契约：稳定 ID、说明、输入 schema、调用入口、来源。内建 callable 与外部 schema 均迁移到该契约；不用伪造 Python 函数签名丢弃外部参数约束。
4. 使用 server ID 与原始工具名组成唯一身份；展示名与实际调用名保留映射。同名工具不得覆盖。首版仅启用少量配置工具。
5. 外部连接、凭据和可变工具集合由应用/工作区作用域持有，通过现有上下文传给调用者，不写入全局共享 registry。内建静态定义可共享，账号态不可共享。
6. 更新 Compiler 工具选择、Executor 调用与 product_context，使模型看到的能力来自当前可用工具目录，而不是固定内建工具清单。

主要修改：新增 agent/mcp/ 下的连接与配置代码；registry/tool_registry.py、compiler/tool_selector.py、executor/plan_executor.py、service/context_factory.py、service/service.py、product_context.py。文件拆分按实际职责决定，不先搭插件框架。

离线验收：本地受控 server 从 Service 入口完成发现和调用，覆盖列表分页、参数 schema、同名隔离、工具错误、连接失败、关闭清理。再跑读取合成资料到本地报告的完整链路，核对源内容、文件字节和最终路径。

## 5. 批次 C：外部效果与调用边界（P0，开放外部写入前完成）

实现：

1. MCP 工具执行复用授权层。外部对象与操作范围显式绑定；不能仅凭服务端描述或 annotations 自动授予写权限。
2. 保存调用 ID、server/tool 身份、脱敏参数、结果、错误与时间；保留结构化结果、来源引用和外部对象 ID。凭据不得进入 prompt、普通日志或报告。
3. 区分“服务端报告成功”与“用户目标已核验”。远程资源链接不是本地文件 Artifact；对选定服务实现必要的回读/查询核验，无核验能力时如实标为未确认。
4. 超时或断连后的写入效果可能未知。保留未知状态及已知部分效果，不宣称未执行，也不自动重试非幂等写入。先查询对账，再决定能否继续。
5. 取消意味着停止后续调度，不意味着撤销远端效果；Run 终态与外部效果状态分别记录，供后续对账。
6. 工具描述、返回值、memory、repository 都作为带来源的非可信资料进入上下文。授权不能依赖提示词隔离；执行前仍按当前目标范围检查。
7. stdio server 进程的真实权限独立检查，不能把 workspace 参数当作操作系统沙箱。

主要修改：effect_authorization.py、execution_errors.py、executor、effect_truth.py、goal.py、runtime_store、service/runtime_launcher.py、context_builder.py。

离线验收：读取结果携带恶意指令不能扩大效果授权；错误结果不能完成任务；模拟“远端写入后响应丢失”不得重复写；取消后保留可对账事实；跨 workspace 的工具和凭据不能互见。

真实验收：使用固定版本 server、测试账号与合成数据，执行查找、授权写入、回读、超时对账与取消检查。项目本轮不执行真实外部测试；由独立测试环境提供对象 ID、前后内容和 Run 证据。

## 6. 后续仍应修复的事项

| 顺序 | 工作 | 最小完成标准 |
| --- | --- | --- |
| P1-1 | Session 与长期记忆分离 | 同 user 两个 session 不读取彼此原始历史；只有有来源的已授权 learned facts 跨 session |
| P1-1 | Summary 来源治理 | 自动用户历史归档已排除 assistant 陈述；仍需评估真实模型对历史原话的指令混淆 |
| P1-2 | Repository workspace 所有权 | 已由 ApplicationContext 按根目录持有并通过 RunContext 注入，离线隔离测试已补 |
| P1-2 | 上下文失败可见 | Workspace contract 和必需 repository grounding 已 fail fast，可选 memory resolution 写 Run diagnostics；仍需入口级展示验证 |
| P1-3 | 重启续接与取消后的部分效果 | 从持久化事实恢复当前目标/产物/待办；明确 re-launch 与 checkpoint resume 区别；不重复不确定写入 |
| P2 | Research 综合与产品体验 | 多源归并、时间与来源展示；CLI 显示工具目标、真实结果与未完成项 |

非可信上下文边界属于批次 C，不能等上述记忆完善后才处理。MCP 作用域隔离属于批次 B，不能等全局 repository 重构完成后才处理。

## 7. 落地与交付顺序

1. 先用失败用例固定批次 A 的根因，完成契约迁移；单独提交可审查补丁时不夹带已有工作区修改。
2. 接入一个受控 MCP server，完成 B；工具目录与作用域改造只支持当前两类真实调用者，不预设插件扩展体系。
3. 完成 C 后才开放外部写入；再按实际使用暴露的问题推进 P1。
4. 每批更新 contracts/runtime/quality 文档，记录离线结果与独立真实验收需求。历史报告不覆盖，测试产物继续只留本地。

首个服务已确定为 DuckDuckGo MCP。基础离线测试使用受控 MCP 测试替身；项目本轮不运行真实网络搜索，实际连通性和 DuckDuckGo 限流/反爬行为仍需在用户环境验证。
