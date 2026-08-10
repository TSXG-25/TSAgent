# P2 Runtime Endurance & Provider Portability — Freeze Record

- 状态：`Accepted — Implemented and Verified`
- Acceptance baseline：`8fec07c9d0758aa68bfacf8c67d2830595e9d4e4`
- Freeze date：2026-08-10
- Contract：`adr-0022-v1`
- Full Dataset hash：`f51c5fef7a13d82e20003bf08bcb593b482511477a5c493bdff8df6bfbf9baef`
- Provider manifest hash：`f08d66b97527557e9dc455aa2d35d1bbd56ef0d74f22759557b6b6a7fb2ca3f7`

## 最终结果

| 分组 | Capability | Runtime Correctness |
| --- | ---: | ---: |
| L Long-horizon | 3/5 | 5/5 PASS |
| R Real SIGKILL Recovery | 仅观测 Runtime | 4/4 PASS |
| S Deterministic Soak | 4/4 | 4/4 PASS |
| P Primary / DeepSeek | 3/3 | 3/3 PASS |
| P Secondary / Ollama `qwen2.5:14b` | 2 PASS + 1 PARTIAL | 3/3 PASS |

Secondary P01–P03 在固定 `8fec07c9` 的干净 worktree 中各执行一次，没有自动复跑，
也没有 Provider-specific prompt、fixture 或 Runtime 适配。P02 的多目标任务被评为
`PARTIAL`，但缺失能力被 Runtime 正确表达，因此 Runtime Correctness 仍为 PASS。

双 Provider 三组 pair 均满足：

```text
prompt parity       3/3 PASS
fixture parity      3/3 PASS
pair Runtime        3/3 PASS
fallback count      0
```

本地 Ollama 在正式 round 中出现过 timeout/Provider error，P03 还包含合同预先规定的
malformed structured-response 注入。这些 evidence 均原样保留；没有通过重跑筛选幸运
结果，也没有回退到 DeepSeek。

## 硬门禁

```text
False COMPLETED                    0
Duplicate Side Effect              0
Cross-context Leakage              0
Workspace Leakage                  0
Durable State Loss                 0
Stale Writer Acceptance            0
Completed Workflow Re-execution    0
Event Replay Gap                   0
Terminal Snapshot/Event Mismatch   0
Unsupported Effect Hallucination   0
Implicit Provider Fallback         0
Credential Leakage                 0
```

## 永久证据

- `realtest_reports/results/p2_l_real_postfix_68480d91.json`
- `realtest_reports/results/p2_r1_round1.json`
- `realtest_reports/results/p2_s1_deterministic.json`
- `realtest_reports/results/p2_p_primary_corrected_697ce06d.json`
- `realtest_reports/results/p2_p_secondary_ollama_qwen25_14b_8fec07c9_round1.json`
- `realtest_reports/results/p2_p_dual_closeout_8fec07c9.json`

P2 至此停止扩展。下一个里程碑是 `v2.3D Cancellation / Timeout Contract`。
