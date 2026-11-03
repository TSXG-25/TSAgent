# v2.1B Conversation Runtime 真实路径验证

本目录归档 v2.1B 最终 `CONTINUE_REFERENCE` 真实 API 复测证据。

## Run provenance

- Provider: DeepSeek
- Run completed: 2026-08-06T09:56:07+0800
- Benchmark: `conversation-continuation`
- Benchmark version: `v0.3`
- Cases: 5 (`reference_resume`)
- Elapsed: 1.6 min
- Dataset hash: `92556c5edd0ea6a413891988d808626c62bbd229df76ad15f183a31934919f80`
- Fixture manifest hash: `e4145b18a5503373f68be06ce0d15787aca5ac56f89e3ab3cba124c7ae040223`
- Archived report SHA-256: `4a9906c8aece1775436121d7431658eecf458451c7bd84c94ba8544ccb58e9fe`

## Three-way result

| Metric | Result |
| --- | --- |
| Raw E2E | 5/5 = 100% |
| Capability rate | 5/5 = 100% |
| Provider error | 0/5 = 0% |
| Benchmark invalid | 0 |

## Root-cause closure

The progression was:

```text
0/5
→ Reference Resolver ordinal branch no longer intercepts “继续刚才那个函数”
→ Runtime Evidence preserves and validates resolved_symbol
→ 5/5
```

The result must not be attributed only to fixture materialization. The fixture
preflight was also validated (`benchmark_invalid=0`), but the final recovery
required both runtime fixes above.

The complete JSON report is [reference_after_evidence_fix.json](./reference_after_evidence_fix.json).
