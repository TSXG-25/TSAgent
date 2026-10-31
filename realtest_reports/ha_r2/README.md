# HA-R2：人工记录复核、跨模块审计与第一批修复

日期：2026-09-08。基准：当前本地工作区，包含上一轮和用户已有未提交修改；不把 GitHub 分支冒充本地状态。整体结论：**Human Acceptance 仍未通过**。本轮修复已确认的目标链路断点，未将所有审计风险标为已解决。

## 人工测试事实

来源为用户提供的 CLI 记录和只读 SQLite 数据库 `/private/tmp/tsagent-human-20260908-195911.sqlite`。`human-transcript.txt` 保存原记录；`human-facts.json` 保存九个 Run 的终态事实及文件检查结果。

| Run（缩写） | 任务 | 观察与归因 |
| --- | --- | --- |
| 38ed7e2f | 你好你知道我是谁吗 | 有文本，COMPLETED；缺少用户身份真值，不能据此断言跨会话记忆通过或失败 |
| b5ee817b | 先写个冒泡排序看看 | 文本代码，无真实执行证据。此措辞可能是代码示例请求，不自动判文件缺失失败；“输出结果为”未清楚区分预期与实跑，需继续审计最终声明 |
| 193202a0 | 汇率转换网站放工作区 | 文件当前存在、6841 字节、路径与回答一致；只证明文件交付，未进行浏览器功能或实时汇率验证 |
| 4bc7a5bd | 最近新闻 | 搜索已完成，总结时 Provider 超时；错误包装成 RUNTIME_EXCEPTION，且最终输出未披露已检索部分。本轮修复分类及部分完成说明 |
| 1c100212 | 写入、读取并告诉内容 | 文件当前为 18 字节；读取结果确为 TSAgent human test。解释步骤实际输出历史 Run 汇总，最终又只给路径。整体 FAIL，本轮修复输入与交付丢失 |
| a6f4eae1 | 追加第二行且不覆盖 | 语义目标正确，包含“不得覆盖或删除”；快捷路径却产生 delete。授权层拦住，未发生删除，仍是任务 FAIL。本轮修复快捷路径与目标路径授权 |
| d1e1a7d1 | 创建 fibonacci.py 并实际验证 | `.py` 执行快捷意图绕过完整语义，缺少创建授权；未执行任务即 BLOCKED，本轮修复 |
| 1c8ff9fe | 修改刚才 fibonacci.py 并测试 | 同类路径/授权问题；当前文件不存在。不能把未创建文件描述成已存在产物 |
| 86d64927 | 5 | SQLite 保存的原始请求是 **5**。不能把它当成日志交错出现的“搜索 Python 最新稳定版”请求。后者没有本记录可对应的 Run ID，状态为未确认提交 |

## 这批代码修复

1. 删除“命名 Python 路径 + CODE_EXECUTION”即跳过完整语义的路径，保留创建/修改/执行的组合要求。语义 mutation target 只有匹配原始请求的明确路径才可绑定授权；仍不能通过改写扩大范围。
2. 删除/复制/移动快捷路径只接受完整独立字面操作。复杂请求进入现有通用 Planner，不再搜索句中任意位置的动作词。
3. ExecutionStage 向推理任务传递当前依赖结果，LLMExecutor 在历史数据之后明确当前任务；最终产物路径答案同时保留终端读取/解释结果。
4. LLMRouter 失败携带 ProviderError 类别与原始 cause，Runtime 保留 PROVIDER_TIMEOUT 等分类；失败答案披露已完成步骤和已验证文件。

对应契约：[ADR-0034](../../docs/adr/0034-goal-pipeline-human-regressions.md)。全部修改仍在本地，未提交或推送。

## 八个审计方向

详表和修复队列在 [goal-contract-audit.md](../../docs/exec-plans/goal-contract-audit.md)。核心结论：

- **语义产物要求**：本地已有 ADR-0033 的 artifact_required，不再仅靠 `_WSC`。本轮发现另一条绕过它的快捷路径并修复。35 组受控语义 family 测试通过不等于真实模型不会漏判。
- **Artifact**：execute 生成的真实 CSV 未被 Runtime artifact projection 发布的探针成立；实际路径与 Task target 仍有两个来源。未修复。
- **重启续接**：最近四个终态 Run 的 SQLite 投影及 Service 重建续接已有离线回归；ConversationTracker 本身未持久化。不能混称“全部会话状态 durable”。
- **Session memory**：ServiceContextFactory 创建两个同用户 session，原始 session 和短期历史确实互通。确认缺陷，未修复；新 DB 不会自动清空 user memory。
- **Summary**：11 轮真实压缩调度、受控摘要响应和生产 policy 证明助手错误陈述可获 STORE；临时位置/汇率文本也可获 STORE。测试拦住实际 persistence，没有向用户长期记忆写入这些数据。确认授权风险，未修复。
- **Prompt trust**：确认 memory/repository 内容进入高权限提示构造；没有执行恶意动作，也没有声称真实模型利用成功。未修复。
- **静默退化**：工作区 current_context 抛 TypeError 后，ContextBuilder 返回空上下文且无 diagnostics。确认缺陷，未修复。
- **Repository scope**：全局 indexer 返回合成 repo B 数据给无 workspace 身份的 repo A 查询。确认作用域缺陷，未修复。

这些探针的结果是 `RISK_CONFIRMED`，**不是 PASS**。`audit-probes.json` 记录边界与实际观察，`audit_probes.py` 可在隔离临时目录复现。假的 secret 在短期存储中被脱敏这一项单独通过，不代表所有 memory 数据路径均已安全。

## Watchlist 复核

| 项目 | 当前证据与边界 |
| --- | --- |
| 同 session 并发 Run | SessionContext 允许多个 Run，同时保留单个 current_run_id；未做并发交错的主路径证明 |
| Cancel 后学习 | MemoryLearningProvider 使用可中断等待，RunInterruptionRequested 是 BaseException 并被显式向上传递；不能仅凭 post-answer 位置断言一定继续学习。但取消与 persistence commit 的竞态仍需专门测试 |
| fire-and-forget summary | create_task 无 Service drain/持久任务接管的证据；探针等待任务完成，不证明 CLI 立刻退出也可靠 |
| Windows namespace | tenant:user 被直接用于短期 JSON 文件名，冒号跨平台风险仍在；未做 Windows 实跑 |
| 默认 workspace | main.py 使用项目目录作为 workspace；测试未改变此默认值 |
| 产品身份/能力 | 本地 product_context 已从 registry 投影 TSAgent 身份与能力；不应把公开旧代码中的缺失判为本地仍缺失。模型遵守程度待人工验收 |
| Research | 有搜索后 synthesis 提示，包含来源、去重、排序要求；没有证明具备完整多源深度研究。新记录失败在 Provider，总结质量无法据此评价 |

额外观察：人测 Profile 的各项耗时相加超过 WALL TOTAL，计时区间可能重叠；仍为性能观测债务，本轮不据此计算占比或优化结论。

## 离线复跑与证据

```bash
cd /Users/tsxg/Desktop/TSAgent
.venv/bin/python realtest_reports/ha_r2/run_offline.py
PYTHONPATH=. HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
  .venv/bin/python realtest_reports/ha_r2/audit_probes.py
```

- `offline-tests.log` / `offline-results.xml`：完整结果，网络 guard 阻止真实外部请求；模型/search 为脚本化边界，文件、Service、SQLite、Compiler、Executor、verifier 和 Python 子进程正常运行。
- 最终结果：**341 passed**，无失败；这不包含下述已确认风险的修复验收。
- 新测试 `tests/test_ha_r2_goal_pipeline.py`：45 项（其中 35 项为语义 family），包含真实临时工作区内的 Fibonacci 运行输出 `FIBONACCI-VERIFIED 0 5`。
- `mypy.log`：按项目 mypy.ini 的检查口径（follow_imports=skip），28 个生产文件通过。
- `mypy-follow-imports-silent.log`：额外扩大导入检查后暴露的类型问题；与项目当前 gate 不同，未作基线隔离，不能据项目 gate 通过宣称全仓类型无问题。
- 新纳入的旧测试迁移到了当前语义契约和完整 context fixture；Provider fallback 单测显式设置 auto，仅使用假 Provider，不改变人工启动器的显式 Provider 策略。

## 下一轮人工验证

新启动仍可运行：

```bash
cd /Users/tsxg/Desktop/TSAgent
bash realtest_reports/ha_r1/start_human.sh
```

先复测原样失败的写入/读取、追加保留、Fibonacci 创建并实际运行，再做 hello.py → 加 main → 重启后继续修改。**重启续接必须保留同一 DB、tenant、user、session**；不要再次运行自动创建新 DB 的启动器来验证重启连续性。直接运行 main.py，并把 --db 指向首次启动打印的数据库路径。

逐条保留 Run ID、最终状态、实际路径、文件字节/内容、真实 stdout 与外部来源。纯回答不要求工具调用；有操作要求才必须核对执行证据。搜索失败时区分 Provider、搜索能力与实际研究质量。尚未修复的 memory/repository/trust 项仍应判未关闭，不能因为这批代码回归通过就宣称整体通过。
