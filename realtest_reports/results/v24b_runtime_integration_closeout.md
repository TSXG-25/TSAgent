# v2.4B-4 Runtime Integration Closeout

- Evaluated HEAD: `13885d40702b78fc4c38820dbe1fbf548a386a25`
- Clean checkout: `PASS`
- Related regression: `67 passed`
- Preflight: `READY_FOR_MIXED_E2E`
- Mixed compiled/dynamic execution: `PASS`

## Frozen ownership

```text
Task owner = COMPILED xor DYNAMIC

COMPILED → complete Compiler ExecutionPlan → shared executor path
DYNAMIC  → one NextAction → one-step ExecutionPlan → shared executor path
```

The mixed acceptance executes a two-step compiled Task followed by one dynamic
Tool action in the same Runtime state. The observed order is:

```text
compiled.one → compiled.two → dynamic.probe
```

Compiler calls for the dynamic Task and Selector calls for the compiled Task
are both zero. Both paths use `ExecutorFactory → ToolExecutor → PlanExecutor →
Verifier`; no direct dynamic Tool invocation exists.

## Hard invariants

```text
dual owner Task                         0
owner switch after resolution          0
compiled Task Selector calls           0
dynamic Task Compiler calls            0
dynamic direct Tool invocation         0
effect before selection                0
unauthorized dynamic effect            0
false Task completion                   0
duplicate effect                        0
```

The production Selector state contract is
`v2.4B-selector-state-v2`, hash
`ec6ec4f58a567275cd04f9f87cc0723fdd04a64457d2075208da5786ed90d358`.
Dataset v1 remains bound to its historical `available_tools` projection.

## Evidence boundary

This closeout is deterministic Runtime integration evidence and makes no new
Provider capability claim. Real-provider Selector capability remains covered
by the frozen v2.4B-3 evidence.

The full repository suite is not reported as green: a development-worktree
run was manually interrupted after `349 passed, 17 skipped` while an existing
external slow test produced no output.
