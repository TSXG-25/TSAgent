# v2.2C Real API Benchmark（Run 级跨 Workflow 恢复）

验证一条 Run 内三个 Workflow（A→B→C）在**真实 Provider**、**真实文件副作用**、
**真实进程重启**条件下，能否恢复到正确的 Workflow，并且**不重复已完成副作用**。

## 固定拓扑（A→B→C）

```text
A spec    需求分析（LLM）→ output/spec.md
B impl    读 spec.md → LLM 生成 solution.py → 写入
C verify  验证产物（LLM）→ output/report.md
```

每个 case 使用独立 `run_id` / `session` / 隔离临时 workspace /
独立 `JsonCheckpointStore` / `JsonRunResumeStore` / side-effect ledger。

## 目录

```text
chain.py          三 Workflow 定义（真实 LLM + write_file 副作用）
harness.py        V22CCase：隔离 workspace、executor 计数、副作用 ledger、
                  CountingWorkflowExecutor（计数 + 故障注入）
store.py          JsonCheckpointStore：跨进程 checkpoint 持久化
restart_worker.py 子进程 worker：--phase a|b_int|crash|resume
runner.py         主 runner：--case c01|c02|c03|c07（子进程真实重启）
offline_dryrun.py 离线快速预检（fake executor，不烧 API）
```

## 用法

```bash
# 真实 API（DeepSeek，需要网络）
PYTHONPATH=. python -B benchmarks/v22c/runner.py --case c01   # 无中断基线
PYTHONPATH=. python -B benchmarks/v22c/runner.py --case c02   # A 完成后进程重启 → B→C
PYTHONPATH=. python -B benchmarks/v22c/runner.py --case c03   # B 在 read_spec 后中断 → EXACT
PYTHONPATH=. python -B benchmarks/v22c/runner.py --case c04   # B 幂等 Stage → REPLAY_FROM_STAGE
PYTHONPATH=. python -B benchmarks/v22c/runner.py --case c05   # A/B 完成、C ACTIVE → 只恢复 C
PYTHONPATH=. python -B benchmarks/v22c/runner.py --case c06   # 上游 spec.md 被篡改 → 阻断 B
PYTHONPATH=. python -B benchmarks/v22c/runner.py --case c07   # B 写盘后 checkpoint 前崩溃
PYTHONPATH=. python -B benchmarks/v22c/runner.py --case c08   # 错误 run/workflow 身份校验
PYTHONPATH=. python -B benchmarks/v22c/runner.py --case all   # P0 全 8 例统一报告

# 离线预检（不调用真实 LLM）
python -B benchmarks/v22c/offline_dryrun.py
```

每个 case 输出到 `V22C_RESULTS`（默认 `/private/tmp/v22c_results.json`）。

## 故障注入点

```text
c03  AFTER_STAGE:impl:read_spec        （WorkflowExecutor interrupt 后 SUSPEND）
c07  AFTER_SIDE_EFFECT_BEFORE_CHECKPOINT:impl:write_code（真实写盘 → SimulatedCrash，进程 rc=70 退出）
c04  AFTER_STAGE:impl:read_spec + REPLAY_FROM_STAGE request
c05  AFTER_SIDE_EFFECT_BEFORE_CHECKPOINT:verify:write_report（真实写盘 → SimulatedCrash，rc=70）
c06  外部篡改 spec.md（runner 在 req_b 后追加内容）
```

`SimulatedCrash` 由 `CountingExecutor` 在真实 `write_file` 成功返回后、`WorkflowExecutor`
记录 Stage checkpoint 之前抛出；Store 与 Workspace 均已落盘，恢复从 Store 重建。

## 指标口径

```text
Raw E2E Rate        = 完整通过 case / 全部 case
Correct Workflow Resume Rate = 恢复到正确 active workflow 的比例
Completed Workflow Skip Rate = 已完成 workflow 未被重复执行的比例
Duplicate Side Effect Rate   = 重复副作用 / 所有已完成副作用（门槛 0%）
Unsafe Resume Acceptance Rate= 存在冲突仍继续恢复的比例（门槛 0%）
Artifact Integrity Rate      = 恢复后产物通过 digest / ExecutionVerifier 的比例
Process-Restart Recovery     = 真正跨进程恢复成功的比例
Provider Error Rate          = timeout / connection error / 全部 case
```

## Smoke 结果（2026-08-06，DeepSeek deepseek-v4-flash）

第一轮 smoke（旧 Runtime）→ 修复后复跑，见 `results/smoke_round1.json`（旧）与
`results/smoke_round2.json`（复跑）。复跑结论：

| Case | 结果 | 关键证据 |
| --- | --- | --- |
| C01 无中断基线 | ✅ PASS | 三工作流 success、3 文件存在、solution.py 真实运行 `rc=0 stdout='49'`、Provider 各 1 |
| C02 跨 Workflow 恢复 | ✅ PASS | A 执行 1 次、resume 不再执行 A、spec.md hash 跨进程不变、B/C 各 1 次、Duplicate=0、index 修订链推进 |
| C03 B 中途 EXACT 恢复 | ✅ PASS | `RESUME_EXACT` + `skipped=[spec]`；read_spec 从 checkpoint 跳过且 `spec_content` 由 `spec.md` 水合 → gen_code/write_code 在 resume 进程内各执行 1 次，solution.py/report.md 存在，Duplicate=0 |
| C07 副作用窗口崩溃 | ✅ PASS | `checkpoint_lineage_fallback` 找到崩溃时 checkpoint；`python_code` 从已写盘的 solution.py 水合（digest 校验一致）；`_recover_committed_file_effect` 把 write_code 判为 COMMITTED 不重写；`solution_unchanged=true`、B LLM 未再调用、Duplicate=0 |

### 第一轮 smoke 发现的两个根因（已被 Runtime 修复覆盖）

1. **C03 — Resume 时 Artifact 不水合（空完成/假成功）**
   修复后：Workflow 恢复时 `hydrate_checkpoint_artifacts` 按 reference+digest
   水合 file-backed Artifact；`required_outputs` 缺失不再静默跳过，而是
   `_resume_dependency_blocked_result` 显式失败。
2. **C07 — 崩溃窗口的 index 不引用最新 checkpoint → 从零重启**
   修复后：Coordinator 在 index 无 checkpoint 引用时回退
   `checkpoint_store.latest_for_workflow(run_id, workflow_id, activation_attempt_id)`；
   `_recover_committed_file_effect` 对已落盘且内容一致的写副作用判 COMMITTED，
   不重复写盘；内容不一致时 `_resume_side_effect_blocked_result` 拒绝不安全恢复。

复跑指标：Raw E2E 4/4、Correct Workflow Resume 4/4、Completed Workflow Skip 100%、
Duplicate Side Effect Rate 0%、Unsafe Resume Acceptance 0%、Artifact Integrity 4/4、
Provider Error 0%。

## P0 全 8 例结果（2026-08-06，DeepSeek deepseek-v4-flash）

见 `results/p0_round1.json`。统一复跑：**8/8 PASS，收口硬门槛全部达标**。

| 指标 | 结果 | 门槛 |
| --- | --- | --- |
| Raw E2E Rate | 1.0 | - |
| Runtime Capability Rate | 1.0 | 8/8 |
| Provider Error Rate | 0.0 | - |
| Correct Workflow Resume | 1.0 | 100% |
| Completed Workflow Skip | 1.0 | 100% |
| Duplicate Side Effect | 0.0 | 0% |
| Unsafe Resume Acceptance | 0.0 | 0% |
| Artifact Integrity | 1.0 | 100% |
| Process-Restart Recovery | 1.0 | PASS |

| Case | 结果 | 验证点 |
| --- | --- | --- |
| C01 基线 | ✅ | 3 文件存在、solution.py 真实运行 |
| C02 跨 Workflow 恢复 | ✅ | A 不重跑、B/C 各 1、Duplicate=0 |
| C03 EXACT resume | ✅ | read_spec 不重跑、spec_content 水合 |
| C04 Stage Replay | ✅ | REPLAY_FROM_STAGE、只重放 gen_code |
| C05 只恢复 C | ✅ | A/B exec=0、hash 不变、二次调用无执行 |
| C06 上游篡改阻断 | ✅ | UPSTREAM_ARTIFACT_CHANGED、B Provider=0 |
| C07 副作用窗口崩溃 | ✅ | 对账 COMMITTED、文件 hash 不变、B LLM 不重调 |
| C08 身份校验 | ✅ | RUN_MISMATCH / REQUIRE_CLARIFICATION、revision 不变 |

P1 的 C09–C12（版本不兼容 / Provider 超时 / B 恢复失败不影响 A / 真正二次进程启动）
可作为增强证据；跨进程恢复能力已由 C02/C03/C05/C07 覆盖，不必为凑 case 重复建设。

## 修复后离线回归（C03/C07，历史记录）

针对上述两个真实 Runtime 缺口已完成最小修复，未修改 Planner 或 Workflow 拓扑：

1. **C03 Artifact Hydration**：恢复时按 checkpoint 的 `reference + digest` 水合
   file-backed Artifact；digest 不匹配或文件缺失时 fail-closed。恢复路径不再把
   `required_outputs` 缺失静默当作“已完成”，并增加 terminal output completion gate，
   只有真实终态产物存在且 checkpoint/verifier 成功时才允许 Run index 完成。
2. **C07 崩溃窗口恢复**：当 Run index 尚未写入 `active_checkpoint_id` 时，按
   `(run_id, workflow_id, activation_attempt_id)` 从 CheckpointStore 回退到最新合法
   checkpoint；对已落盘且内容匹配的 write/modify 副作用进行确定性 COMMITTED reconcile，
   不再次调用 LLM 或覆盖文件；内容不一致时阻塞恢复。

离线预检命令：

```bash
PYTHONPATH=. python -B benchmarks/v22c/offline_dryrun.py
```

修复后结果：

| Case | 结果 | 关键证据 |
| --- | --- | --- |
| C02 | ✅ PASS | 跨进程恢复 B→C，A 不重跑，副作用不重复 |
| C03 | ✅ PASS | `RESUME_EXACT`，read_spec 不重跑，B/C 产物真实落盘，Run 完成 |
| C07 | ✅ PASS | 写盘后崩溃可从最新 checkpoint 恢复，B 的 LLM/写入不重跑 |

该段记录的是 Provider 暂不可达期间的离线收口证据。随后 P0 全量真实 API 复跑已完成，
详见 `results/p0_round1.json`：C01–C08 为 **8/8 PASS**，Provider Error Rate 为 0，
并满足全部 v2.2C P0 硬门槛。当前 v2.2C 以 P0 统一报告作为正式收口快照；C09–C12
保留为 post-close hardening backlog，不阻塞本里程碑。
