# Desktop-4c MVP-2 Real Local Integration Evidence

- HEAD: `14a79f8a`
- Mode: local
- Dataset hash: `bda9f690fff05f4e947d7088c4c95d414cab5b220f5c2082a4985f10a04d4185`
- Harness: injected Tauri bridge + real Python JSONL sidecar
- Provider configuration: ollama:qwen2.5:14b (test-forced-no-fallback)
- Status: `PARTIALLY_VERIFIED`
- Hard invariant violations: `0`
- Manual Tauri/Rust shell smoke: DEFERRED (no Tauri host is present in this repository)

This report distinguishes capability outcome from Runtime correctness. Provider-unavailable cases are retained as evidence and are not counted as capability PASS. The harness does not read SQLite or workspace paths from the desktop process.

| Case | Result | Runtime | Capability | Terminal |
| --- | --- | --- | --- | --- |
| DT01 | PASS | PASS | N/A | — |
| DT02 | PROVIDER_ERROR | PASS | DEFERRED | failed |
| DT03 | DEFERRED | DEFERRED | DEFERRED | failed |
| DT04 | PASS | PASS | PARTIAL | failed |
| DT05 | DEFERRED | DEFERRED | DEFERRED | — |
| DT06 | PASS | PASS | PARTIAL | cancelled |
| DT07 | DEFERRED | DEFERRED | DEFERRED | — |
| DT08 | PASS | PASS | PASS | timed_out |
| DT09 | PASS | PASS | N/A | — |

## Hard-gate summary

`false_completed`, duplicate UI events, event gaps, direct SQLite access, implicit Mock fallback, and terminal Snapshot/Event mismatch are evaluated per case in the JSON report.

DT02 reached a durable `run_failed` terminal event with `RUNTIME_EXCEPTION`; its capability
outcome is recorded as `FAIL`, while the terminal/event safety checks remain `PASS`. DT03 is
`DEFERRED` because it depends on a completed DT02 artifact. DT05 remains `DEFERRED` because
this repository has no production-side deterministic launcher injection point for constructing
a recoverable Run fixture. These are not converted into a Desktop Runtime PASS.
