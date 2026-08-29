# `evaluation/`

`evaluation/` 保存项目级、历史兼容的质量门禁基础设施：Metrics facade、FailBoard、
Architecture/Contract Verification、Trend Gate 以及既有 Dataset factory。

它负责跨能力的全局门禁与历史趋势，不是新能力 Dataset 的默认目录。新能力的 Dataset
和专属 Oracle 放在 `evals/`；子系统合同/集成 Dataset 放在 `benchmarks/`。
