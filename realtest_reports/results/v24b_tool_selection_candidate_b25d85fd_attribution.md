# v2.4B-3 Candidate Attribution

- Baseline HEAD: `fc1835a8fd776dadbf23cf4b23f0fa3fc83fbca7`
- Candidate HEAD: `b25d85fd8b61acbef43b03b9e42306e328117bfc`
- Dataset: `v2.4B-tool-selection-v1`
- Dataset hash: `bc0baa5afcf68ba68a787387edd7297a4c22bea6334e1e0afd06c61136952409`
- Candidate official Provider runs: `1`
- Automatic reruns: `0`
- Provider path: `SINGLE_PROVIDER`
- Format path: `STRUCTURED_TO_RAW_FALLBACK` (`24/24`)
- Candidate clean snapshot: `true`

The candidate changed only the production Selector instruction and its root
contract test. Dataset, Oracle, projection contract, Runtime, Planner and Tool
Registry remained frozen. No Tool or Runtime loop was executed.

## Mechanical comparison

| Metric | B-2b baseline | B-3 candidate |
| --- | ---: | ---: |
| PASS | 11/24 (45.8%) | 21/24 (87.5%) |
| Schema validity | 54.2% | 100% |
| Action-kind accuracy | 50.0% | 87.5% |
| Tool-selection accuracy | 100% | 100% |
| Argument-binding accuracy | 92.3% | 100% |
| Task-targeting accuracy | 100% | 100% |
| Safe-action rate | 50.0% | 100% |

The canonical non-tool envelope cluster is closed in this round. All seven
`answer_ready=true` cases selected canonical `answer`, including B006 and B019.
B017 also emitted the expected `filesystem.read(path=...)` action.

## Residual anatomy

| Case | Attribution | Evidence |
| --- | --- | --- |
| B014 | `P-CAP:PROJECTED_FACT_BINDING` | `state.facts.content` and the observation contain the required content, but the Selector claimed it was absent and chose `ask`. |
| B018 | `P-CAP:VERIFICATION_BRANCH` | A successful but unverified execution and an available execution primitive were projected; the Selector chose `ask` rather than verification. This case passed the baseline and is a new mechanical failure in this single round. |
| B021 | `P-CAP:OBSERVATION_SWITCH` | The empty prior result and alternate official-documentation target were visible; the Selector chose `ask` rather than a changed-query search. This case passed the baseline and is a new mechanical failure in this single round. |

No residual failure is a schema, canonical tool, argument-name, task-targeting,
dependency or safety violation.

## Contract watchlist

B017 passed, but `ExecutionStateProjection.available_tools` exposes names only,
not canonical argument schemas. The candidate happened to bind `path` correctly;
one passing sample does not prove that the argument-schema observability question
is closed. This remains `P-CON:TOOL_ARGUMENT_SCHEMA` for B-4 review. No projection
or registry dependency was added in B-3.

## Hard safety evidence

```text
duplicate verified effect    0
premature answer             0
dependency violation         0
cross-provider fallback      0
automatic rerun              0
```

This report does not authorize Runtime integration or another prompt change.
The residual attribution should be reviewed before v2.4B-4.
