# ADR-0029: Planner Capability Contract（v2.4A）

- 状态: Accepted — v2.4A Implemented and Verified（Context / Evidence closeout；能力残差已记录）
- 范围: Planner 的目标分解、任务结构和依赖质量评测
- 前置基线: v2.3 Runtime / Service / Desktop 能力已冻结；本 ADR 不改变生产执行路径

## 1. 背景

Runtime 的持久化、恢复、事件、隔离和取消合同已经有独立门禁。下一阶段需要单独测量
Planner 是否把用户目标转换成完整、可执行且不过度拆分的任务计划。Planner 能力结果不能
替代 Runtime 正确性，也不能把 Provider 或模型质量问题伪装成 Runtime 失败。

v2.4A 只建立一个 Provider-independent 的合同、确定性 Dataset 和 Oracle。它不接入新的
Planner 控制循环，不修改 prompt、Tool 选择或执行器。v2.4A-2c 进一步校准了请求路由、
目标别名和信息不足策略；这些校准仍然不改变生产 Planner 的实现。

## 2. 合同

### 2.1 请求边界与计划模式

当前生产职责边界是：

```text
User Request
    ↓
Intent / Execution Need Boundary
    ├── CHAT ─────────────→ Answer
    ├── NEED_CLARIFICATION → Ask
    └── PLANNING ──────────→ Planner
                                  ├── PLAN
                                  └── ABSTAIN
```

因此，Chat 不属于当前 Planner。Planner 只接收已经确定需要目标分解的请求，当前
Planner 输出模式为：

- `plan`: 至少有一个任务，且不 abstain；
- `abstain`: 不猜测缺失目标，`tasks=[]` 且 `abstain=true`。

原 v1 Dataset 中的 `chat` mode 仍作为不可变历史基线保留；在 v1.1 calibrated view
中，PA001–PA004 标记为 `routing` ownership，由独立 Routing Dataset 验证，不计入
Planner decomposition capability。

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

v2.4A-2c 之后，Dataset 来源按职责拆分为：

```text
evals/planner/dataset.json
evals/planner/dataset_v1_1.py
evals/planner/target_aliases_v1_1.json
evals/routing/dataset.json
evals/uncertainty/dataset.json
```

`evals/planner/dataset.json` 是版本 `v2.4A-planner-v1` 的 50-case immutable baseline，
hash 为：

```text
7f5b28f608194a324f4244c860a8ed9101bcb7afa3b68e5129632ebfb0290291
```

它的原始 case 和 hash 不被 v1.1 重写。v1.1 是由 v1 派生的校准视图，保留 50 个 case，
其中 46 个为 `planner` ownership、4 个为 `routing` ownership；加入的 target alias
必须属于标准缩写、全称、中英文标准名称或常见同义表达，不得根据 Provider 的某次
实际输出逐条抄录。v1.1 hash 为：

```text
8c268b5855d109c7a2be940257ae0acf7edc877793dd5914cc020ae380aae023
```

Planner v1.1 仍覆盖原有以下 family：

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

总计 50 个 case。Planner Oracle 提供 canonical golden plan、结构校验、目标匹配、依赖
校验、约束校验和稳定 JSON 报告；同一输入不得依赖 LLM 解释来决定是否通过。v1.1 的
aliases 只改变明确登记的 text target 等价匹配，不改变 v1 的结构合同。

Chat ownership 由 Routing Dataset 独立验证：4 个 case，hash 为：

```text
f3aea7b4cecdcd1997a7716f9c3e7b2396efa2ff6fb3e6b1721784135d345458
```

其硬约束是 `CHAT`、不调用 Planner、且不要求执行。

信息不足由独立 Uncertainty Dataset 验证，共 27 个 case，hash 为：

```text
8f1479bdded0f00e20fd4d283082869d078b4fa217719dc768ae8a6afaaf1cdb
```

其中包含成对的可继续/应澄清边界；它测量 abstain policy，不把结果归因给 Planner。

## 4. 指标

Planner 指标只对 Planner-owned cases 聚合得到；Routing 与 Uncertainty 使用各自的
deterministic Oracle。Planner 指标包括：

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

后续真实 Planner 运行至少必须满足（只针对 Planner-owned cases）：

```text
planner_case_count                 >= 46
schema_validity                    = 100%
dependency_validity                = 100%
plan_validity                      = 100%
critical_missing_task_rate         <= 5%
overplanning_rate                  <= 10%
```

这些是 Planner capability gate，不是 Provider availability、Runtime safety 或真实业务
成功率。模型没有完成目标但 Runtime 正确 abstain/failed 的结果，必须在报告中分层记录。

v2.4A-4 增加语义审计要求：目标别名或目标边界造成的 matcher 未命中不能直接归因于
Planner。若 clean evidence 证明计划已覆盖用户要求，而失败只来自确定性 matcher 的表达
边界，该 case 记录为 `P-MEASUREMENT`，不通过删除任务或修改旧 Oracle 来“修复”分数。
该语义归因不会回写历史结果，也不改变 v1/v1.1 Dataset hash。

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

v2.4A-2c calibration evidence 使用以下确定性命令生成：

```bash
python3 realtest_reports/harness/v24a_contract_calibration.py
python3 realtest_reports/harness/v24a_uncertainty.py
```

校准结果应明确区分：

- v1 immutable baseline self-check；
- v1.1 alias/ownership self-check；
- Routing ownership self-check；
- 当前生产 uncertainty detector baseline。

Uncertainty detector 的 mismatch 记录为 `P-UNCERTAINTY`，不会被包装成 Planner failure，
也不会因为该 baseline 而修改 Planner prompt。

本次校准记录的当前 production-policy baseline 为 `20/27`：abstain precision `85.7%`、
recall `50.0%`、false abstention rate `6.7%`、missed abstention rate `50.0%`。这组数值是
后续 Uncertainty Policy 改进的起点，不是 v2.4A Planner capability acceptance 结果。

## 7. 后续边界

v2.4A-2d 才在校准后的 Planner-owned Dataset 上重新接入真实 Planner，采集每个 case 的
raw plan、Provider、模型、prompt/fixture hash 和 latency。旧 v1 baseline 只可作为历史
对照，不能用 v1.1 Oracle 的重评分结果冒充新的 capability evidence。真实 acceptance
过程中不得修改 Dataset、Oracle 或 prompt 后覆盖原始结果。

在 v2.4A-2d 中，PA013 与 PA016 曾列入 Planner Capability Watchlist。v2.4A-3
候选运行会重新记录它们的结果；只有在新一轮 evidence 中形成可复现的共同 failure family，
才允许继续扩大 Planner Improvement，不能按单 case 添加规则。

## 8. v2.4A-3 候选改进证据

v2.4A-3 只调整 Planner instruction 与 uncertainty detector 的确定性边界，没有修改
Dataset、Oracle、Runtime、Tool selection 或执行架构。完整的三轮 raw/calibrated evidence
与对比摘要归档在：

```text
realtest_reports/results/v24a_planner_improvement_round1.json
realtest_reports/results/v24a_planner_improvement_round2_calibrated.json
realtest_reports/results/v24a_planner_improvement_round3_calibrated.json
realtest_reports/results/v24a_planner_improvement_comparison.json
realtest_reports/results/v24a_planner_improvement_comparison.md
```

Round 3 候选结果为：45 个可评估 case 中 `37/45 (82.2%)` capability pass，schema 与
dependency validity 均为 `100%`，executable plan rate `82.2%`，missing task rate
`12.9%`，overplanning rate `35.6%`，clarification accuracy `100%`。归因结果为
`P-CAP=8`（全部 `UNDER_PLAN`）、`P-PROV=1`、`P-CON/P-ORACLE=0`、`P-INT=0`。

这相对 v2.4A-2d 的 `20/44 (45.5%)` capability pass 有明显改善，但仍未达到本 ADR 的
`missing_task_rate <= 5%` 与 `overplanning_rate <= 10%` gate。因此 ADR 当前只记录为
`v2.4A-3 Candidate Evaluated`，不宣布 Planner 已冻结。剩余 watchlist 为
`PA016, PA018, PA021, PA023, PA029, PA042, PA046, PA049`；不得针对单个 case 添加规则。

其中 `PA012` 是 Provider Error，不计入 Planner capability failure；独立 uncertainty
policy run 为 `26/27`，同样不进入 Planner capability 分数。候选真实运行发生在
`6cf9e0d0` 的 dirty working tree 上，后续如需冻结必须先基于提交后的 clean checkout
复核；raw report 不覆盖。

## 9. v2.4A-4 closeout discovery

对 Round 3 的 8 个机械 `P-CAP:UNDER_PLAN` 进行了逐案 semantic anatomy。结果没有形成
可直接支持下一轮泛化 Prompt 修补的共同 decomposition failure：PA018、PA021、PA023、
PA042 主要是 text target/alias 边界；PA016、PA029 包含必要前置或已覆盖的显式动作；
PA046、PA049 依赖 `completed_units` 和 resume 上下文，但 direct Planner harness 没有将这些
状态注入生产 `plan_with_metadata`。完整审计见：

```text
realtest_reports/results/v24a_planner_closeout_anatomy.json
realtest_reports/results/v24a_planner_closeout_anatomy.md
```

因此当时不删除合理 Task、不修改 Dataset/Oracle、不添加 case-specific 规则，也不宣布
v2.4A 冻结。另发现 PA005 的 `SUCCESS_WITH_PROVIDER_FALLBACK` 实际三次调用均为 DeepSeek，
其中一次是 structured-output rejection 后的同 Provider raw-text fallback，并非跨 Provider
切换；后续 freeze evidence 必须区分这两种 fallback。v2.4A-4 当前结论为：先解决 evidence
命名与 P12 context contract，再决定是否需要最后一轮泛化候选。该阶段性结论由下方
clean freeze evidence 更新。

v2.4B 再处理 Tool Selection / ReAct；v2.4C 处理 Workflow 编排；v2.4D 处理 Memory
Learning。它们不应反向扩大本 ADR 的 Planner 结构合同。

## 10. v2.4A-4 合同校准：Continuation Projection 与 Evidence Semantics

### 10.1 P12 continuation planning context

Planner 可以负责 continuation/resume 的剩余目标分解，但只能消费 Runtime 投影后的
`PlannerContext`。生产 `ContextBuilder` 从当前 Runtime plan 派生以下只读字段：

```text
completed_tasks       = 已达到 succeeded/skipped 的任务描述
established_facts     = 已记录的紧凑观察事实
available_artifacts   = opaque Artifact identity
continuation_scope    = 未完成任务及 active task 之间的依赖
```

任务描述最多包含 `id`、`verb`、`target`、`target_type`、`status` 和
`dependencies`。Planner 不接收原始 Checkpoint、完整 Runtime state、Executor inputs、
错误详情或文件绝对路径。`plan_with_metadata(..., planning_context=...)` 只渲染这组
投影；未续接的普通 Planner 请求保持原有输入边界。

P12 的 `completed_units` 是 durable-state fixture 的来源，不是额外的 Planner golden
answer。真实 Runtime 必须通过同一 projection 提供已完成任务和剩余 scope；direct
Planner harness 未提供该 projection 的旧结果只能作为历史对照，不能证明 continuation
contract 已被验证。

### 10.2 Provider 与格式路径分离

Real-provider evidence 使用两个正交字段，不再将同 Provider 的格式降级称为 Provider
fallback：

```text
provider_path:
  SINGLE_PROVIDER
  CROSS_PROVIDER_FALLBACK
  NOT_CALLED
  UNRESOLVED

format_path:
  STRUCTURED_ONLY
  STRUCTURED_TO_RAW_FALLBACK
  RAW_ONLY
  NOT_CALLED
  UNRESOLVED
```

因此 structured bind/invoke 失败后仍由同一 DeepSeek raw-text 调用成功，记录为：

```text
provider_path = SINGLE_PROVIDER
format_path   = STRUCTURED_TO_RAW_FALLBACK
```

只有观察到两个确定的 Provider 身份时，才记录 `CROSS_PROVIDER_FALLBACK`。auto router
无法从透明 harness wrapper 解析真实 Provider 顺序时必须记录 `UNRESOLVED`，不能猜测。

旧 v2.4A-2d/3 raw evidence 保持原样，不重新命名或重算。最终 freeze 必须在提交后的
clean checkout 运行，并同时保留历史 mechanical score 与 semantic attribution；本节
只解决输入上下文和证据口径，不引入新的 Planner subsystem、Dataset case 或 case-specific
规则。

## 11. v2.4A clean freeze evidence

`a15ab559e276db5bf1c6326179efa86e9d7768cb` 是本次 clean checkout 的精确提交，运行时
工作树干净。使用 v1.1 calibrated Dataset（hash
`8c268b5855d109c7a2be940257ae0acf7edc877793dd5914cc020ae380aae023`）一次提交 46 个
Planner-owned case；没有自动 case retry、golden-plan 修复或跨 Provider fallback。

原始证据：

```text
realtest_reports/results/v24a_planner_freeze_clean_a15ab559.json
realtest_reports/results/v24a_planner_freeze_clean_a15ab559.md
```

结果摘要：

```text
submitted/evaluable                    46 / 45
capability mechanical score             39 / 45 = 86.7%
schema validity                         100%
dependency validity                     100%
clarification accuracy                  100%
missing task rate                       8.2%
overplanning rate                       35.6%（诊断指标，不作为语义归因结论）
P-CAP after semantic audit              0
P-MEASUREMENT                           PA021, PA022, PA023, PA029, PA030, PA042
P-PROV                                  PA012（single-provider error）
P-CON / P-INT                           0
P12 continuation projection             5 / 5
provider fallback                       0
structured-to-raw format fallback       PA005（same DeepSeek provider）
```

PA021/PA022/PA023/PA029/PA030/PA042 的 raw plan 都包含用户要求的动作、合理依赖和可执行
任务；它们未通过的是 v1.1 frozen target matcher 的具体文字表达，而不是缺少新的
decomposition unit。因此报告同时保留机械结果和 `P-MEASUREMENT` 语义归因，不将这次
clean run 重述为 45/45，也不覆盖 v2.4A-2d/v2.4A-3 历史分数。

PA046–PA050 在新增的 Runtime `PlannerContext` continuation projection 下全部通过，证明
Planner 只消费 `completed_tasks`、`established_facts`、opaque artifact references 和
`continuation_scope` 的窄投影；它不直接读取 Checkpoint、完整 Runtime state 或 golden plan。

因此 v2.4A 的 Context / Evidence 合同可以冻结并进入 v2.4B。剩余的 mechanical
overplanning 与目标表达覆盖属于后续 measurement calibration/watchlist，不得在 v2.4A
closeout 中通过 case-specific prompt 或 Oracle 修改处理。
