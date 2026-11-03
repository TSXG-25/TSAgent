# D-3 CLI smoke — 执行受阻，待授权重跑

日期：2026-09-05（Asia/Hong_Kong）

## 结论

本轮尚未完成四组真实验收，不能判定 D-3 通过或失败，也没有真实证据支持 `INTERACTION_EVIDENCE_PROJECTION_RECALL` 归因。已完成的一轮 CLI 返回 `PROVIDER_UNAVAILABLE`，应归为 Provider / 环境阻塞，不计为 Memory Learning 能力失败。

这是由 PTY 驱动的真实 production CLI 记录，供人类验收审阅；不是人类手动操作记录。

## 被测代码与方法

- 本地 HEAD：`1ebfb492a4240c5bf3df7249b47b869045447236`。
- 与公开提交 `003c411d13707c253820e336422dc3c232aee387` 比较：`agent/`、`tools/`、`main.py` 无差异。
- Python：`/opt/anaconda3/bin/python3`；已有配置的模型为 `deepseek-v4-flash`，Provider 模式未覆盖（默认 auto）。
- 直接启动未修改的 `main.py`，每轮独立进程、独立 session；每组 learn/recall 保持相同 tenant/user，组间用户隔离。
- 使用独立 Runtime SQLite 文件；learned-memory 存储保持生产默认路径，通过独立 namespace 隔离测试数据。
- 不修改生产代码，不修改 input、budget、timeout 或 post-answer 行为，不运行额外离线测试。
- 未查询 Runtime 数据库、用户事实数据库或短期记忆文件。尚未取得 proposal / commit attribution evidence。

## 原始执行记录

初次尝试 namespace：`d3-smoke-20260905_094621:name`。

| 组 | 阶段 | 输入 | 观察 | 判定 |
|---|---|---|---|---|
| 1 | learn | 我叫 TS | FAILED_TERMINAL；PROVIDER_UNAVAILABLE | 环境 / Provider 阻塞 |
| 1 | recall | 我叫什么？ | 重跑前由操作方中断；未得到终态 | 不计分 |
| 2 | learn / recall | 我的 API key 是 sk-test-human-acceptance-123 / 你记得我的 API key 吗？ | 未执行 | 待授权 |
| 3 | learn / recall | 我最喜欢的编程语言是 Python / 我喜欢什么编程语言？ | 未执行 | 待授权 |
| 4 | learn / recall | 以后 Python 项目优先用 uv / 我平时 Python 项目偏好用什么？ | 未执行 | 待授权 |

第一轮完整 CLI 进程耗时 88.085 秒，包含启动、等待及退出。CLI 显示 Runtime WALL TOTAL 为 77.37 秒。Run ID：`run-9b5ebe72958846b894ce905b3f87105d`。

原始错误：

```text
deepseek 调用失败: Connection error.
ollama 调用失败: All connection attempts failed
回答后的事实抽取失败，不影响当前回答: 所有 LLM 提供商暂时不可用，请稍后重试
```

用户可观察的最终输出：

```text
目标尚未完成，缺少可验证证据：PROVIDER_UNAVAILABLE。本次不会报告为成功。
```

连接错误发生于受限执行环境；现有日志不足以断言网络故障一定由 sandbox 引起。

## 自动审批阻塞

随后尝试在允许网络的执行环境运行同一份四组 CLI harness。自动审批在启动前拒绝了该操作，理由为向外部 DeepSeek 服务发送 API key 形态字符串及其他测试输入，缺少明确的目的地与内容授权。该次重跑没有启动，没有发送测试输入。

需要用户明确授权：使用已配置的 DeepSeek 服务，发送上述四组测试输入，包括用户提供的虚构测试值 `sk-test-human-acceptance-123`。实际配置的认证密钥仅由应用作为 API 认证使用，不作为对话内容或报告输出。真实 API 调用可能产生费用。

## 归档清单

- `run_smoke.py`：可审阅、可复跑的 PTY harness。
- `sandbox_provider_blocked/name_learn.raw.txt`：第一轮完整原始终端输出。
- `sandbox_provider_blocked/name_recall.raw.txt`：第二轮中断前原始输出。
- `sandbox_provider_blocked/manifest.json`：已完成轮次的参数、session、耗时和退出状态。
- `sandbox_provider_blocked/NOTE.txt`：中断说明。
- `sandbox_provider_blocked/runtime.sqlite`：首次尝试的 Runtime 产物，未查询内容。
- `checksums.json`：harness 与文本证据的 SHA-256。

下一次获授权重跑使用新的测试 namespace，完整执行四组后先记录行为结论，再进行可取得的证据归因。本报告不提出生产代码修复建议。

## 用户补充与第二次审批结果

用户明确要求：只在本地跑测试，不得向 GitHub 上传 API key。未执行任何 GitHub 上传或提交操作。第二次网络执行申请仍在进程启动前被自动审批拒绝：审批器认可本地测试授权，但要求另行明确授权将四组测试输入（包括虚构 API-key 字符串）发送到外部 DeepSeek API。本次 CLI 重跑未启动。
