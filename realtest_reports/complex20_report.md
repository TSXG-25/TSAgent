# TSAgent 复杂结合意图真实 API 测试报告（20 例）

- 日期：2026-08-07
- Provider：DeepSeek deepseek-v4-flash + DDGS 实时搜索
- 被测代码：当前 master（隔离快照 `/private/tmp/tsagent-complex20`，不污染仓库）
- 框架：`harness/tsagent_harness_complex20.py`，结果 `results/round4_complex20.json`
- 耗时：主跑 18.6 min；定向复跑 P01/P02/C01 另计

## 测试集（复杂结合意图）

| 类别 | 例数 | 示例 |
| --- | --- | --- |
| fin_research 金融研究+实时搜索 | 6 | 推荐些今天的股票 / 最近一周 A 股热门板块 / 加密货币行情 |
| program 编程+数据/金融 | 5 | 写股票分析程序 / 移动平均线 / 基金收益计算器 |
| program_search 编程+搜索 | 4 | 搜索抓取股票数据后写示例 / asyncio 并发下载示例 |
| research 搜索+研究报告落盘 | 3 | 量子计算进展报告 / LLM 动态总结 / Python vs Rust 对比 |
| mixed 搜索+编程+建议 | 2 | 搜索北京天气后写穿衣建议脚本 / 免费股票 API 调用示例 |

## 结果口径

```text
Raw E2E Rate    = PASS / 全部 20
Capability Rate = PASS / (20 - PROVIDER)
Provider Rate   = 网络/超时类 / 全部 20
```

## 主跑汇总（原始口径）

```text
PASS 8 | WARN 10 | BUG 2 | PROVIDER 0
Raw E2E Rate 0.40 | Capability Rate 0.40 | Provider Rate 0.0
```

## 逐例结果（含地面真相复验与定向复跑）

| ID | 类别 | 主跑 | 复验/复跑 | 结论 |
| --- | --- | --- | --- | --- |
| F01 推荐些今天的股票 | fin | WARN | 安全拒答：拒绝投资建议（policy） | 安全护栏，非 Bug |
| F02 A股热门板块 | fin | PASS | 基于搜索结果的板块分析 | ✅ |
| F03 财经新闻 | fin | PASS | 但内容误读：把"提问"当成"用户已提供新闻" | ⚠️ 对话上下文问题 |
| F04 美股表现 | fin | PASS | 长文搜索答案（130.7s，多次 Provider 超时重试） | ✅ |
| F05 加密货币 | fin | WARN | "达到时间/步骤上限，停止重试"（139.8s，Provider 超时） | ⚠️ Provider 波动下放弃 |
| F06 新能源机会 | fin | PASS | 搜索+分析 | ✅ |
| P01 股票分析程序 | program | WARN | 复跑 PASS：`output/stock_analyzer.py` 9081B 语法有效 | ⚠️ 偶发内部错误（见发现 2） |
| P02 移动平均线 | program | BUG | 复跑 PASS：`output/ma.py` 1998B 语法有效 | ⚠️ Provider 超时→步骤上限（flaky） |
| P03 基金收益计算器 | program | PASS | `output/fund_return.py` 814B 有效 | ✅ |
| P04 日收益统计 | program | PASS | `output/return_stats.py` 4352B 有效 | ✅ |
| P05 K 线生成 | program | PASS | `output/kline.py` 5616B 有效 | ✅ |
| C01 搜索后写抓取程序 | prog_search | BUG | **复跑仍 FAIL（2/2）**：文件未生成 | 🔴 真 Bug（见发现 1） |
| C02 async 下载示例 | prog_search | WARN | `output/async_download.py` 1862B 有效；回复简短 | ✅（oracle 过严） |
| C03 数据清洗示例 | prog_search | WARN | `output/data_clean.py` 2697B 有效；无 `def`（脚本式） | ✅（oracle 过严） |
| C04 K 线绘图示例 | prog_search | WARN | `output/kline_plot.py` 2197B 有效；回复简短 | ✅（oracle 过严） |
| R01 量子计算报告 | research | WARN | `output/quantum.md` 781B 有实质内容 | ✅（oracle 过严） |
| R02 LLM 动态总结 | research | WARN | `output/llm_news.md` 3911B | ✅（oracle 过严） |
| R03 Python vs Rust | research | PASS | `output/lang_compare.md` 14032B | ✅ |
| M01 天气穿衣脚本 | mixed | WARN | `output/weather_advice.py` 913B 有效 | ✅（oracle 过严） |
| M02 股票 API 示例 | mixed | WARN | `output/stock_api_demo.py` 3358B 有效 | ✅（oracle 过严） |

## 发现（按价值排序）

### 🔴 发现 1 — C01：搜索→编程结合意图的"假成功 + 执行缺口"（可复现 2/2）

输入「搜索 Python 怎么抓取股票行情数据，然后写一个示例程序保存到 output/fetch_stock.py」：

- 主跑：回复声称「我为你整理了一个示例程序，**并保存到 output/fetch_stock.py 文件里**」，但文件实际不存在 → **假成功（hallucinated success）**。
- 复跑：给出完整代码，但让用户「你把它**保存为** output/fetch_stock.py 即可」→ **把写入动作推给用户**。

这是复杂结合意图下的真实缺口：**搜索部分成功、代码生成部分成功，但"保存到文件"这个 World State Change 没有进入执行链**（与 v2.1A 的 Intent→Execution 边界同类，但在"搜索+编程"组合下仍复发）。Finalizer 的"以文件系统为准"校验没有兜住 C01（可能因为输出里写了"保存到"字样被误判为已保存）。

### ⚠️ 发现 2 — P01：structured-output 400 会偶发转成"内部错误"

日志反复出现 `Structured output 失败，永久关闭: Error code: 400 - response_format unavailable`（DeepSeek 不支持该响应格式）。多数时候自动降级成功，但 P01 主跑时最终回复「遇到了一点问题（内部错误）」，文件未写；复跑则成功（9081B）。属于**偶发稳定性问题**：降级路径里有一支会把 400 变成 agent 内部错误而非继续重试。

### ⚠️ 发现 3 — Provider 超时 → "达到时间/步骤上限"直接放弃（P02 / F05）

DeepSeek 本轮多次 `Request timed out`（F04 花了 130s 靠重试成功；P02/F05 直接放弃）。Agent 在超时后选择停止而不是继续重试/降级。P02 复跑 8.2s 即成功，证明是 Provider 波动下的稳定性问题，不是能力缺失。

### ⚠️ 发现 4 — F01：投资建议安全拒答

「推荐些今天的股票」被拒绝（"我无法提供股票推荐或投资建议…建议咨询持牌财务顾问"）。属于合规/安全护栏行为；但用户明确要推荐，产品语义上需要明确这是安全策略还是应放宽。

### ⚠️ 发现 5 — F03：对话上下文误读

用户只问「今天有哪些值得关注的财经新闻」，agent 却回复「你提到'已检索到的财经新闻'…」，把用户提问当成"用户已提供新闻"，疑似 Conversation Runtime 注入了错误引用（值得查 reference resolver / last_instruction 注入）。

### ℹ️ 观察 — 文件类任务回复过短

7 例文件写入任务只回复「已写入 output/xxx」（C02/C03/C04/R01/R02/M01/M02），文件本身有效。Benchmark 的 output 长度阈值对这些 case 过严；真实质量问题是 UX（无摘要/无验证结果），不是能力失败。

## 文件地面真相

主跑+复跑落盘的 10 个 `.py` 全部通过 `compile()` 语法校验（808B~9KB）；3 个 `.md` 报告有实质内容（781B~14KB）。仅 `fetch_stock.py`（C01）始终未生成。

## 结论与建议

- **1 个可复现真 Bug**：C01 搜索→编程结合意图的文件写入缺口（假成功 + 推给用户），应优先修。
- **2 个偶发稳定性**：structured-output 400 降级路径偶发内部错误；Provider 超时后直接放弃（可加超时重试/降级策略）。
- **1 个对话上下文**：F03 引用误读，建议查 reference resolver。
- **1 个产品策略**：F01 投资建议拒答是否需要保留。
- Benchmark 侧：文件类 case 的 output 长度阈值应放宽，改为"文件存在+语法有效"为主判据；新增"声称已写入 X 但文件不存在"的假成功检测（C01 已在复跑中暴露）。

---

# 附录 A：时间接地（Temporal Grounding）审计（round-4b，判据修订）

> 修订判据（评审意见）：**请求说了"今日/最近/最新"等时间限定词，但 Agent 没有去读
> "今日/最近/最新"的真实外部数据，也计为 Bug**。

实现：在 harness 的 ToolRegistry 层包裹 `web_search/web_deep_search/web_news_search/web_fetch`
四个工具（记录真实调用与查询词，不改变行为），并对含时间限定词的输入增加
`TEMPORAL_NOT_GROUNDED` 检查。干净快照（清空 data/ 与 output/ 残留）重跑 20 例。

## round-4b 汇总

```text
PASS 5 | WARN 10 | BUG 5 | PROVIDER 0
Raw E2E 0.25 | Capability 0.25 | Provider 0.0   （9.3 min）
```

## 5 个时间接地 Bug（均为"说了时间限定词但 web_calls=NONE"）

| ID | 输入（时间词） | 实际行为 | 证据 |
| --- | --- | --- | --- |
| F01 | 推荐些**今天**的股票 | 未调 web，直接拒绝"不掌握实时行情" | trace 无任何 web 工具调用 |
| F03 | **今天**有哪些值得关注的财经新闻 | 未调 web，回"您没有提供具体的新闻条目…"；另一轮甚至谎称"我无法进行实时网络搜索"（实际具备 web_search/web_fetch） | trace 无 web 调用 + 虚假的"无联网能力"声明 |
| R01 | 搜索量子计算**最新**进展 | 未调 web，`quantum.md` 纯模型记忆（无来源/日期/链接） | 文件 1083B 无任何引用 |
| R02 | 搜索大语言模型**最新**动态 | 未调 web，`llm_news.md` 无 URL/报道来源 | 文件 3355B 无引用 |
| M01 | 搜索**今天**北京的天气…写穿衣脚本 | 未调 web，`weather_advice.py` 用 `input()` 让用户输入温度，而非读取今天真实天气 | 文件 1262B 无网络请求 |

这 5 例与 round-4 主跑 + 两轮独立时间审计的结果一致（F01/F03/R01/R02/M01 始终
无 web 调用），结论稳健。其中 F01/F03 属于 `is_fresh_research_request` 已识别为
实时研究意图、但执行层没有把意图落到工具调用上——**Intent → Tool 的执行缺口**。

## 尝试了但被 Provider 拖垮（WARN，非时间接地 Bug）

F02/F04/F05/F06/C01 均实际调用了 `web_fetch`（web_calls={'web_fetch':1}），但随后进入
"内部错误"回复（structured-output 400 路径，本轮高发）。C01 本轮 `fetch_stock.py`
已写入（2373B）却回复"内部错误"——**文件真实存在但回复声称失败（truthfulness bug）**。

## 观察：搜索意图同样存在"说了搜索没去搜"

C02/C03/C04 请求含"搜索"，但 web_calls=NONE（文件已写且有效，但未执行搜索）。
时间接地判据不适用（无时间词），作为"搜索意图未执行"的独立观察项。

## 对本轮结论的修订

在时间接地判据下，round-4 的 BUG 数从 1（C01）上升到 **5（F01/F03/R01/R02/M01）+1（C01 truthfulness/执行缺口）**。
程序类（P01–P05）与文件写入类（C02–C04/R03/M02）在干净环境下均成功且产物有效。
