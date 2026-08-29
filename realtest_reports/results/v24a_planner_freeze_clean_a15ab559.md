# v2.4A-2d Real Planner Re-baseline

本报告只测 v1.1 calibrated view 中的 Planner-owned cases。Chat/Routing 与 Uncertainty 独立统计，不进入 Planner capability 分母。

- Harness: `v2.4A-2d-real-planner-rebaseline-v2`
- HEAD: `a15ab559e276db5bf1c6326179efa86e9d7768cb`
- Dataset: `v2.4A-planner-v1.1`
- Dataset hash: `8c268b5855d109c7a2be940257ae0acf7edc877793dd5914cc020ae380aae023`
- Planner cases: **46**
- Provider: `deepseek` / `deepseek-v4-flash`
- Automatic case retry: **false**
- Provider fallback: **false**
- Source raw report: `this report`

## Summary

| Metric | Value |
| --- | ---: |
| Cases submitted | 46 |
| Evaluable cases | 45 |
| Capability pass | 39/45 (86.7%) |
| Raw case pass | 39/46 (84.8%) |
| Schema validity | 100.0% |
| Dependency validity | 100.0% |
| Executable plan rate | 86.7% |
| Missing task rate | 8.2% |
| Overplanning rate | 35.6% |
| Clarification accuracy | 100.0% |
| Unnecessary planning rate | 0.0% |
| Average / P95 tasks | 2.16 / 4 |
| Average / P95 latency | 9146ms / 25505.635 |

## Provider and format paths

- Provider path counts: `{"NOT_CALLED": 8, "SINGLE_PROVIDER": 38}`
- Format path counts: `{"NOT_CALLED": 8, "RAW_ONLY": 37, "STRUCTURED_TO_RAW_FALLBACK": 1}`
- Cross-provider fallback cases: `none`
- Structured-to-raw cases: `PA005`

## Continuation context

P12 cases receive a narrow `PlannerContext` projection derived from the durable-state fixture; the raw Dataset case and golden plan are not passed to production Planner.
- Projection cases: `PA046, PA047, PA048, PA049, PA050`

## Failure separation

- Provider/API failures: **1**
- Contract/Oracle failures: **0**
- Runtime/integration failures: **0**

| Attribution | Cases |
| --- | ---: |
| P-CAP | 6 |
| P-PROV | 1 |

## Failure clusters

| Cluster | Count |
| --- | ---: |
| P-CAP:UNDER_PLAN | 6 |
| P-PROV:INTERNAL | 1 |

## Case results

| Case | Mode | Provider status | Provider path | Format path | Context | Pass | Tasks | Latency | Failure |
| --- | --- | --- | --- | --- | :---: | :---: | ---: | ---: | --- |
| PA005 | plan | SUCCESS_WITH_FORMAT_FALLBACK | SINGLE_PROVIDER | STRUCTURED_TO_RAW_FALLBACK | no | ✅ | 2 | 23436ms | — |
| PA006 | plan | SUCCESS | SINGLE_PROVIDER | RAW_ONLY | no | ✅ | 2 | 4302ms | — |
| PA007 | plan | SUCCESS | SINGLE_PROVIDER | RAW_ONLY | no | ✅ | 1 | 21645ms | — |
| PA008 | plan | SUCCESS | SINGLE_PROVIDER | RAW_ONLY | no | ✅ | 1 | 3525ms | — |
| PA009 | plan | SUCCESS | SINGLE_PROVIDER | RAW_ONLY | no | ✅ | 4 | 18481ms | — |
| PA010 | plan | SUCCESS | SINGLE_PROVIDER | RAW_ONLY | no | ✅ | 5 | 16082ms | — |
| PA011 | plan | SUCCESS | SINGLE_PROVIDER | RAW_ONLY | no | ✅ | 5 | 29169ms | — |
| PA012 | plan | PROVIDER_ERROR | SINGLE_PROVIDER | RAW_ONLY | no | ❌ | — | 45007ms | P-PROV:INTERNAL |
| PA013 | plan | SUCCESS | SINGLE_PROVIDER | RAW_ONLY | no | ✅ | 4 | 20187ms | — |
| PA014 | plan | SUCCESS | SINGLE_PROVIDER | RAW_ONLY | no | ✅ | 4 | 25506ms | — |
| PA015 | plan | SUCCESS | SINGLE_PROVIDER | RAW_ONLY | no | ✅ | 4 | 18034ms | — |
| PA016 | plan | SUCCESS | SINGLE_PROVIDER | RAW_ONLY | no | ✅ | 4 | 20733ms | — |
| PA017 | plan | SUCCESS | SINGLE_PROVIDER | RAW_ONLY | no | ✅ | 2 | 13651ms | — |
| PA018 | plan | SUCCESS | SINGLE_PROVIDER | RAW_ONLY | no | ✅ | 2 | 15992ms | — |
| PA019 | plan | SUCCESS | SINGLE_PROVIDER | RAW_ONLY | no | ✅ | 3 | 4027ms | — |
| PA020 | plan | SUCCESS | SINGLE_PROVIDER | RAW_ONLY | no | ✅ | 3 | 5203ms | — |
| PA021 | plan | SUCCESS | SINGLE_PROVIDER | RAW_ONLY | no | ❌ | 2 | 4212ms | P-CAP:UNDER_PLAN |
| PA022 | plan | SUCCESS | SINGLE_PROVIDER | RAW_ONLY | no | ❌ | 2 | 5336ms | P-CAP:UNDER_PLAN |
| PA023 | plan | SUCCESS | SINGLE_PROVIDER | RAW_ONLY | no | ❌ | 2 | 7154ms | P-CAP:UNDER_PLAN |
| PA024 | plan | SUCCESS | SINGLE_PROVIDER | RAW_ONLY | no | ✅ | 3 | 8559ms | — |
| PA025 | plan | SUCCESS | SINGLE_PROVIDER | RAW_ONLY | no | ✅ | 2 | 4201ms | — |
| PA026 | plan | SUCCESS | SINGLE_PROVIDER | RAW_ONLY | no | ✅ | 2 | 2803ms | — |
| PA027 | plan | SUCCESS | SINGLE_PROVIDER | RAW_ONLY | no | ✅ | 2 | 4540ms | — |
| PA028 | plan | SUCCESS | SINGLE_PROVIDER | RAW_ONLY | no | ✅ | 2 | 6099ms | — |
| PA029 | plan | SUCCESS | SINGLE_PROVIDER | RAW_ONLY | no | ❌ | 3 | 18627ms | P-CAP:UNDER_PLAN |
| PA030 | plan | SUCCESS | SINGLE_PROVIDER | RAW_ONLY | no | ❌ | 4 | 12649ms | P-CAP:UNDER_PLAN |
| PA031 | plan | SUCCESS | SINGLE_PROVIDER | RAW_ONLY | no | ✅ | 3 | 8569ms | — |
| PA032 | plan | SUCCESS | SINGLE_PROVIDER | RAW_ONLY | no | ✅ | 3 | 15264ms | — |
| PA033 | abstain | NOT_CALLED | NOT_CALLED | NOT_CALLED | no | ✅ | 0 | 0ms | — |
| PA034 | abstain | NOT_CALLED | NOT_CALLED | NOT_CALLED | no | ✅ | 0 | 0ms | — |
| PA035 | abstain | NOT_CALLED | NOT_CALLED | NOT_CALLED | no | ✅ | 0 | 0ms | — |
| PA036 | abstain | NOT_CALLED | NOT_CALLED | NOT_CALLED | no | ✅ | 0 | 0ms | — |
| PA037 | abstain | NOT_CALLED | NOT_CALLED | NOT_CALLED | no | ✅ | 0 | 0ms | — |
| PA038 | abstain | NOT_CALLED | NOT_CALLED | NOT_CALLED | no | ✅ | 0 | 0ms | — |
| PA039 | abstain | NOT_CALLED | NOT_CALLED | NOT_CALLED | no | ✅ | 0 | 0ms | — |
| PA040 | abstain | NOT_CALLED | NOT_CALLED | NOT_CALLED | no | ✅ | 0 | 0ms | — |
| PA041 | plan | SUCCESS | SINGLE_PROVIDER | RAW_ONLY | no | ✅ | 3 | 3971ms | — |
| PA042 | plan | SUCCESS | SINGLE_PROVIDER | RAW_ONLY | no | ❌ | 3 | 8028ms | P-CAP:UNDER_PLAN |
| PA043 | plan | SUCCESS | SINGLE_PROVIDER | RAW_ONLY | no | ✅ | 3 | 3884ms | — |
| PA044 | plan | SUCCESS | SINGLE_PROVIDER | RAW_ONLY | no | ✅ | 2 | 1679ms | — |
| PA045 | plan | SUCCESS | SINGLE_PROVIDER | RAW_ONLY | no | ✅ | 3 | 4779ms | — |
| PA046 | plan | SUCCESS | SINGLE_PROVIDER | RAW_ONLY | yes | ✅ | 1 | 3138ms | — |
| PA047 | plan | SUCCESS | SINGLE_PROVIDER | RAW_ONLY | yes | ✅ | 1 | 2412ms | — |
| PA048 | plan | SUCCESS | SINGLE_PROVIDER | RAW_ONLY | yes | ✅ | 1 | 2184ms | — |
| PA049 | plan | SUCCESS | SINGLE_PROVIDER | RAW_ONLY | yes | ✅ | 3 | 2906ms | — |
| PA050 | plan | SUCCESS | SINGLE_PROVIDER | RAW_ONLY | yes | ✅ | 1 | 4749ms | — |

## Interpretation rules

- Provider/API, contract/oracle, and runtime failures are reported separately from Planner capability.
- Each selected case is submitted once; internal production fallback calls, if any, are recorded as evidence and are not altered.
- Provider selection and response-format fallback are reported as separate evidence dimensions.
- P12 continuation cases receive only the Runtime-projected completed/remaining task scope.
- Deterministic pre-Planner abstentions are classified as P-UNCERTAINTY and excluded from the Planner capability denominator.
- No golden plan, case-specific correction, or JSON repair is used in the Planner score.
- A new capability score requires this real run; v1.0 results are not re-scored in place.
