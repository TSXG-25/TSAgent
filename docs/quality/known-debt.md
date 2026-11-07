# Known Debt

以下条目均来自当前代码、静态调用搜索或已有文档与实现的可复核差异；它们不是本次审计的修复项。

## 1. Resume 入口已接 checkpoint；终态/并发边界仍需验收

AgentService 重启恢复现在从 durable Run index 进入 `RunResumeCoordinator` 和 `WorkflowExecutor`，不会重新构造 UniversalAgent 或把原请求交给 Planner。Workflow checkpoint/index/artifact 先原子提交；只有 Service 完成终态证据与 RunOutput 投影后才提交唯一的 Run 终态。离线 E2E 覆盖 Service 重建、恢复同一 active Workflow、持久化输入绑定及单次 stage 执行。

剩余边界：`FAILED_TERMINAL` 不可恢复；`BLOCKED` 可由 `run_resumed` 开始下一次尝试。同一 tenant/session 的新 Run 与恢复现在在 SQLite 写事务边界检查活跃 Run/worker lease，并 fail-fast 返回 `RUN_ALREADY_ACTIVE`（不排队）；终态释放 lease 后可顺序启动下一 Run。checkpoint `RESUME_EXACT` 有意保留原 Workflow 输入；同 session 的普通目标纠正作为新 Run 已有 AgentService 端到端覆盖，持久化 `goal_id`/`goal_version` 与新禁止效果。Checkpoint ResumeValidator 现要求外部观测精确绑定资源 ID、Guard 类型、预期值及新鲜时间戳；Service `ResumeRunRequest` 已可传递此类通用 Guard 观测并经过恢复 E2E 验证。遇到未知副作用时，`ResumeRunRequest.no_effect_confirmation` 必须绑定唯一 active task、idempotency key 和 external reference；有效的精确恢复确认会作为 `ATTESTED` evidence 持久化，`WAITING_USER` 可经 Service restart 后继续。该确认是调用方 attestation，不会自动查询或证明真实外部状态；多个未决效果、错误绑定、过期证据和已提交效果仍禁止续跑。Service 已通过 SQLite worker lease/heartbeat 阻止未过期租约被第二个 Service 实例接管，并对 lease 丢失时的 Runtime task 执行取消；离线覆盖两个独立 Store/Service 连接及租约过期接管。SQLite lease 不能证明被暂停的进程或其外部在途调用已经停止，目标服务仍需幂等键/自身 fencing 才能保证外部效果不重复；OS 级暂停/终止与真实外部效果尚未测试。未提交 Run index 的请求不能伪装成 checkpoint 恢复；真实 Provider/Workflow 恢复未在本地验证。详见 [ADR-0037](../adr/0037-service-worker-lease-for-resume.md)。

## 2. 重规划覆盖局部交付验收修复；一般错误仍受限

Runtime 允许只读失败，以及携带明确无副作用事实的有限失败进入有界 REPLAN。首个工具步骤的 schema 参数验证失败和执行环境在调用前拒绝会标记 `side_effect_state=NONE`；同类失败若发生在计划后续步骤，效果记为 `UNKNOWN`。未知效果既不会重规划，也不会原样重放动作。Workflow checkpoint 在效果证据缺失时同样记录 `UNKNOWN`，不再把失败推断为提交前失败。

在上述基础上，Runtime 现可针对明确的文件交付数量/验收缺口触发现有有界 REPLAN，但范围限于所有任务已成功或跳过、没有未决/未知所需效果、缺口全是交付或验收证据，且仅规划 read/search/fetch/list/explain/write/modify。重规划继续受目标版本、效果授权、进展指纹和 goal-round budget 约束；已验证副作用保留，文件按当前验收证据修订。Service 入口离线回归覆盖先写出不符合文本验收条件的文件、根据验证结果修改该文件、再次验收通过。字面写入快速路径也只在明确的“内容为/内容是/内容:”分隔符后识别正文，避免把验收条件误写入正文。

一般工具错误仍受错误码白名单限制；命令执行、外部副作用、未知效果或含其它缺口的失败不会走此交付修复分支。真实 Provider 是否能稳定给出安全有效修订、DuckDuckGo MCP 是否可真实联网，仍需独立验证。DuckDuckGo 工具的“已注册/运行可用”差异已由 ToolAvailability 修复，并有 Service 入口离线证据。

## 3. Summary provenance 与非可信上下文的真实模型评估

离线实现现按 tenant/session 保存用户历史原文归档，排除 assistant 陈述；Intent、Planner、直接回答、Runtime selectors 和 ContextService 将动态上下文放在用户级消息，并由系统提示声明信任边界。显式获准的 user facts/resolutions 使用 tenant/user 命名空间。

尚未运行真实 Provider 的 prompt-injection 评估，不能把消息角色隔离表述为对模型的安全保证；执行授权仍由确定性 Runtime 约束。

## 4. 记忆重置仍需入口级验收

MemoryRuntime.reset 已可限定 facts namespace 与 scope；当前离线回归覆盖同用户跨 session 的原始对话隔离与 user facts 共享。真实 Service reset API 的用户可见语义仍需入口级验收。

## 5. 两个同名 ConversationState

agent/conversation/state.py 的 ConversationState 是 session 对话状态；agent/cognition/cognitive_context.py 的 ConversationState 是 resolution timeline cache。两者语义不同但名称相同，影响是维护者容易把 intent cache 当成 recent_goal/last_answer 的权威。

## 6. Direct API 绕过 Service

TSAgent 和 SessionRuntime 仍可直接构造 UniversalAgent。它们不会自动获得 AgentService 的 reservation、公共 event stream、projector 和 Service resume 语义。影响是“Runtime 可用”与“Service 可恢复”不是同一保证。

## 7. Desktop local host 不在仓库内

Desktop clientFactory 默认 mock；local mode 需要注入 TauriSidecarBridge。仓库没有 Tauri/Rust host，真实桌面进程生命周期、身份注入和 sidecar 协作仍需外部验证。

前端自身的剩余技术债（UI 自动化测试缺失、Mock 剧本视图格式、local Run 列表、样式叠加等）见 [apps/desktop/TECHNICAL_MANUAL.md](../../apps/desktop/TECHNICAL_MANUAL.md#9-已知技术债未处理)。

## 8. 历史文档状态滞后

ADR-0023 的开头仍保留早期“Service cancel 未实现”的时间层次，当前代码已经有 AgentService.cancel_run、timeout watchdog 和 convergence。ADR-0018 对 resume integration 的表述也比当前主路径更强。应通过新 ADR 或明确 revision 记录校正，不在本次审计中直接重写历史决策。

## 处理优先级

2026-09-08 补充：新一轮人工测试触发了[用户目标跨模块契约审计](../exec-plans/goal-contract-audit.md)。2026-09-27 离线实现已覆盖用户历史归档来源、提示信任边界、workspace-scoped repository index 及 ContextBuilder 静默退化，并接通 Service checkpoint resume；跨进程并发仲裁、Direct API/Service 差异和完整入口级验收仍开放。下述原审计优先级保留为历史记录；当前修复顺序以该执行计划为准。

最高优先级是确定 resume 正式语义并补主路径验收；其次是 memory scope/reset contract；之后再处理同名状态和 Desktop host 集成。上述条目在没有明确需求和验收前不应通过兼容分支或静默 fallback 处理。
