# HA-R3 文件效果与 Artifact 链路修复

日期：2026-09-09。基准为本地工作区，保留用户已有修改；没有真实 Provider/API 复跑，没有提交或推送。

## 已修复

- 文件产物来自执行器实际效果的规范路径与 SHA-256，不再根据 Task verb/target/status 推测。
- 命令生成器通过 Task.output_paths 声明预期文件或目录；执行前后变化经验证后进入 Artifact。只有退出码成功、或者原有文件存在，不足以证明产物交付。
- 同一任务前面写入成功、后面步骤失败，已验证文件仍被披露和发布。
- 复制/移动使用真实目标路径；已验证的移动/删除会撤销旧文件记录。
- Finalizer 和 Service 发布前核验摘要；发现文件被改动/删除时不能继续声称成功。
- 移除 ExecutionStage 基于 Task.target 的重复文件校验，文件效果校验归 ExecutionVerifier。

## 证据

最终结果：**391 项离线回归通过**，30 个生产文件按项目配置静态检查通过；compileall 与 git diff --check 通过。模型和搜索使用受控边界，不将结果表述为真实 Provider 验收。

`offline-tests.log` 和 `offline-results.xml` 保存最终回归。`mypy.log` 使用项目 mypy.ini 的检查口径。新测试 `tests/test_ha_r3_file_evidence.py` 共 11 项，包含真实本地子进程、多文件生成与完整 Service/SQLite 发布，以及终态前篡改文件。

新增纳入的旧意图测试原先依赖真实 Provider；已改为显式提供模型判定，仅测试确定性要求不能被抹除。这避免把 Provider 不可达导致的 requires_execution=True 误当作意图回归通过。

```bash
cd /Users/tsxg/Desktop/TSAgent
.venv/bin/python realtest_reports/ha_r3/run_offline.py
```

历史归档说明：首轮误复用了 HA-R2 runner。HA-R2 的原始控制台日志已恢复，新结果移到本目录的 initial-*；原 HA-R2 XML 已被覆盖，不能作为原轮证据。说明见 ../ha_r2/xml-archive-note.txt。没有把新结果伪装为旧结果。

## 验收边界

本轮只证明受管文件操作和**已声明输出**的命令链路。任意脚本的未声明文件不会自动扫描发现；与外部写入者并发时，声明目录的变化不能当成操作系统级因果归属；发布后文件仍可能改变。真实模型是否完整声明产物、实际脚本能否满足业务目标，仍需人工测试。

新一轮仍可运行 `bash realtest_reports/ha_r1/start_human.sh`，重点测试：生成两份报告、脚本生成 dist/ 多个文件、生成一个文件后故意失败、生成后移动文件。核对实际文件、返回路径、Artifact 数量/摘要、stdout、终态与失败后的保留文件。纯回答继续不要求工具。

session 原始历史串用、summary 来源失真、repository 全局索引、prompt trust、静默上下文退化等尚未修复，整体 Human Acceptance 仍未通过。完整修复队列见 ../../docs/exec-plans/goal-contract-audit.md。
