# ADR-0029: Planner Capability Contract（v2.4A）

- 状态: Accepted — v2.4A Contract / Dataset / Oracle Frozen
- 范围: Planner 的目标分解、任务结构和依赖质量评测
- 前置基线: v2.3 Runtime / Service / Desktop 能力已冻结；本 ADR 不改变生产执行路径

## 1. 背景

Runtime 的持久化、恢复、事件、隔离和取消合同已经有独立门禁。下一阶段需要单独测量
Planner 是否把用户目标转换成完整、可执行且不过度拆分的任务计划。Planner 能力结果不能
替代 Runtime 正确性，也不能把 Provider 或模型质量问题伪装成 Runtime 失败。

v2.4A 只建立一个 Provider-independent 的合同、确定性 Dataset 和 Oracle。它不接入新的
Planner 控制循环，不修改 prompt、Tool 选择或执行器。

## 2. 合同

### 2.1 计划模式

每个请求必须被归入一个明确模式：

- `chat`: 不需要执行计划，`tasks=[]` 且不 abstain；
- `plan`: 至少有一个任务，且不 abstain；
- `abstain`: 不猜测缺失目标，`tasks=[]` 且 `abstain=true`。

### 2.2 Goal unit 与 Task

Dataset 使用 `goal_units` 作为期望目标的最小可验证单元。每个 unit 声明：

- 稳定的 `id`；
- 一个或多个允许的 `verbs`；
- `target` 与 `target_type`；
- 是否为 `critical`；
- 只依赖之前声明的 unit。

Planner 输出仍使用项目唯一的 canonical `Task` 模型。Oracle 只检查任务结构、目标覆盖和
依赖，不执行 Tool、不读取 Workspace、不调用 Provider。

恢复类 case 使用 `completed_units` 声明已完成的目标。生成的新计划必须跳过这些 unit，
并且只保留 active unit 之间仍然需要的依赖。

### 2.3 结构与依赖

任务必须满足：

- `Task.from_dict` 可解析；
- `id` 非空且唯一；
- `verb` 属于 canonical `Verb` 枚举；
- file/symbol target 符合 `Task` 的目标合同；
- dependency 指向存在任务、依赖顺序正确且无环；
- 必要时满足 case 的 required/forbidden verb 和 parallel-group 约束。

## 3. Dataset / Oracle

唯一 Dataset 来源是：

```text
evals/planner/dataset.json
```

版本为 `v2.4A-planner-v1`，共 50 个 case，覆盖：

| Family | 场景 | 例数 |
| --- | --- | ---: |
| P1 | 简单问答 | 4 |
| P2 | 单文件修改 | 4 |
| P3 | 多文件 Bug | 4 |
| P4 | Feature 开发 | 4 |
| P5 | 仓库分析 | 4 |
| P6 | 搜索与摘要 | 4 |
| P7 | 搜索与落盘 | 4 |
| P8 | 研究与编程混合 | 4 |
| P9 | 不完整请求 / abstain | 4 |
| P10 | 澄清 / abstain | 4 |
| P11 | 并行分支 | 5 |
| P12 | 部分完成 / Resume | 5 |

总计 50 个 case。Oracle 提供 canonical golden plan、结构校验、目标匹配、依赖校验、
约束校验和稳定 JSON 报告；同一输入不得依赖 LLM 解释来决定是否通过。

当前冻结 Dataset hash：

```text
7f5b28f608194a324f4244c860a8ed9101bcb7afa3b68e5129632ebfb0290291
```

## 4. 指标

所有指标由 per-case Oracle record 聚合得到：

- `schema_validity`: 计划任务是否符合 canonical Task 结构；
- `dependency_validity`: 依赖是否存在、顺序合法且无环；
- `plan_validity`: 模式、结构和依赖同时正确；
- `dependency_accuracy`: 期望 active-unit 依赖边与实际任务依赖边的重合度；
- `task_granularity`: 任务数是否落在 case 的 `[min_tasks, max_tasks]`；
- `unnecessary_task_rate`: 未匹配目标任务数 / 预测任务数；
- `missing_task_rate`: 缺失目标单元数 / active goal unit 数；
- `executable_plan_rate`: 结构、约束和 critical goal 都满足的比例；
- `overplanning_rate`: 超出 case 任务上限或包含未匹配任务的 case 比例；
- `critical_missing_task_rate`: 缺失 critical goal 的 case 比例。

空数据集不通过任何 acceptance gate。未提供的 Planner 结果按缺失记录处理，不得静默
当作成功。

## 5. Acceptance gate

后续真实 Planner 运行至少必须满足：

```text
case_count                         >= 50
schema_validity                    = 100%
dependency_validity                = 100%
plan_validity                      = 100%
critical_missing_task_rate         <= 5%
overplanning_rate                  <= 10%
```

这些是 Planner capability gate，不是 Provider availability、Runtime safety 或真实业务
成功率。模型没有完成目标但 Runtime 正确 abstain/failed 的结果，必须在报告中分层记录。

## 6. 证据与命令

Golden self-check 只证明 Dataset 和 Oracle 一致：

```bash
python -m evals.planner.report --self-check \
  --output /private/tmp/v24a_planner_selfcheck.json
```

当前 self-check 结果为：

```text
50/50 PASS
schema_validity          100%
dependency_validity      100%
plan_validity             100%
executable_plan_rate      100%
critical_missing_rate       0%
overplanning_rate           0%
```

这份结果不代表真实 Provider 或生产 Planner 已达到上述 gate。真实 Planner acceptance
必须使用相同 Dataset、相同 Oracle 和独立的 Provider evidence。

## 7. 后续边界

v2.4A-2 才接入真实 Planner，采集每个 case 的 raw plan、Provider、模型、prompt/fixture
hash 和 latency。不得在真实 acceptance 过程中修改 Dataset、Oracle 或 prompt 后覆盖原始
结果。

v2.4B 再处理 Tool Selection / ReAct；v2.4C 处理 Workflow 编排；v2.4D 处理 Memory
Learning。它们不应反向扩大本 ADR 的 Planner 结构合同。
