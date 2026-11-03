# v2.4C-2b Workflow Baseline Attribution Audit

## Mechanical baseline

```text
Evaluated HEAD              a040c48aab4b0abb2fcdb31beb902a5758c0e5c8
Capability                  22/24 (91.7%)
Schema validity             100%
Workflow accuracy           100%
Safe-decision rate          100%
False Workflow Selection    0
Unsafe reuse                0
Provider/Contract/Oracle/
Integration failures        0 / 0 / 0 / 0
```

All 24 calls used the same DeepSeek Provider. The observed
`STRUCTURED_TO_RAW_FALLBACK` path is a format fallback, not a Provider
fallback.

## Residual anatomy

### C002 — free-text binding span

The Selector correctly chose `research_report`, preserved
`output/wal.md`, and produced a safe complete binding. The only difference is:

```text
Oracle topic     SQLite WAL
Actual topic     SQLite WAL 的最新官方资料
```

These values are semantically equivalent for the free-text `topic` binding.
The mechanical `P-CAP:ARGUMENT_BINDING` label is retained for historical
comparability, while closeout attribution is
`P-MEASUREMENT:FREE_TEXT_BINDING_SPAN`.

### C019 — completed Workflow decline/ask boundary

The Runtime projection says `data_cleanup` is completed and not reusable. The
Selector did not instantiate or reuse it; it safely chose `ask`, while the
frozen Oracle expects `decline`.

This is a contract boundary between two non-executing decisions, not a False
Workflow Selection. Closeout attribution is
`P-CON:COMPLETED_WORKFLOW_DECLINE_ASK_BOUNDARY`.

## Decision

No systemic production Selector gap is proven, and no prompt change is
justified. The frozen 22/24 mechanical score is not rewritten. The next step is
Runtime integration, where `WORKFLOW_INSTANTIATION_BOUNDARY_HARDCODED` remains
an explicit watchlist item; WorkflowExecutor and Resume policy remain frozen.
