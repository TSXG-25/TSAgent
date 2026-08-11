# Desktop-4c MVP-2 Real Local Integration Evidence

- HEAD: `434d4882298f24b108894418169286b658b0bc75`
- Mode: local
- Dataset hash: `bda9f690fff05f4e947d7088c4c95d414cab5b220f5c2082a4985f10a04d4185`
- Harness: injected Tauri bridge + real Python JSONL sidecar
- Provider configuration: configured-primary-with-ollama-fallback:qwen2.5:14b
- Manual Tauri/Rust shell smoke: DEFERRED (no Tauri host is present in this repository)

This report distinguishes capability outcome from Runtime correctness. Provider-unavailable cases are retained as evidence and are not counted as capability PASS. The harness does not read SQLite or workspace paths from the desktop process.

| Case | Result | Runtime | Capability | Terminal |
| --- | --- | --- | --- | --- |
| DT01 | PASS | PASS | N/A | — |
| DT02 | PROVIDER_ERROR | DEFERRED | DEFERRED | active |
| DT03 | PROVIDER_ERROR | DEFERRED | DEFERRED | active |
| DT04 | PROVIDER_ERROR | DEFERRED | DEFERRED | — |
| DT05 | DEFERRED | DEFERRED | DEFERRED | — |
| DT06 | PROVIDER_ERROR | DEFERRED | DEFERRED | — |
| DT07 | DEFERRED | DEFERRED | DEFERRED | — |
| DT08 | PROVIDER_ERROR | DEFERRED | DEFERRED | active |
| DT09 | PASS | PASS | N/A | — |

## Hard-gate summary

`false_completed`, duplicate UI events, event gaps, direct SQLite access, implicit Mock fallback, and terminal Snapshot/Event mismatch are evaluated per case in the JSON report.
