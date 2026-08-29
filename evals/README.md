# `evals/`

`evals/` 是能力评测的 Dataset、确定性 Oracle、指标和报告入口。

- 这里回答“模型/Planner 是否完成目标”；
- 每个能力 Dataset 应有版本和稳定 hash；
- Oracle 不执行 Tool、不读取真实 Workspace、不调用 Provider；
- Provider 结果与 Runtime Correctness 必须分开记录。

当前 Planner 能力基线位于 `evals/planner/`。v2.4A-2c 之后，职责边界由独立 Dataset
表示：

- `evals/planner/dataset.json`：不可变的 v1 历史基线；
- `evals/planner/dataset_v1_1.py`：带 ownership 与确定性 target aliases 的校准视图；
- `evals/routing/`：验证 Chat 在 Planner 之外的路由合同；
- `evals/uncertainty/`：验证信息不足时的 deterministic abstention policy。

新的能力评测优先放在这里，并通过 ADR 声明范围和门禁。旧版本 hash 与真实 Provider
结果不得被校准后的 Oracle 覆盖。
