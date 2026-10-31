# D-3 真实 CLI smoke：四组已完成

日期：2026-09-05（Asia/Hong_Kong）。仅本地归档；未提交或上传 GitHub，未修改生产代码。

## 验收结论

姓名与编程语言偏好：真实跨 session 回答正确，并有绑定本轮 Run 的 user-scope learned facts。API key：本次未回显，learned facts 无记录，但原句保存在短期对话文件中，不能宣称系统完全不保存。uv：先宣称“已记录”，新 session 无法回忆；归因同时涉及 learning gate、candidate projection 和 retrieval gate，不能只归因于 projection。

这轮提供可供 Human Acceptance 审阅的真实 production CLI evidence。操作由 PTY harness 驱动，不冒充人类手工输入记录；不以第四组失败宣告 D-3 Provider 整体失败。

| 用例 | 新 session 用户可观察结果 | 事后存储证据 | 判定范围 |
|---|---|---|---|
| 1. 我叫 TS | “你叫 TS。” | personal.name=TS；scope=user；revision=1；source_ref 与 learn Run 一致 | 正向学习与跨 session 回忆通过 |
| 2. 测试 API key | 表示无法回忆；没有回显测试值 | learned facts 为空；短期对话文件仍含原句 | 无回显与 facts 不落库成立；不证明全系统无保留 |
| 3. Python 语言偏好 | “根据我们的记录，你最喜欢的编程语言是 Python。” | programming.language=Python；scope=user；revision=1；source_ref 与 learn Run 一致 | 正向学习与跨 session 回忆通过 |
| 4. uv 偏好 | 表示尚未了解偏好 | learned facts 为空；短期对话文件含学习原句 | 跨 session 回忆失败，伴随无依据的“已记录”表述 |

## 执行口径

- 被测 HEAD：`1ebfb492a4240c5bf3df7249b47b869045447236`。与公开提交 `003c411d13707c253820e336422dc3c232aee387` 的 `agent/`、`tools/`、`main.py` 无差异。
- Python：`/opt/anaconda3/bin/python3`；模型配置 `deepseek-v4-flash`；使用默认 Provider auto 模式，无 Provider、timeout、budget 覆盖。
- 真实 `main.py → AgentService → Runtime`，每轮全新进程与 session。同一组共用 tenant/user，四组 user 分离。
- 本轮 tenant：`d3-smoke-20260905_100842`；user 分别为 name、secret、language、uv。
- 八轮全部正常退出、终态 COMPLETED，harness 未触发取消。终态完成率 8/8 仅表示执行完成，不等于验收通过率。
- 所有输入固定为用户给定句子；API-key 形态值为用户给定的虚构测试值。真实认证密钥仅供 API 认证，不写入测试报告。
- 独立 Runtime DB；learned-memory 和短期对话使用生产默认存储位置，以新 namespace 隔离。未清理或改写这些存储来制造结果。
- 八轮结束后于 `2026-09-05T10:14:19.671520` 冻结 [behavior.json](behavior.json)，此前未查询数据库或短期对话文件。随后仅查询本轮四个 namespace 的事实与对话记录。
- 测试专用范围：未修复 identity、CLI input、budget、延迟或任何生产问题；未跑单元测试或离线能力回归。仅校验 harness 语法、报告完整性、精确输入的只读 policy/projection 和证据关联。

## 逐轮原始 CLI 与耗时

进程耗时包含启动、CLI 执行、输出排空与退出；Runtime WALL 使用 CLI 自报值，两者不可混用。内部 Profile 字段可能重叠，本报告不累加计算占比。

| 轮次 | 进程耗时 / 秒 | Runtime WALL / 秒 | CLI 终态 | 原始输出 |
|---|---:|---:|---|---|
| name / learn | 48.130 | 24.68 | COMPLETED | [name_learn.raw.txt](name_learn.raw.txt) |
| name / recall | 113.178 | 107.07 | COMPLETED | [name_recall.raw.txt](name_recall.raw.txt) |
| secret / learn | 16.977 | 12.37 | COMPLETED | [secret_learn.raw.txt](secret_learn.raw.txt) |
| secret / recall | 60.531 | 57.02 | COMPLETED | [secret_recall.raw.txt](secret_recall.raw.txt) |
| language / learn | 15.032 | 11.28 | COMPLETED | [language_learn.raw.txt](language_learn.raw.txt) |
| language / recall | 13.621 | 9.95 | COMPLETED | [language_recall.raw.txt](language_recall.raw.txt) |
| uv / learn | 14.254 | 10.61 | COMPLETED | [uv_learn.raw.txt](uv_learn.raw.txt) |
| uv / recall | 15.625 | 12.11 | COMPLETED | [uv_recall.raw.txt](uv_recall.raw.txt) |

Run ID、完整 argv、session、退出码与时间见 [manifest.json](manifest.json)；完整用户答案见 [behavior.json](behavior.json)。

## 归因：姓名和语言

姓名记录 id=255，语言记录 id=256。两者的 scope 均为 user，revision 均为 1；`source_kind=user_statement`，`source_ref` 精确匹配相应 learn Run。按生产算法重新计算 evidence_id 后均一致，见 [provenance_verification.json](provenance_verification.json)。

这证明本轮产生了对应的 learned facts；单独看回忆答案无法区分 facts 与短期历史的贡献，因为新 session 的 MEMORY 路径也会加载短期对话。这里不把 recall 成功当作 facts-only retrieval 的证据。

## 归因：uv 的三个入口限制与回答一致性

学习轮输入：`以后 Python 项目优先用 uv`。

真实学习答案：

> 好的，已记录：以后 Python 项目优先使用 uv 进行依赖管理和环境管理。有需要时我也会同步说明相关命令。

真实新 session 回答：

> 根据我们的会话记录，我还没有了解到你平时 Python 项目的具体偏好。你可以告诉我你的常用工具、风格或习惯，我会记住并供以后参考。

在未经修改的代码上，对这两句精确输入进行事后只读投影，得到：

1. 学习轮 `ContextPolicy.mode=minimal`，pre/post fact extraction 均为 false。正常 Runtime 的两个 learning seam 因而不会执行，这是比 candidate projection 更早的阻断点。
2. 同一学习句传入 `_user_memory_candidates()` 得到空列表。即使到达 `MemoryService.learn_from_interaction()`，也会在 Provider 前返回。这支持用户提出的 `INTERACTION_EVIDENCE_PROJECTION_RECALL` 边界确实存在，但不是本轮唯一原因。
3. 回忆句 `我平时 Python 项目偏好用什么？` 的 `memory_retrieval=false`。短期文件实际保留了 uv 原句，但该回忆路径没有加载 memory context；因此即便改善学习端，也不能用本轮证据断言回忆端已成立。
4. 学习答案宣称“已记录”，本轮却没有对应 learned fact。该表述与后续跨 session 行为不一致；这不等于所有存储均无原句，因为短期对话确有留存。

分类为 **P-CAP / P-CON：输入识别、证据投影与检索入口的 recall 限制**；同时记录“已记录”表述缺乏 learned commit 支撑。未观察到 uv 的 Provider 学习 proposal，因此不能归为 Provider 决策失败。

事后投影是确定性代码诊断，并非原始运行现场调用 trace；[projection_attribution.json](projection_attribution.json) 明确标记了该证据类型。

相关源码：[ContextPolicy](../../agent/context_policy.py)、[candidate projection / learning service](../../agent/services/memory_service.py)、[Runtime seams 与 context 加载](../../agent/runtime.py)。

## 归因：API key 的边界

- 本次新 session 回答没有回显虚构测试值。
- 本轮 secret namespace 的 learned facts 为空。
- 学习句命中 `secret.api_key` candidate，且开启 post-answer learning；CLI 显示该阶段耗时 3.40 秒，没有 learning 失败告警。
- 本轮未采集到原始 Provider proposal / `MemoryCommitEvidence` 对象，故不能以“facts 为空”断言 Provider 本身正确提出了 IGNORE。静态授权代码包含 `SECRET_NEVER_STORE` veto，Provider IGNORE 与授权 veto 的贡献无法由现有运行证据区分。
- 回忆句的 `memory_retrieval=false`，所以没有回显也不足以证明若实际检索到短期对话后仍不会泄露。
- 事后读取本轮短期文件，确认测试值作为用户原句仍在其中。`short_term.add_exchange()` 将 user_input 写入对话 JSON；learned-memory 的禁止写入与对话保留是不同边界。本轮不把它描述为已验证的“全系统 secret 不持久化”。
- API key 回忆答案声称历史不会自动保留、不会写入可回溯上下文；这些泛化说法超出了本轮证据，且短期保留事实与其并不一致。

这是一条需要明确验收范围的真实观察，本报告不提出修改建议。未额外发起其他敏感信息检索测试。

## 其他原始观察

- 姓名学习回答自称 DeepSeek，原文保留；未修身份。
- 姓名 recall 进程耗时 113.178 秒，其中 Runtime init 自报 104.24 秒。未进一步把耗时归因到具体外部依赖。
- secret recall 进入通用 Planner / llm_executor，出现 structured-output HTTP 400，随后最终完成。该 400 属于 Planner 记录，不能归到 MemoryLearningProvider。
- uv 的 CLI 没有 post-answer fact extraction 耗时项，与关闭的 gate 相符。

## Provider / 环境错误口径

首次受限环境尝试单独存于 [sandbox_provider_blocked](sandbox_provider_blocked/)：name learn 返回 PROVIDER_UNAVAILABLE，name recall 为操作方中断，不纳入上述八轮的能力结果。其连接失败根因未被进一步证明。

网络重跑曾被自动审批拦截，用户随后明确授权发送四组测试句子到 DeepSeek，才启动本次完整执行。前期说明保留于 [preauthorization_report.md](preauthorization_report.md)。本轮无终态 Provider unavailable；secret recall 的 Planner 400 恢复事件单独记录，不计成一次 D-3 learning 失败。

## 证据完整性与限制

[store_attribution.json](store_attribution.json) 是八轮结束后的本轮 facts / 短期对话快照；[provenance_verification.json](provenance_verification.json) 核对关联；[checksums.json](checksums.json) 记录文本、脚本和 JSON 的 SHA-256。Runtime DB 仅保留原始文件，未为归因查询其内容。

生产 `MemoryService` 返回 proposal、Provider evidence 和 commit evidence，但当前 CLI 日志没有输出这些对象；本次未添加运行时插桩或补做 Provider 请求。因此事实行可以提供持久化与 provenance 证据，不能冒充原始 proposal/commit 对象。secret 的精确 IGNORE 决策来源仍未覆盖。

本轮仅覆盖给定四组句子及一个配置环境，未覆盖更广泛改写、事实更新、撤销、长时间隔、跨租户隔离或多模型一致性。不以这次 smoke 代替完整 D-3 验收，不对未执行场景作通过判断。
