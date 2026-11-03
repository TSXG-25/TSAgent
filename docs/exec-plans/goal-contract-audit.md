# 用户目标跨模块契约审计

状态：进行中；2026-09-08 完成第一批修复与离线验证，整体人工验收未通过。

2026-09-09：第二批完成 [ADR-0035](../adr/0035-verified-file-effect-publication.md) 的 canonical file evidence 链路。受管文件操作与已声明命令输出不再依赖 Task verb/target 发布 Artifact，失败任务的部分文件得到保留；未声明效果自动发现及其它审计风险仍开放。

## 范围与证据

基准是当前本地工作区（包含已有未提交修改），不是 GitHub 分支。读取了更新后的 architecture、contracts、runtime、quality 文档，以及新一轮 CLI 记录和对应 SQLite 终态事实。用户已有修改不回滚；不上传本地测试产物。

审计主线：原始请求 → 语义目标 → requested outcomes / authorization → Task → actual effect → verified evidence → artifact → continuation → final claim。

## 本轮已处理

- [x] 创建并执行代码：删除“识别到 `.py` + 执行要求就跳过完整语义”的捷径，执行约束继续作为附加事实，创建/修改要求交给完整语义契约。
- [x] 有语义文件变更要求时，将模型目标与原始请求的明确路径对齐后绑定授权；模型改写不能自行扩大路径范围。
- [x] 删除/复制/移动快捷解析只接受完整字面操作；不能从“不得删除原内容”这种约束中提取动作。复合目标进入通用 Planner。
- [x] LLM 解释步骤接收本轮依赖 Task 的实际结果，历史 Run 数据排在当前任务之前。
- [x] 文件交付答案保留终端读取/解释结果，不再只返回路径。
- [x] LLMRouter 的错误携带 Provider 类别到 Runtime/Service；总结阶段失败如实披露已执行步骤。
- [x] 35 组 deliverable family 契约测试；Service 写入/读取/解释、追加保留、真实 Python 执行、搜索后 Provider 超时回归。
- [x] 自动会话归档只保存带时间戳的用户原话，排除助手陈述；会话摘要不再被当作跨会话 user fact。
- [x] Intent、Planner、直接回答、NextAction/Workflow Selector 和 ContextService 的检索数据均位于用户级消息；静态系统提示明确限定信任边界。
- [x] RepositoryIndexer 按 `ApplicationContext + resolved workspace root` 持有；Runtime、ContextBuilder、执行搜索统一使用当前 Run 注入的索引。
- [x] Workspace contract 错误不再被转成空上下文；可选 memory-resolution 失败写入 Run diagnostics；必需 repository 检索无 index 时显式失败。
- [x] 工具注册不再等同于运行可用：ToolRegistry 记录动态 availability/reason，工具选择与 Compiler 排除不可用项；fresh research 按需加载 web 模块并在决策前检查 MCP server。AgentService → Runtime → durable Run 的离线入口测试确认缺少搜索服务时进入 BLOCKED，failure code/detail 和用户输出均保留。

## 八个方向的当前结论

| 方向 | 本地结论 | 未完成工作 |
| --- | --- | --- |
| 语义交付要求 | ADR-0033 已引入语义 artifact_required，非单纯 `_WSC`；本轮又移除代码执行绕过语义的路径 | 真实模型漏判仍可能导致错误完成；受控语义测试不是 NLP 能力验收；其余复合目标快捷路径仍需逐一审计 |
| Artifact evidence | ADR-0035 已统一实际路径/摘要证据，覆盖受管写入、复制/移动、声明产物的命令、部分失败及发布前失效 | 任意脚本的未声明文件未自动发现；并发外部写入的因果归属、取消后的部分文件发布仍需另行审计 |
| 重启续接 | 最近四个终态 Run 的请求、目标、输出、产物与有限 observations 已经通过 SQLite 提供，已有跨 Service 重建回归 | ConversationTracker 本身未恢复；不能承诺完整会话状态/长历史/执行中目标都可恢复；Service re-launch 与 checkpoint resume 仍不同 |
| Session / user memory | 已按 tenant:session 隔离原始消息、short-term 和自动摘要；获准的 learned facts/resolutions 按 tenant:user 共享。Service ContextFactory 离线回归通过 | 既有 user-scope 摘要不迁移/删除且不由新 view 检索；入口级 reset 语义仍需验收 |
| Summary provenance | 用户历史按原文归档，assistant 内容不进入归档；会话作用域阻止跨 session 洗成用户事实 | 本地边界已有回归；仍需评估真实模型是否会把用户原话里的旧指令误认为当前指令 |
| Prompt trust | Intent、Planner、Direct Answer、NextAction/Workflow Selector 和 ContextService 已把动态数据放在用户级消息；执行授权仍由确定性代码约束 | 离线提示结构测试通过；未运行真实模型 prompt-injection 评估 |
| 静默退化 | Workspace contract 失败会向上暴露；可选 memory-resolution enrichment 失败写入 Run diagnostics；必需 repository grounding 无 index 时失败；搜索工具不可用状态在规划前阻塞并持久化 detail | Runtime 启动/Provider 外部错误的入口级展示仍需完整矩阵验证 |
| Repository scope | RepositoryIndexer 由 ApplicationContext 按 workspace root 缓存，RunContext 注入 Runtime/规划/执行 | 同进程双 workspace 和执行注入有离线回归；真实多项目桌面入口仍需独立场景验收 |

## 下一批顺序

1. ADR-0035 已完成受管文件与声明命令产物的证据统一；继续清理丢失复合目标的快捷路径，并审计未声明/取消后的效果边界。
2. session 原始记录、user learned memory 与 assistant-free 用户历史归档已有离线约束；补入口级 reset 与同 session 引用验收。
3. workspace repository 所有权、提示数据边界和上下文失败可见已接线；补从 AgentService/CLI 的完整隔离及真实 Provider 注入评估。
4. 明确并接通 Service resume 的 re-launch 或 checkpoint resume 契约，扩展崩溃/取消后的部分效果对账测试。

## 仍需人工验证

显式 DeepSeek 或 Ollama；沿用本地启动器。依次测试创建 hello.py、给它加 main、保留相同 DB/tenant/user/session 重启后继续修改，再验证写入并读取、追加保留、创建 Python 并实际执行。每条核对 stdout、文件字节、路径、Run ID 和 failure category。只验证模型语义/来源质量时也必须记录 Provider Error，不能归为业务能力失败。

本地 summary provenance 边界、提示数据角色隔离、repository workspace 所有权与上下文失败可见已有实现和离线回归；仍不能宣称整体审计通过，因为真实模型 prompt-injection、AgentService/CLI 全矩阵及 checkpoint/re-launch 恢复契约尚未验收。离线探针使用临时目录、合成数据和受控外部响应，未改用户持久记忆，也未执行恶意文本。
# HA-R4 Gate A 补充审计

Intent 提示已改成交付形式优先，明确无路径的工具/网页/脚本/项目/成份报告要求 artifact。六条受控语义到真实 Service 文件执行的离线测试通过；这不是 Provider 识别准确率。真实分类评测准备在 realtest_reports/ha_r4_gate_a/run_semantic_eval.py，本轮未调用真实 API。

新确认：否定句“只在聊天里展示计算器代码，不创建文件”即使模型正确返回 artifact=false / execution=false，旧词法合并仍添加 FILE_MUTATION 并强制执行。需统一词法约束与语义结果的权威边界；不应继续加否定词特例。产物数量也尚不能由 artifact_required 布尔值表达。整体 Gate A 保持未通过。
