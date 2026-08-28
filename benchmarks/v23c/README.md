# v2.3C AgentService Contract Dataset

这是 v2.3C-1 的确定性 Contract / Dataset / Oracle 验证层，不调用真实
Provider，也不声称 concrete `AgentService` 已经接入 Runtime。

## 覆盖范围

共 32 例，覆盖：

- 显式 tenant/user/session/run/request identity；
- 空请求拒绝；
- 相同 request digest 的幂等重试与不同 digest 冲突；
- 不同 tenant 下相同 request_id 的隔离；
- start_run 返回已持久化 RunHandle、进程重开后的 Snapshot 和 close 保留 durable Run；
- 公开 DTO canonical round-trip；
- `RunSnapshot` revision、terminal monotonic 和内部模型隔离；
- 事件连续序列、scope 校验、after-sequence replay；
- cursor 过期、terminal replay 和客户端断开不等于取消；
- Artifact 跨 Run scope 拒绝；
- completed/active/错 scope Resume、resume request 幂等和 Coordinator 委派；
- 明确终态事件与终态后的追加阻断；
- Service error 脱敏。

## 运行

```bash
python -B -m benchmarks.v23c.validate
pytest -q tests/test_v23c_service_contract.py
mypy agent/service benchmarks/v23c tests/test_v23c_service_contract.py
```

当前 Dataset hash：

```text
7cf52067c7af4a9217aceb70ef600fb5e79638c4ed77a63dce08f6438c416e6a
```

## 版本边界

- v2.3C-2：实现纯 Python `AgentService` 并接入现有 Runtime；
- v2.3C-3：持久化事件、重连和慢消费者隔离；
- v2.3C-4：少量真实 Provider E2E 与 Service 收口。

本 Dataset 不覆盖 Cancellation、Timeout、Approval、Provider Failover 或
FastAPI 适配器。
