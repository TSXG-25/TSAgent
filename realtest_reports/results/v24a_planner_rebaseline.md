# v2.4A-2d Real Planner Re-baseline

本报告只测 v1.1 calibrated view 中的 Planner-owned cases。Chat/Routing 与 Uncertainty 独立统计，不进入 Planner capability 分母。

- Harness: `v2.4A-2d-real-planner-rebaseline-v1`
- HEAD: `6c1743078d67cf1644c22779f40649fb4b9880d2`
- Dataset: `v2.4A-planner-v1.1`
- Dataset hash: `8c268b5855d109c7a2be940257ae0acf7edc877793dd5914cc020ae380aae023`
- Planner cases: **46**
- Provider: `deepseek` / `deepseek-v4-flash`
- Automatic case retry: **false**
- Provider fallback: **false**

## Summary

| Metric | Value |
| --- | ---: |
| Cases submitted | 46 |
| Evaluable cases | 45 |
| Capability pass | 20/45 (44.4%) |
| Raw case pass | 20/46 (43.5%) |
| Schema validity | 88.9% |
| Dependency validity | 88.9% |
| Executable plan rate | 44.4% |
| Missing task rate | 33.3% |
| Overplanning rate | 64.4% |
| Clarification accuracy | 88.9% |
| Unnecessary planning rate | 50.0% |
| Average / P95 tasks | 2.64 / 6 |
| Average / P95 latency | 13234ms / 41594.133 |

## Failure separation

- Provider/API failures: **1**
- Contract/Oracle failures: **0**
- Runtime/integration failures: **0**

| Attribution | Cases |
| --- | ---: |
| P-CAP | 25 |
| P-PROV | 1 |

## Failure clusters

| Cluster | Count |
| --- | ---: |
| P-CAP:FALSE_CLARIFICATION | 1 |
| P-CAP:MISSED_CLARIFICATION | 4 |
| P-CAP:UNDER_PLAN | 20 |
| P-PROV:INTERNAL | 1 |

## Case results

| Case | Mode | Provider | Pass | Tasks | Latency | Failure |
| --- | --- | --- | :---: | ---: | ---: | --- |
| PA005 | plan | SUCCESS_WITH_PROVIDER_FALLBACK | ✅ | 3 | 22317ms | — |
| PA006 | plan | SUCCESS | ✅ | 3 | 9632ms | — |
| PA007 | plan | SUCCESS | ✅ | 1 | 7166ms | — |
| PA008 | plan | SUCCESS | ✅ | 3 | 15537ms | — |
| PA009 | plan | SUCCESS | ❌ | 5 | 24050ms | P-CAP:UNDER_PLAN |
| PA010 | plan | SUCCESS | ❌ | 5 | 22499ms | P-CAP:UNDER_PLAN |
| PA011 | plan | SUCCESS | ✅ | 7 | 11280ms | — |
| PA012 | plan | SUCCESS | ❌ | 6 | 41594ms | P-CAP:UNDER_PLAN |
| PA013 | plan | PROVIDER_ERROR | ❌ | — | 45008ms | P-PROV:INTERNAL |
| PA014 | plan | SUCCESS | ❌ | 3 | 18952ms | P-CAP:UNDER_PLAN |
| PA015 | plan | SUCCESS | ❌ | 5 | 39863ms | P-CAP:UNDER_PLAN |
| PA016 | plan | SUCCESS | ❌ | 4 | 13564ms | P-CAP:UNDER_PLAN |
| PA017 | plan | SUCCESS | ❌ | 2 | 6277ms | P-CAP:UNDER_PLAN |
| PA018 | plan | SUCCESS | ❌ | 5 | 19187ms | P-CAP:UNDER_PLAN |
| PA019 | plan | SUCCESS | ✅ | 3 | 6426ms | — |
| PA020 | plan | SUCCESS | ❌ | 3 | 6389ms | P-CAP:UNDER_PLAN |
| PA021 | plan | SUCCESS | ❌ | 2 | 8249ms | P-CAP:UNDER_PLAN |
| PA022 | plan | NOT_CALLED | ❌ | 0 | 0ms | P-CAP:FALSE_CLARIFICATION |
| PA023 | plan | SUCCESS | ❌ | 2 | 5246ms | P-CAP:UNDER_PLAN |
| PA024 | plan | SUCCESS | ❌ | 5 | 22080ms | P-CAP:UNDER_PLAN |
| PA025 | plan | SUCCESS | ✅ | 2 | 6135ms | — |
| PA026 | plan | SUCCESS | ❌ | 2 | 6159ms | P-CAP:UNDER_PLAN |
| PA027 | plan | SUCCESS | ✅ | 2 | 8253ms | — |
| PA028 | plan | SUCCESS | ❌ | 2 | 5686ms | P-CAP:UNDER_PLAN |
| PA029 | plan | SUCCESS | ❌ | 4 | 8304ms | P-CAP:UNDER_PLAN |
| PA030 | plan | SUCCESS | ❌ | 4 | 9802ms | P-CAP:UNDER_PLAN |
| PA031 | plan | SUCCESS | ❌ | 3 | 21131ms | P-CAP:UNDER_PLAN |
| PA032 | plan | SUCCESS | ❌ | 6 | 33789ms | P-CAP:UNDER_PLAN |
| PA033 | abstain | NOT_CALLED | ✅ | 0 | 0ms | — |
| PA034 | abstain | SUCCESS | ❌ | 1 | 6306ms | P-CAP:MISSED_CLARIFICATION |
| PA035 | abstain | NOT_CALLED | ✅ | 0 | 0ms | — |
| PA036 | abstain | SUCCESS | ❌ | 1 | 14953ms | P-CAP:MISSED_CLARIFICATION |
| PA037 | abstain | NOT_CALLED | ✅ | 0 | 0ms | — |
| PA038 | abstain | SUCCESS | ❌ | 1 | 15911ms | P-CAP:MISSED_CLARIFICATION |
| PA039 | abstain | SUCCESS_WITH_PROVIDER_FALLBACK | ❌ | 1 | 47844ms | P-CAP:MISSED_CLARIFICATION |
| PA040 | abstain | NOT_CALLED | ✅ | 0 | 0ms | — |
| PA041 | plan | SUCCESS | ✅ | 3 | 4729ms | — |
| PA042 | plan | SUCCESS | ❌ | 3 | 11751ms | P-CAP:UNDER_PLAN |
| PA043 | plan | SUCCESS | ✅ | 3 | 4939ms | — |
| PA044 | plan | SUCCESS | ✅ | 2 | 2808ms | — |
| PA045 | plan | SUCCESS | ✅ | 3 | 6653ms | — |
| PA046 | plan | SUCCESS | ✅ | 2 | 9517ms | — |
| PA047 | plan | SUCCESS | ✅ | 1 | 5637ms | — |
| PA048 | plan | SUCCESS | ✅ | 1 | 3449ms | — |
| PA049 | plan | SUCCESS | ❌ | 4 | 25871ms | P-CAP:UNDER_PLAN |
| PA050 | plan | SUCCESS | ✅ | 1 | 3813ms | — |

## Interpretation rules

- Provider/API, contract/oracle, and runtime failures are reported separately from Planner capability.
- Each selected case is submitted once; internal production fallback calls, if any, are recorded as evidence and are not altered.
- No golden plan, case-specific correction, JSON repair, or Uncertainty policy result is used in the Planner score.
- A new capability score requires this real run; v1.0 results are not re-scored in place.
