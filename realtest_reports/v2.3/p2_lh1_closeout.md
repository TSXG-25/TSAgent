# P2-LH1 Workspace Boundary Closeout

## Scope

`RunContext.workspace` is now the single path source of truth for the scoped
Runtime execution chain:

```text
RunContext.workspace
  → PlanExecutor filesystem primitives
  → ExecutionVerifier / Completion Gate
  → Workflow Artifact recorder and checkpoint hydration
```

The legacy `tools.filesystem.ROOT` path remains available only through the
explicit compatibility adapter for unscoped callers and old direct tests.

## Commits

- `7d00aee6` — `v2.3P2-LH1a: bind filesystem tools to RunContext workspace`
- `b6e873a5` — `v2.3P2-LH1b: verify artifacts in scoped workspace`
- Final harness compatibility fix is included in the LH1 closeout commit.

## Deterministic evidence

- P2-LH1 workspace boundary tests: 8 passed
- P1/File Ops, H1, Runtime Context, Workspace, AgentService and durable-store
  regressions: 118 passed
- P2 Dataset/Oracle: 16/16 PASS
- P2-L fixture Runtime invariants: 5/5 PASS
- Targeted modules mypy: PASS
- Production architecture gate: no direct `tools.filesystem.ROOT`,
  `Path.cwd()` or `os.getcwd()` in filesystem execution, verifier, artifact
  hydration and validator paths.

## Real L01–L05 attempt after the fix

The harness was first corrected for the new scoped `_exec_tool(...,
workspace=...)` contract; that was a harness-only compatibility defect. The
clean second attempt is recorded at:

`/private/tmp/p2_lh1_real_round2.json`

The Provider was unavailable in this environment (`54` provider errors), so
no successful artifact-producing capability claim is made. The attempt still
confirmed the safety side of the boundary:

- process-root workspace leakage: `0/5`
- false `COMPLETED`: `0/5`
- duplicate side effects: `0/5`
- terminal snapshot/event mismatch: `0/5`
- all cases ended in `FAILED_TERMINAL` with `run_failed`, not false success

L01–L05 capability validation remains deferred until the real Provider is
reachable. This does not reopen P1 or invalidate the deterministic LH1
boundary closeout.
