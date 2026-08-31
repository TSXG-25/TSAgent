# v2.4C Workflow Capability Freeze

## Status

```text
ADR-0031                    ACCEPTED
Integration HEAD            df7bb543161ec1bd83d804e49877cf793a7f66b5
Real baseline HEAD          a040c48aab4b0abb2fcdb31beb902a5758c0e5c8
Status                      PASS
```

v2.4C freezes the production boundary:

```text
Goal + projected Context + AvailableWorkflows
    → instantiate | reuse | decline | ask
```

The Selector does not execute or resume Workflows. `WorkflowExecutor` still
owns Stage iteration and Stage → Task projection; existing Resume policy still
owns exact resume/replay/block decisions.

## Real Provider capability

```text
Mechanical capability       22/24 (91.7%)
Schema validity              100%
Decision-kind accuracy       95.8%
Workflow accuracy            100%
Binding accuracy             95.8%
Safe-decision rate           100%

False Workflow Selection     0
Unsafe reuse                 0
Missed Workflow              0
Provider/Contract/Oracle/
Integration failures         0 / 0 / 0 / 0
```

All calls stayed on DeepSeek. `STRUCTURED_TO_RAW_FALLBACK` is a same-Provider
format path. Automatic retry, Provider fallback, JSON repair, Workflow
execution, and Runtime mutation were disabled.

The frozen mechanical failures remain visible. C002 is a free-text topic span
measurement boundary; C019 is the safe `decline` versus `ask` boundary for an
already completed Workflow. No systemic Selector gap or prompt change is
supported by the evidence.

## Runtime integration

Production integration now consumes Workflow-declared capability metadata and
materializes all selected bindings generically:

```text
Workflow definition metadata
        ↓ projection
WorkflowDecisionSelector
        ↓ instantiate decision
generic binding → Artifact projection
        ↓
existing WorkflowExecutor
```

The old `question_path` / `output_path` construction in `PlannerStage` is gone.
The selected `code_generation` E2E invokes the Selector once and
`WorkflowExecutor` once, with both bindings preserved exactly.

## Clean evidence

```text
Related clean-checkout regression       106 PASS
Preflight blockers                        0
Integration watchlist                     0
WorkflowExecutor rewrite                 no
Resume policy rewrite                    no
```

The real baseline and final integration use identical Selector prompt and
projection hashes. A full repository-suite green claim is intentionally not
made; the freeze claim is bounded to the listed clean related regression and
the frozen real-provider evidence.
