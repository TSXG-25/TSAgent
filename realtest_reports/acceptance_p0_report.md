# Real API Acceptance Suite v0.1 — P0 报告（24 cases）

- 日期：2026-08-07；Provider：DeepSeek deepseek-v4-flash
- 同时验证两层 API：真实 Provider + TSAgent AgentService（SQLite 事件流 / 终态闭环）
- 框架：`harness/acceptance_p0.py`（每 case 独立 SQLite DB + 工具/LLM/Planner 插桩）
- 结果：`results/acceptance_p0_round1.json`

## 汇总

```text
总 24 例：PASS 20 | FAIL 4 | 通过率 83.3%

组 A 终态正确性      4/4
组 B Planning/路由    4/4
组 C 多目的多 Tool    1/4   <- 本轮主要短板
组 D AgentService/事件 4/4
组 E 恢复/副作用      4/4
组 F Provider/性能    3/4
```

## 分组成绩

### A 终态正确性（4/4）
- A01 读取内部存储路径 data/semantic_memory/chroma.sqlite3 -> FAILED_TERMINAL + run_failed，chroma 未被读取（内部路径被拒，不 COMPLETED）。
- A02 预算耗尽 -> FAILED_TERMINAL，事件流无 run_completed。
- A03 ExecutionVerifier 确定性探针：plan 声称写了不存在的文件 -> verifier 拦截；真实 run 亦不 COMPLETED。
- A04 必需文件缺失 -> FAILED_TERMINAL，不因"有回答"标完成。

### B Planning / Tool 路由（4/4）
- B01 解释快速排序 -> tool_calls=[]（不误调 Tool）。
- B02 快速排序保存 output/q.py -> 文件创建 + 语法有效 + filesystem.write 真实调用。
- B03 搜索本周 AI/半导体 -> web_search 真实调用。
- B04 偏好推荐板块 -> web_search + Memory，未读 chroma。

### C 多目的多 Tool（1/4）
- C01 Memory+Research+Write -> market.md 4107B（带来源），web+write 全走通，PASS。
- C02 多文件合并 -> merged.txt 实际已写入，但最后"统计重复行"LLM 步骤抛 coroutine raised StopIteration -> 回复"内部错误"，FAIL。
- C03 create/run/fix calc.py -> filesystem.write 调了 2 次但 calc.py 未生成，同样命中 StopIteration，FAIL。
- C04 搜索 + summary.md/sources.json 双产物 -> 无任何工具调用，"抱歉，我暂时无法回答"，FAIL。

### D AgentService 与事件流（4/4）
- D01 同 request_id+同内容两次 start_run -> 同一 run_id，run_started=1（Provider 只执行一次）。
- D02 同 request_id+异内容 -> IDEMPOTENCY_CONFLICT。
- D03 读取部分事件后以 after_sequence 重连 -> 无缺失、不重跑。
- D04 Service close 后重建 -> get_run 返回同一 durable COMPLETED snapshot。

### E 恢复与副作用安全（4/4）
- E01 Workflow B 中断后恢复 -> resume 进程中 A(spec) exec=0，B/C 完成，solution.py 存在。
- E02 写文件成功、finalize 前崩溃 -> FAILED_TERMINAL，文件不重复写、Provider runtime 不重复实例化。
- E03 对 COMPLETED Run resume -> ALREADY_COMPLETED。
- E04 错 tenant 查询 -> RUN_NOT_FOUND，不泄漏 owner。

### F Provider / 性能（3/4）
- F01 Provider 请求前超时 -> FAILED_TERMINAL，无 Tool 副作用（Provider Error 单列）。
- F02 durable PREPARED + Provider 超时 -> 重试不创建第二条 intent、不重复不安全操作（runtime 实例 1->1）。
- F03 非法 Planner 输出 -> 有限重试（1 次），FAILED_TERMINAL，不把解析失败当完成。
- F04 简单任务基线（hello.txt）-> 文件成功，但 LLM calls=4（门槛 <=2），FAIL。

## 主要发现

1. HIGH - LLM 步骤 coroutine 内抛 StopIteration（异步 bug）：多目标任务中途崩溃 + "内部错误"回复（C02/C03；C02 文件已写但最后一步崩）。与 complex20 反复出现的"内部错误"同源，位于 DeepSeek response_format=400 降级路径。
2. MEDIUM - Planner 偶发幻觉不存在的工具 copy_file（未注册 -> "未找到工具"）。多轮出现但非每轮，属 LLM 规划不接地到真实工具清单。
3. MEDIUM - 简单单 Tool 任务 LLM=4 次（F04），未达 LLM calls <= 2 门槛。
4. LOW - Provider 异常未在 durable snapshot 留下结构化 failure_code（F01 failure_summary=None，仅事件流可观测）。

## 失败归类

```text
C02/C03/C04 -> RUNTIME_CONTRACT_FAILURE（StopIteration / 无工具执行）
F04        -> PERFORMANCE_REGRESSION（LLM calls 超门槛）
```

## 测试基建说明

- 每 case 独立 SQLite DB，AgentService 用真实 create_default_agent_service。
- 工具调用在 PlanExecutor._exec_tool 层插桩（覆盖 filesystem.write 等映射名），LLM 在 agent.llm.llm.ainvoke 层计数，Planner 在 ExecutionOrchestrator.plan 层计数。
- 曾因 registry 层包装破坏同步工具（.func=None）引入假失败，已移除 registry 包装、改用 executor 层插桩并复跑受影响组。

## 重跑确认（C02/C03/C04/F04）

2026-08-07 复跑这 4 例（真实 DeepSeek，同一 harness），**仍全部失败**，与首轮一致：

| Case | 首轮 | 复跑 | 地面真相 |
| --- | --- | --- | --- |
| C02 多文件合并 | FAIL | FAIL | merged.txt 已创建但 **0 字节（空写入）**；最后 explain 步骤 `coroutine raised StopIteration` → 回复"内部错误" |
| C03 calc.py | FAIL | FAIL | calc.py **未生成**（filesystem.write 调了 2 次但落盘缺失）；同样命中 StopIteration → "内部错误" |
| C04 双产物 | FAIL | FAIL | **无任何工具调用**；回复给出 summary.md 内容却让用户"请将以下两个文件保存至 output/ 目录"（把写入推给用户）；还自述"无法实时检索互联网…基于2025年知识库"（搜索未执行） |
| F04 性能基线 | FAIL | FAIL | hello.txt 成功但 LLM calls=3（门槛 <=2），plan=1 |

结论：**P0 维持 20/24，无法收口 24/24**。4 例失败均为真实能力/运行时问题（非测试框架问题），
需要 Runtime/质量修复后方可复跑收口：

```text
C02/C03 → RUNTIME_CONTRACT_FAILURE（LLM 步骤 StopIteration 异步 bug + 空写入/未落盘）
C04    → MODEL_QUALITY/RUNTIME（搜索+写入意图未执行，把保存推给用户）
F04    → PERFORMANCE_REGRESSION（简单任务 LLM calls=3-4 > 2）
```
