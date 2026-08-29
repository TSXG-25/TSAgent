# `evals/`

`evals/` 是能力评测的 Dataset、确定性 Oracle、指标和报告入口。

- 这里回答“模型/Planner 是否完成目标”；
- 每个能力 Dataset 应有版本和稳定 hash；
- Oracle 不执行 Tool、不读取真实 Workspace、不调用 Provider；
- Provider 结果与 Runtime Correctness 必须分开记录。

当前 Planner 能力基线位于 `evals/planner/`。新的能力评测优先放在这里，并通过 ADR
声明范围和门禁。
