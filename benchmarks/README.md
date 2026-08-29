# `benchmarks/`

`benchmarks/` 保存面向子系统或 Runtime 合同的确定性集成 Dataset、Runner、Oracle 和
离线验证器，例如 Checkpoint、Context Isolation、Workflow Resume 和 Durable Store。

这里验证的是执行边界、持久化、隔离和副作用等 Runtime Correctness，不用于替代
`evals/` 中的模型能力评测。运行结果和临时快照应写入临时目录或明确的证据归档。
