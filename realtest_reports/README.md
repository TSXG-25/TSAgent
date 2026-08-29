# TSAgent 真实 Provider 证据归档（`realtest_reports/`）

本目录只保存经过脱敏的真实 Provider/本地模型测试证据、Harness 说明和冻结报告。它
不是生产 Runtime 的输入，也不是确定性 Dataset 的 Source of Truth。

目录职责：

- `harness/`：真实场景测试框架和采集逻辑；
- `results/`：带 HEAD、Dataset hash、Provider 和时间信息的结果；
- `v2.2/`、`v2.3/`：已冻结的里程碑证据；
- 本地 workspace、用户文件、API key、完整环境配置和临时日志不得归档。

真实 Provider 结果必须与离线回归分开，并区分 Capability Outcome、Runtime Correctness
和 Provider Error。

三轮真实 DeepSeek API 模糊测试的结果与工具，覆盖 2026-08-03 ~ 08-04。

## Round 4：复杂结合意图（2026-08-07）

20 例真实 API 测试，聚焦"编程 + 搜索 + 金融研究 + 报告 + 混合链"等复杂结合意图
（如「推荐些今天的股票」「写个股票分析程序」）。详见 `complex20_report.md`，
结果 `results/round4_complex20.json`，框架 `harness/tsagent_harness_complex20.py`。

主跑：PASS 8 / WARN 10 / BUG 2 / PROVIDER 0（18.6 min）。
定向复跑与文件地面真相复验后：**1 个可复现真 Bug（C01 搜索→编程执行缺口+假成功）**、
2 个偶发稳定性（P01 structured-output 400 内部错误、P02 Provider 超时步骤上限）、
1 个对话上下文误读（F03）、1 个安全拒答策略（F01）。

## 结果数据（results/）

| 文件 | 轮次 | 被测代码 | 规模 | 结果 |
|---|---|---|---|---|
| round1_oldcode_124scenarios.json | 第 1 轮 | 修复前旧代码 | 124 场景 | 38 BUG / 86 PASS，44.5 min |
| round2_fix1_80scenarios.json | 第 2 轮 | 第一阶段修复（写链/安全/稳定性） | 80 聚焦场景 | 23 BUG / 56 PASS，60.1 min |
| round3_fix2_80scenarios.json | 第 3 轮 | 第二阶段修复（路由/降级/真实性） | 80 聚焦场景 | 14 BUG / 64 PASS，41.6 min |

每份 JSON 结构：
```json
{ "summary": {"total","bug","warn","pass","elapsed_min"},
  "results": [ {"id","cat","input","status","problems","warns","dur","out","exc"} ] }
```

## 框架（harness/）

- `tsagent_harness.py`   — 第 1 轮 124 场景框架
- `tsagent_harness2.py`  — 第 2/3 轮可配置框架（断点续跑 + 场景筛选）

用法（示例）：
```bash
TSAGENT_SNAPSHOT=/path/to/snapshot \
TSAGENT_CATS="codegen,codebug,fileop,memory,security,office,chain" \
TSAGENT_RESULTS=/tmp/results.json \
python -B tsagent_harness2.py
```
环境变量：`TSAGENT_SNAPSHOT`（被测代码目录）、`TSAGENT_CATS`/`TSAGENT_IDS`（场景筛选，∪ 关系）、`TSAGENT_RESULTS`（输出路径）。

## 调试日志（logs/）

- `debug_D03_write_plan.log` — D03 写文件场景插桩日志（验证 WriteRule 生成的 plan 步骤）
- `debug_F03_write_plan.log` — F03 写文件场景插桩日志（验证写链修复生效）

## 说明

- 快照目录（tsagent-run/run2/run3/run4，各约 16MB）未归档，避免仓库体积膨胀；需要复现可重新从对应 git 状态创建。
- 每轮聚焦集 = codegen(12) + codebug(10) + fileop(10) + memory(12) + security(8) + office(6) + chain(8) + 抽样控制(14) = 80 场景。
- 注意：第 2 轮曾因框架 SNAPSHOT 指向旧快照而误跑，已修正后重跑，`round2` 文件为修正后结果。
