# v2.3D Cancellation / Timeout Final Freeze

Date: 2026-08-11

Implementation baseline: `7fcb5bb4`

Commit: `v2.3D-4c: expose cancellation contract to desktop client`

## Final status

**PASS — Runtime correctness and cancellation safety are frozen.**

The closeout does not add a second cancellation state machine, real LocalAgentService
transport, FastAPI, WebSocket, Pause, Approval, or a new timeout policy.

## Milestone evidence

| Area | Result |
| --- | --- |
| D1 Contract / Dataset / Oracle | 16/16 PASS |
| D2 Durable Cancellation Core | PASS |
| D3 Propagation / Side-effect Safety | PASS |
| D4a CLI Cancellation Control | PASS |
| D4b Real Provider / Timeout E2E | Runtime Correctness 10/10 |
| D4c Desktop Cancellation Contract | PASS |

Frozen Dataset hash:

```text
090adaf6a972f812e11990fe2b04e7736e1d74455e34ddcb10ad7c12bd55654c
```

## D4b real evidence

Source: [`d4b_clean_acceptance.json`](../results/d4b_clean_acceptance.json)

- D401 real Ollama cancellation: PASS
- D408 real Ollama Run timeout: PASS
- D407 real child-process SIGKILL recovery: PASS
- D409 Provider timeout contrast: `PROVIDER_ERROR`, Runtime Correctness PASS
- Case result: 9 PASS + 1 PROVIDER_ERROR
- Runtime Correctness: 10/10
- Automatic rerun: disabled
- Hard invariant violations: 0

D409 remains an explicit Provider infrastructure result. It is not relabeled as a
successful capability case and does not reopen the Cancellation Runtime.

## D4c evidence

- Public `cancelRun()` request/response contract is part of `AgentServiceClient`.
- Desktop state is authoritative-Snapshot driven:

  ```text
  ACTIVE → CANCELLING → CANCELLED / TIMED_OUT
  ```

- The Cancel action is available only for `ACTIVE` Runs.
- `CANCELLING` is rendered as a non-terminal pending state.
- Verified partial artifacts and queued/interrupted tasks remain visible after interruption.
- Disconnect, unmount, and event replay do not implicitly call `cancelRun()`.
- Desktop source has no direct Runtime, SQLite, Orchestrator, or process-global workspace import.
- `npm run build`: PASS.

## Hard invariants

All v2.3D hard invariants are zero:

| Invariant | Violations |
| --- | ---: |
| Lost cancellation intent | 0 |
| Post-cancel new side effect | 0 |
| Pending task started after cancel | 0 |
| Duplicate cancellation transition | 0 |
| Duplicate side effect | 0 |
| False `CANCELLED` | 0 |
| False `COMPLETED` after cancellation | 0 |
| Committed effect/artifact lost | 0 |
| Cancelled Run normal-resumed | 0 |
| Stale writer accepted | 0 |
| Atomic transaction torn | 0 |
| Run timeout reported `COMPLETED` | 0 |
| Provider timeout promoted to `RUN_TIMEOUT` | 0 |
| Snapshot / terminal-event mismatch | 0 |
| Cancel-triggered Provider fallback | 0 |
| Client disconnect implicit cancellation | 0 |
| Frontend optimistic `CANCELLED` | 0 |

## Local verification

- `python -B -m benchmarks.v23d.validate`: PASS, 16 cases, hash above.
- `pytest -q tests/test_v23d_*.py tests/test_architecture_verification.py`: 50 passed.
- `mypy agent/interruption benchmarks/v23d`: PASS, 15 files.
- `npm run build` in `apps/desktop`: PASS.
- Full pytest: `553 passed, 17 skipped, 1 environment failure`.

The single full-suite failure is `test_web_fetch`, caused by sandbox DNS inability to
resolve `example.com`; it is retained as an environment failure and does not exercise
v2.3D cancellation. A full-repository mypy run currently reports 146 existing errors in
26 files outside the v2.3D scoped check; it is not represented as a global PASS.

## Deferred / next milestone

- Real `LocalAgentServiceClient` and Python sidecar integration.
- FastAPI / WebSocket transport adapters.
- Provider infrastructure recovery for D409.
- Full-repository mypy cleanup.

The next product line is Desktop Real Integration, not another v2.3D Runtime slice.
