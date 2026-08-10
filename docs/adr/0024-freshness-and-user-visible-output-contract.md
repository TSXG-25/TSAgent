# ADR-0024: Freshness Grounding and User-visible Run Output（v2.3H3）

- 状态: Accepted — Implemented and Integration Verified
- Real-provider capability evidence: Deferred（Provider unavailable）
- 日期: 2026-08-10
- 关联: ADR-0021（AgentService/Event Stream）、ADR-0022（P2 Acceptance）、ADR-0023（Cancellation/Timeout）
- Dataset: `tests/test_h3_output_freshness.py`

## 一、范围

H3 只收口两类 Runtime 事实，不扩展 Cancellation 或 Planner 能力：

1. 时效性请求必须由可核验的外部来源接地；
2. 需要回答用户的 Run 必须有真实的 user-visible output 才能完成。

## 二、Freshness Gate

跨领域的时间限定词（例如“今天”“最新”“当前”、显式日期）与动态主题
（例如新闻、天气、股票、价格、政策、进展）组合时，Runtime 设置：

```text
freshness_required = true
source_grounding_required = true
```

只有成功的 `web_search`、`web_news_search`、`web_deep_search` 或 `web_fetch`
观察结果，且包含非空来源摘要，才能满足 `fresh_evidence`。缺少工具、工具失败或
没有来源时，Run 必须进入 `BLOCKED / RESEARCH_TOOL_UNAVAILABLE`，不得使用模型记忆
生成当前事实，也不得产生 `run_completed`。

静态知识解释不触发该 Gate。若 Provider 本身不可用，Run 必须以稳定的 Provider
失败/阻塞事实结束，不得把礼貌性的 fallback 文本当作成功证据。

## 三、Required Output Gate

公开回答型 Run 默认声明 `answer_required = true`。完成条件必须同时满足：

```text
verified effects / artifacts
+ required freshness evidence（如适用）
+ non-empty user-visible output
→ COMPLETED
```

缺少最终回答时使用 `FAILED_TERMINAL / MISSING_USER_OUTPUT`，不得写入
`run_completed`。

最终回答由 Runtime evidence 决定，Finalizer 只能表达已验证事实，不能自行提升终态。

## 四、Durable RunOutput 与“输出呢”

每个终态 Run 的非空用户可见文本可作为同一 durable transaction 中的 `RunOutput`：

```text
run_id
revision
text
evidence_ids
artifact_ids
created_at
```

“输出呢”“结果呢”等确定性请求不进入 Planner、LLM 或 Tool，而是在同一
`tenant_id + session_id` 范围内查找最近的 Run：

- 成功 Run：返回其 durable `RunOutput`；
- FAILED/BLOCKED/CANCELLED/TIMED_OUT Run：返回已有状态/失败摘要，并保持当前请求为非完成状态；
- 无输出：返回 `MISSING_PREVIOUS_OUTPUT`，不得静默重新生成；
- 其他 Session 或 Tenant 的 Run：不可见。

## 五、验证状态

离线 H3 回归覆盖 H301–H308 及 Provider fallback 边界；真实 smoke 的能力结果受当前
DeepSeek/Ollama/Web 网络不可达影响，按 Provider Error/Deferred 记录，不冒充能力通过。
