# TSAgent

面向复杂工程任务的长期运行 Agent Runtime。项目当前重点是：在模型能力不稳定、进程
重启、取消和多客户端接入时，仍然保持可验证的执行事实。

## 当前状态

```text
v2.3 Runtime Platform       已冻结
  Context Isolation         ✅
  Durable SQLite Store      ✅
  AgentService + Events     ✅
  Crash/Resume              ✅
  Workspace / Effect Truth  ✅
  Cancellation / Timeout    ✅

v2.4 Capability Development 进行中
  v2.4A Planner Contract / Dataset / Oracle  ✅ 冻结
  v2.4A 真实 Planner Capability               待验收
  v2.4B Tool Selection / ReAct                后续
  v2.4C Workflow                             后续
  v2.4D Memory Learning                      后续
```

这里的“已冻结”表示对应合同和回归门禁已经建立；真实 Provider 的能力结果始终单独记录，
不会用离线 golden self-check 冒充模型验收。

## 架构概览

```text
CLI / Desktop / future REST
              ↓
         AgentService
              ↓
 ApplicationContext / SessionContext / RunContext
              ↓
          Runtime spine
              ↓
       SqliteRuntimeStore
```

执行主链是：

```text
Goal → NextAction → Task → Compiler → ExecutionPlan
     → Executor → ActionResult → Verifier → next action / terminal state
```

普通动作失败不会偷偷启动第二套 Planner 循环；结构性失败经过统一的
`FailurePolicy → RecoveryDirective`。所有成功声明都必须有对应的执行或 Artifact evidence。

完整当前架构见 [docs/architecture.md](docs/architecture.md)，历史决策见
[docs/adr/](docs/adr/)。

## 目录职责

```text
agent/              Runtime、Context、Service、Planner、Compiler、Executor
apps/desktop/       Desktop UI 与本地传输适配器
benchmarks/         子系统合同与离线集成 Dataset / Oracle
evals/              能力评测 Dataset、Oracle 和报告工具
evaluation/         历史全局门禁、Metrics、FailBoard 与趋势检查
realtest_reports/   经脱敏的真实 Provider 证据归档
tests/              单元、合同和离线集成回归
tools/              受 Workspace/RunContext 约束的工具实现
```

`input/` 和 `output/` 是本地 Runtime workspace，不是源码或测试 fixture，默认不会被 Git
跟踪。测试所需文件应放在对应 Dataset 或 `tests/fixtures/` 中。

## 快速开始

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
.venv/bin/python -m pytest -q
```

只运行 v2.4A Planner Dataset 的确定性自检：

```bash
.venv/bin/python -m evals.planner.report --self-check
```

当前 Dataset 位于 `evals/planner/dataset.json`，共 50 个 case。真实 API 或本地模型测试
需要自行配置 Provider 凭据，结果必须与离线回归分开归档。

## 工程循环

新增能力遵循：

```text
ADR → Dataset → Oracle → implementation → offline regression → real-provider evidence
```

每项能力都要明确区分：

- Capability Outcome：模型/Provider 是否完成用户目标；
- Runtime Correctness：状态、权限、Artifact、事件和副作用是否符合合同；
- Provider Error：外部服务不可达、超时或协议错误。

## 版本边界

当前不在 v2.4A 中加入新的 Runtime 状态机、取消策略、分布式执行或 Provider fallback。
这些变化必须通过新的 ADR 和 Dataset 进入工程循环。
