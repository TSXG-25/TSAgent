# ADR-0031: Workflow Capability Contract（v2.4C）

- 状态: Accepted — v2.4C Implemented and Integration Verified
- 前置基线: ADR-0029 Planner Capability、ADR-0030 Tool Selection / ReAct 已冻结
- 范围: Workflow 选择、实例化、拒绝与 continuation nomination
- 非范围: WorkflowExecutor 重写、Resume policy、Stage execution、Tool selection

## 1. 背景

现有 Runtime 已能执行 canonical `Workflow → Stage → Task → ExecutionPlan`，并通过
Checkpoint/RunResume 合同安全恢复。v2.4C 不再证明 WorkflowExecutor “能跑”，而是测量：

> 给定用户 Goal、窄 Runtime Context 和当前可用 Workflow，系统能否正确决定复用、实例化、
> 拒绝或请求必要信息。

错误选择 Workflow 会让后续每个 Stage 都合法地执行错误目标，因此
`False Workflow Selection` 是本阶段最高风险指标。

## 2. 决定

Workflow capability 的纯决策语义为：

```text
Goal + projected Context + AvailableWorkflows
    → exactly one WorkflowDecision
```

canonical `WorkflowDecision` 定义在 `agent.workflow_decision`：

```text
kind = instantiate | reuse | decline | ask
workflow_id
bindings
reason
```

- `instantiate`: 选择一个当前可用 Workflow definition，并给出完整参数绑定；
- `reuse`: 提名继续当前 active Workflow，不替换 durable bindings；
- `decline`: 当前 Goal 应继续走 Planner/Task 路径，不套用 Workflow；
- `ask`: 缺少安全实例化所需的信息；不执行 Workflow。

`reason` 仅是诊断信息，不是 workflow/effect evidence。

### 2.1 输入投影

Selector 只能消费以下窄事实：

```text
goal
available_workflows
  ├─ id / version / description
  ├─ required_bindings / defaults
  ├─ required_artifacts
  ├─ required_capabilities
  └─ output_types
context
  ├─ available artifacts
  ├─ available capabilities
  ├─ established facts
  └─ active_workflow projection
```

不得直接读取 WorkflowRegistry、Checkpoint、SQLite、Workspace 或完整 Runtime state。
WorkflowRegistry 与 Runtime policy 先完成 composition projection，Selector 只读。

### 2.2 所有权边界

```text
Planner
  owns Goal → Task DAG

Workflow capability
  owns Goal/context → instantiate | reuse | decline | ask

NextActionSelector
  owns dynamic Task state → one NextAction

RunResumeCoordinator / ResumeValidator
  own exact resume/replay/block decision

WorkflowExecutor
  owns Stage iteration and Stage → Task projection
```

`reuse` 不是第二套 Resume Policy。它只能在 Runtime projection 已提供：

```text
active_workflow.status = active
reuse_allowed = true
```

时提名当前 workflow id。具体 `RESUME_EXACT / REPLAY_FROM_STAGE / BLOCK` 仍由既有
RunResumeCoordinator/ResumeValidator 决定。completed Stage 不得由 Selector 重新打开。

### 2.3 实例化与参数绑定

`instantiate` 必须满足：

1. workflow id 位于 projected available set；
2. required capabilities/artifacts 已存在；
3. required bindings 全部来自 Goal、projected facts 或 catalog defaults；
4. 不发明路径、环境、账号、artifact 或 capability；
5. 不把一次简单 Task 包装成 Workflow；
6. 不决定 Workflow 内 Stage 的 `COMPILED xor DYNAMIC` owner。

参数绑定是 Workflow capability 的合同职责；PlannerStage 不应长期硬编码某个 Workflow 的
输入 Artifact。实际 Artifact hydration 和 workspace 验证继续属于 WorkflowExecutor/Runtime。

### 2.4 Runtime integration invariants

- instantiated Workflow 仍通过既有 WorkflowExecutor；
- Stage 仍投影为 canonical Task；
- 每个 Task 继续服从 ADR-0030 的 execution ownership；
- Workflow 选择不执行 Tool、不写状态、不创建 retry/resume budget；
- completed Stage execution count 保持 0；
- stale/missing Artifact 不得被旧 Stage output 假满足；
- stage outcome 改变后的 replay/blocked 由 ResumeValidator 决定；
- Workflow decline 不得阻断通用 Planner/Task 路径；
- client disconnect 不改变 Workflow decision 或 continuation facts。

## 3. Dataset / Oracle

第一版冻结 Dataset：

```text
evals/workflow_capability/dataset.json
version = v2.4C-workflow-capability-v1
cases   = 24
hash    = 43338803cbe9192c19a2957887a8013c17058a6dbea9e7bb6cb66c06d60fbd69
```

| Family | 目的 | Cases |
| --- | --- | ---: |
| `CLEAR_MATCH` | 明确匹配已有 Workflow | 4 |
| `FALSE_MATCH_GUARD` | 相似请求不得误套模板 | 4 |
| `PARAMETER_BINDING` | 参数/default/缺参边界 | 4 |
| `SIMPLE_TASK_DECLINE` | 简单 Task 不 over-workflow | 4 |
| `CONTINUATION` | active/completed/blocked continuation | 4 |
| `RUNTIME_BOUNDARY` | Workflow 与 dynamic/能力投影边界 | 4 |

deterministic Oracle 位于 `evals/workflow_capability/oracle.py`。它不调用 Provider、Tool、
WorkflowExecutor、Registry 或 ResumeCoordinator，只验证：

```text
Schema Validity
Decision-kind Accuracy
Workflow Accuracy
Binding Accuracy
Safe Decision Rate
False Workflow Selection
Missed Workflow
Unsafe Reuse
```

Golden `24/24` 只证明 Dataset、合同和 Oracle 自洽，不代表生产能力。

## 4. Real baseline rules

真实 Provider baseline 必须调用正式 production Workflow decision entry，不复制 prompt 或创建
benchmark-only selector，并冻结：

```text
automatic_retry = false
provider_fallback = false
golden_repair = false
json_repair = false
workflow_execution = false
runtime_mutation = false
```

归因仍使用：

```text
P-CAP / P-CON / P-ORACLE / P-INT / P-PROV
```

能力细分至少包括：

```text
WRONG_KIND
WRONG_WORKFLOW
ARGUMENT_BINDING
FALSE_WORKFLOW_SELECTION
MISSED_WORKFLOW
UNSAFE_REUSE
MISSED_ASK
SCHEMA_INVALID
```

首次 baseline 前不得修改生产选择逻辑；失败先聚类，再决定是否改进。

## 5. Acceptance gate

Capability quality 与 Runtime correctness 分开：

```text
Schema validity                    100%
False Workflow Selection             0
Unsafe Workflow Reuse                 0
Unavailable Workflow execution        0
Missing-binding execution             0
Completed Stage re-execution           0
Duplicate side effect                  0
False Workflow completion              0
Planner/Selector ownership drift        0
Resume policy duplication               0
```

真实 capability 不要求 24/24；系统性能力缺口必须由聚类证据证明，禁止逐 case prompt fitting。

## 6. 阶段

```text
v2.4C-1  Contract / Dataset / Oracle / Preflight    DONE
v2.4C-2  Production selector + real baseline         DONE
v2.4C-3  Attribution audit                           DONE — no systemic gap proven
v2.4C-4  Runtime integration / clean freeze          DONE
```

## 7. Freeze evidence

```text
Freeze integration HEAD          df7bb543161ec1bd83d804e49877cf793a7f66b5
Real baseline HEAD               a040c48aab4b0abb2fcdb31beb902a5758c0e5c8
Mechanical capability            22/24 (91.7%)
Schema / Workflow / Safety       100% / 100% / 100%
False Workflow Selection         0
Unsafe reuse                     0
Provider / Contract / Oracle /
Integration failures             0 / 0 / 0 / 0
Clean related regression         106 PASS
Preflight blockers/watchlist     0 / 0
```

Real baseline 后新增的 Runtime composition 只增加 Workflow metadata projection 与
generic binding consumption；`WorkflowDecisionSelector` prompt、projection hash 和决策逻辑未变。
两份证据的 prompt/projection hash 完全一致。

机械失败 C002/C019 保留在 frozen Oracle 结果中。semantic audit 分别归为自由文本 binding
测量边界与 completed Workflow 的 `decline/ask` 合同边界；没有证据支持新的系统性 P-CAP
cluster，因此未做逐 case prompt fitting。
