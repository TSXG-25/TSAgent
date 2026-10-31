# HA-R4 Gate A

状态：语义提示已调整，离线传播测试通过；真实 Provider 识别效果未验证，不能宣称 False Completion 已解决。

本轮离线回归：397 passed，2 warnings，82.11 秒；见 offline-tests.log 与 offline-results.xml。compileall 与 git diff --check 通过。下面已知否定问题来自独立审计探针，不在这 397 项通过断言中。

本轮修改 IntentEngine 的单次语义提示：先判断交付形式，再选择 domain；没有路径的工具、网页、脚本、项目和成份报告请求也需要 artifact。没有新增关键词正则或第二分类器。

`tests/test_ha_r4_gate_a.py` 对六条原句使用受控模型响应，故意同时给出 artifact=true / domain=数学 / execution=false，验证真实 Service 会写文件并发布 artifact。该文件只是链路探针，不是计算器、网页或报告交付；测试通过不代表六个业务目标完成，也不代表语言识别通过。

真实语义评测准备：

```sh
.venv/bin/python realtest_reports/ha_r4_gate_a/run_semantic_eval.py --provider deepseek
```

该命令会调用真实 API，本轮未执行。需要项目 .env 中已有密钥或 Ollama 模型环境。只运行 IntentEngine，不执行业务 Task，因此记录 Case ID，不伪造 Run ID。逐例记录 artifact/execution/outcomes、domain/action、耗时、错误和 PASS/FAIL；Provider 错误单独归类。原六句已出现在生产提示中，另有 held-out 变体和负例，不能把提示内样例成绩当泛化结果。每次写独立时间戳 JSON。

## 已证实的剩余问题

离线调用真实 IntentEngine、模型返回 artifact=false / execution=false 后，请求“只在聊天里展示计算器代码，不创建文件”仍得到 artifact=false / execution=true / FILE_MUTATION。原因是旧词法结果被无条件合并，否定语句中的“创建文件”污染了最终语义。这不是 Provider 失败；当前真实评测的该负例预计失败。本轮未加否定词特例遮盖它，后续需要统一语义与显式操作约束的权威边界。

另外，artifact_required 布尔值不表达产物数量，“做两份报告”需要独立验证两份交付。Gate A 识别正确也不证明报告数量、程序功能或 Gate B 正确。

所有脚本、测试与报告保留本地，未提交或上传。
