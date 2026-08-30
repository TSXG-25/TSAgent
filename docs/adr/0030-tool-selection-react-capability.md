# ADR-0030: Tool Selection / ReAct Capability Contract（v2.4B）

- 状态: Proposed — v2.4B-2a Production Selector Bootstrap Implemented
- 范围: 单步 Tool Selection / ReAct action choice
- 前置基线: v2.4A Planner Contract 已冻结；v2.3 Runtime spine、Verifier 和 durable state 已冻结
- 本阶段: 只建立合同、确定性 Dataset 和 Oracle；不修改 Planner、Compiler、Runtime 或 Tool 实现

## 1. 背景

v2.4A 测量的是 `Goal → Task DAG`。下一层要测量的是：在已有 Task、当前状态和最近一次
`ActionResult` 观察下，Agent 是否选择了正确的下一步，而不是重复动作、过早结束或调用
不可用工具。

本 ADR 不把 Tool Selection 重新塞回 Planner，也不创建第二套 Executor。它定义现有
`agent.next_action.NextAction` 在能力评测中的输入和输出边界。真实 Provider acceptance
必须在本合同、Dataset 和 Oracle 冻结后另行运行。

## 2. 决定

Tool Selection 的纯函数语义是：

```text
Task + projected State + Observation → exactly one NextAction
```

其中：

- `Task` 是当前可执行任务的窄投影：`id`、`verb`、`target`、`target_type`、`status`、`dependencies`；
- `State` 是 Runtime 提供的只读投影：目标、当前/待处理任务、已完成 outcome、可用工具、完成证据和历史动作摘要；
- `Observation` 是最近动作及其结构化 `ActionResult`，包括 `ok`、`verified`、`error_code`、`retryable` 和机器结果；
- Selector 只能选择一个动作，不执行 Tool、不修改 State、不生成新的 Planner 计划；
- `reason` 是诊断元数据，不是 effect evidence，也不能改变动作真值。

### 2.1 `NextAction` 输出

输出使用现有 canonical `NextAction`：

```text
kind = tool | answer | ask
tool = canonical execution primitive（kind=tool 时必填）
args = 结构化工具参数
reason = 可选诊断说明
task_id = 当前动作对应 Task（kind=tool 时必填）
```

评测使用 Compiler/ExecutionStep 的 canonical tool identity，例如：

```text
filesystem.read
filesystem.write
filesystem.copy
filesystem.move
filesystem.delete
filesystem.list
run_python
run_python_file
web_search
web_fetch
shell
```

Tool Registry 的实现别名（如 `read_file`、`write_file`）属于下游映射，不由 Selector
自行发明。`answer` 和 `ask` 不得携带 Tool、参数或 Task identity。

### 2.2 选择不变量

1. 每次决策只返回一个 `NextAction`；不返回 action list，不在 Selector 中执行副作用。
2. `tool` 必须存在于当前 projected `available_tools`；未知或不可用 Tool 不是可执行选择。
3. Tool action 的 Task 必须处于 pending/running，且所有依赖已达到 succeeded/skipped。
4. retry 只由已有 `ActionResult.retryable` 与 Runtime budget 允许；Selector 不创建第二个隐式预算。
5. 已有 verified effect 不得被再次选择为相同 Task 的副作用动作；验证不足时应选择验证动作或停止，而不是重复写入/执行。
6. `answer` 只能在 Runtime projection 标记 `answer_ready` 时选择；自然语言声称完成不能替代 `ActionResult`、Verifier 或 artifact evidence。
7. `ask` 用于缺少必要信息或能力边界；它不声明任何外部动作已经发生。
8. Selector 不读取 SQLite、Checkpoint、Workspace、绝对路径或 Provider 原始上下文；它只消费窄投影。
9. action 选择失败属于能力/合同结果，不能由 Oracle 自动重写、由 harness retry 或由 golden action 修复。

## 3. Dataset / Oracle

第一版 Dataset 位于：

```text
evals/tool_selection/dataset.json
evals/tool_selection/oracle.py
```

版本为 `v2.4B-tool-selection-v1`，共 24 个 case，覆盖六个 family：

Dataset hash（完整 JSON envelope）：

```text
bc0baa5afcf68ba68a787387edd7297a4c22bea6334e1e0afd06c61136952409
```

| Family | 测量内容 | 例数 |
| --- | --- | ---: |
| `INITIAL_SELECTION` | 从明确 Task 选择首个 Tool | 4 |
| `RESULT_TRANSITION` | 成功 Observation 后 finish / verify | 4 |
| `FAILURE_RECOVERY` | retry、澄清和 unsupported boundary | 4 |
| `DEPENDENCY_CONTROL` | 依赖就绪、顺序和参数绑定 | 4 |
| `VERIFICATION_BOUNDARY` | effect evidence、纯回答和缺参边界 | 4 |
| `OBSERVATION_BRANCH` | 无结果切换、URL fetch、输出复用和去重 | 4 |

Oracle 只验证结构和状态约束，不调用 Tool、Provider、Workspace 或 LLM。它输出：

- `schema_validity`；
- action kind accuracy；
- Tool selection accuracy；
- argument binding accuracy；
- Task targeting/dependency accuracy；
- safe action rate；
- duplicate verified effect count；
- premature finish count。

Golden self-check 只证明 Dataset、canonical `NextAction` 和 Oracle 彼此一致，不代表真实
Provider 已达到能力门槛。Dataset hash 由 Oracle 对完整 JSON envelope 计算，历史结果不能
被后续 calibration 覆盖。

## 4. Evidence rules

真实 Provider harness（后续 v2.4B-2）必须逐 case 保存：

```text
case_id
input/state/observation
raw provider output
normalized NextAction
provider/model/path
latency/token usage（若可得）
oracle result
failure category
```

并遵守：

- `automatic_retry = false`；
- `provider_fallback = false`，格式降级也单独记录；
- 不修改 JSON、不套 golden action、不执行 Tool；
- Provider/API failure、Contract/Oracle failure、Runtime/Integration failure 与 capability failure 分开；
- 同一 Dataset、prompt/fixture hash 固定后，原始 evidence 不被覆盖。

## 5. 后续阶段

```text
v2.4B-1  Contract / Dataset / Oracle       ✅
v2.4B-2  Real Provider baseline
  ├─ B-2 preflight                        ✅ production selector missing evidence
  ├─ B-2a Production Selector Bootstrap   ← current
  └─ B-2b Real Provider baseline
v2.4B-3  Attribution / capability improvement（仅在形成系统性 cluster 时）
v2.4B-4  Clean freeze evidence
```

`agent.next_action_selector.NextActionSelector` 是 B-2a 的唯一生产决策入口。它只消费
`TaskProjection`、`ExecutionStateProjection` 和 `ActionObservation`，输出 canonical
`NextAction`；Provider/format evidence 通过独立结果返回，不改变动作真值。B-2a 不接入
Runtime 主循环，也不修改 Planner、Compiler、Executor、Tool Registry、Workspace 或
Checkpoint。

若真实 baseline 发现当前
`NextAction` 与生产 Runtime 的 observation projection 不一致，应先归类为
`P-CON`/`P-INT`，保留原始 evidence，再决定是否做最小合同修订；不能在 harness 中静默
适配成另一套 action schema。
