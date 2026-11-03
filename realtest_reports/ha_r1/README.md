# HA-R1：下一轮人工验收入口

当前结论：代码修复和本地基础验证完成，可以开始新的人工验收。尚未证明真实 Provider 的自然语言判断、联网检索质量或全部业务任务成功。

## 启动

在终端运行：

```bash
cd /Users/tsxg/Desktop/TSAgent
bash realtest_reports/ha_r1/start_human.sh
```

启动器使用本项目 `.venv/bin/python`，默认显式选择 DeepSeek，创建新的 `/private/tmp/tsagent-human-时间.sqlite`。拒绝 `auto`。

本机没有可用的 Docker 执行环境。此人工验收入口显式设置 `TSAGENT_ALLOW_LOCAL_EXECUTION=1`，让你输入的命令在本机工作区执行。生产默认的本地执行开关没有改变；如果直接运行 main.py 而不设置此变量，命令执行可能因缺少隔离环境而阻塞。

测试 Ollama 时先显式设置：

```bash
export TSAGENT_LLM_PROVIDER=ollama
export OLLAMA_MODEL=qwen2.5:14b
bash realtest_reports/ha_r1/start_human.sh
```

密钥继续使用根目录 `.env` 中的 `OPENAI_API_KEY`；不放入报告或 Git。

## 四轮连续验收

逐条输入，等待上一轮结束，保持同一个进程和 session：

1. 今天有什么大新闻？
2. 这不够大，给我挑三件真正重要的并总结。
3. 帮我写个汇率转换计算器，写完告诉我路径。
4. 再给它加一个 HKD → JPY 的例子。

分别记录 Run ID、状态、真实输出、来源/文件路径。第 3 轮后独立检查并运行实际产物；第 4 轮核验同一文件发生了正确修改，原换算功能仍可使用。

另外复测：纯回答、`执行 date 命令，并把真实 stdout 原样贴出来。`、显式路径写入后运行、读代码生成报告、列目录、无搜索能力时阻塞、无邮件能力时阻塞。如上一轮没有完成，可直接纠正“不是应该直接在本地做吗”，核验仍在完成原目标。

只有实际任务与产物满足要求才算通过。`COMPLETED`、非空回答或一次工具调用都不能单独证明完成。Provider 不可达单独记为 Provider Error。请保留失败时已产生的文件及相关证据。

## 已完成的修复

- 移除通用关键词意图分发表及独立的同步 LLM 引用消歧，统一结构化语义理解；话题分类不能抹掉交付要求。
- 普通请求读取同一 tenant/session 最近四个终态 Run 的目标、缺失项、真实观察、输出与产物；独立于 Memory，可在 Service 重启后续接。
- 创建或修改文件的要求保留到最终验证。文件须有实际执行和当前文件系统证据；最终产物路径由 workspace 生成，不接受模型编造链接。失败时披露已完成文件。
- 保留原始请求的路径权限；默认新文件只可写入 output/，续接使用已验证产物路径。
- 动态投影 TSAgent 产品身份与注册能力；研究回答从工具观察综合，证据不足不凑数。
- 修复路径先于“写入”时的目标绑定；编译器不再修改命令正文中的引号与反斜杠；输出链路保留完整 stdout 与空白，非零退出作为真实失败。
- 完成条件缺证据时保留底层失败原因，避免覆盖 Provider/执行环境错误。

## 本地证据

- `offline-tests.log` / `offline-results.xml`：272 项通过。测试对模型与搜索使用固定响应，真正执行 Runtime、Compiler、Executor、Service、SQLite、文件读写及本地进程。四轮连续测试还重新运行生成的计算器并核验 HKD → JPY 输出。
- `mypy.log`：27 个修改后的生产文件静态检查通过。
- `cli-local-date.log`：真实 CLI 调用 date，展示实际输出。
- `cli-write-execute.log` / `cli-artifact-probe.json`：真实 CLI 写入并运行探针；另起 Python 进程从实际文件核验 stdout 为 HUMAN-PROBE-OK。
- `environment.json`：项目 Python 环境版本。已实际完成本地 embedding 初始化、Chroma 摘要写入与读取，无 ABI 异常。
- `human-feedback.md`：本轮修复依据。带 initial 的日志及最初 cli-date.log 保留排查过程中的失败，不是最终验证结果。

复跑离线集：

```bash
.venv/bin/python realtest_reports/ha_r1/run_offline.py
```

模型/搜索桩验证的是执行合同与数据通路，不证明 DeepSeek/Ollama 的真实能力。真实新闻来源、事实准确性、重要性排序和开放式代码生成质量，需要你下一轮人工测试确认。本轮没有将真实 API 成功作为交付依据。

## 环境说明

项目 `.venv` 基于现有 Python 3.12 的 system-site-packages 创建，仅在项目中覆盖 NumPy 1.26.4、SciPy 1.13.1、scikit-learn 1.5.2。原系统 Python 没有修改。它解决本机已有 pandas/扩展与 NumPy 2 的 ABI 冲突；不是一个可在任意机器重建的完整依赖锁文件。

代码、脚本、测试、报告和探针均保持本地，本轮没有 commit 或 push。
