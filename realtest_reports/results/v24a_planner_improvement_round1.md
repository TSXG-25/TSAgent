# v2.4A-2d Real Planner Re-baseline

本报告只测 v1.1 calibrated view 中的 Planner-owned cases。Chat/Routing 与 Uncertainty 独立统计，不进入 Planner capability 分母。

- Harness: `v2.4A-2d-real-planner-rebaseline-v1`
- HEAD: `6cf9e0d0016b0f95d1717581b22784f2776be470`
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
| Evaluable cases | 44 |
| Capability pass | 33/44 (75.0%) |
| Raw case pass | 33/46 (71.7%) |
| Schema validity | 93.2% |
| Dependency validity | 93.2% |
| Executable plan rate | 75.0% |
| Missing task rate | 15.5% |
| Overplanning rate | 38.6% |
| Clarification accuracy | 93.2% |
| Unnecessary planning rate | 25.0% |
| Average / P95 tasks | 2.18 / 5 |
| Average / P95 latency | 11477ms / 36179.157 |

## Failure separation

- Provider/API failures: **2**
- Contract/Oracle failures: **0**
- Runtime/integration failures: **0**

| Attribution | Cases |
| --- | ---: |
| P-CAP | 11 |
| P-PROV | 2 |

## Failure clusters

| Cluster | Count |
| --- | ---: |
| P-CAP:FALSE_CLARIFICATION | 1 |
| P-CAP:MISSED_CLARIFICATION | 2 |
| P-CAP:UNDER_PLAN | 8 |
| P-PROV:INTERNAL | 2 |

## Case results

| Case | Mode | Provider | Pass | Tasks | Latency | Failure |
| --- | --- | --- | :---: | ---: | ---: | --- |
| PA005 | plan | SUCCESS_WITH_PROVIDER_FALLBACK | ✅ | 2 | 14666ms | — |
| PA006 | plan | SUCCESS | ✅ | 2 | 3601ms | — |
| PA007 | plan | SUCCESS | ✅ | 1 | 9917ms | — |
| PA008 | plan | SUCCESS | ✅ | 1 | 3445ms | — |
| PA009 | plan | SUCCESS | ✅ | 5 | 15296ms | — |
| PA010 | plan | SUCCESS | ✅ | 5 | 15098ms | — |
| PA011 | plan | SUCCESS | ✅ | 7 | 24334ms | — |
| PA012 | plan | PROVIDER_ERROR | ❌ | — | 45014ms | P-PROV:INTERNAL |
| PA013 | plan | SUCCESS | ✅ | 3 | 30272ms | — |
| PA014 | plan | SUCCESS | ✅ | 2 | 6444ms | — |
| PA015 | plan | SUCCESS | ✅ | 4 | 17821ms | — |
| PA016 | plan | SUCCESS | ❌ | 4 | 27471ms | P-CAP:UNDER_PLAN |
| PA017 | plan | SUCCESS | ❌ | 2 | 5042ms | P-CAP:UNDER_PLAN |
| PA018 | plan | SUCCESS | ❌ | 3 | 16944ms | P-CAP:UNDER_PLAN |
| PA019 | plan | SUCCESS | ✅ | 3 | 5923ms | — |
| PA020 | plan | SUCCESS | ✅ | 3 | 4420ms | — |
| PA021 | plan | SUCCESS | ✅ | 2 | 4388ms | — |
| PA022 | plan | NOT_CALLED | ❌ | 0 | 0ms | P-CAP:FALSE_CLARIFICATION |
| PA023 | plan | SUCCESS | ❌ | 2 | 9933ms | P-CAP:UNDER_PLAN |
| PA024 | plan | SUCCESS | ❌ | 3 | 6971ms | P-CAP:UNDER_PLAN |
| PA025 | plan | SUCCESS | ✅ | 2 | 6014ms | — |
| PA026 | plan | SUCCESS | ✅ | 2 | 5261ms | — |
| PA027 | plan | SUCCESS | ✅ | 2 | 5713ms | — |
| PA028 | plan | SUCCESS | ✅ | 2 | 3830ms | — |
| PA029 | plan | SUCCESS | ❌ | 3 | 36179ms | P-CAP:UNDER_PLAN |
| PA030 | plan | SUCCESS | ✅ | 4 | 6416ms | — |
| PA031 | plan | SUCCESS | ✅ | 4 | 22022ms | — |
| PA032 | plan | SUCCESS | ✅ | 3 | 18471ms | — |
| PA033 | abstain | NOT_CALLED | ✅ | 0 | 0ms | — |
| PA034 | abstain | NOT_CALLED | ✅ | 0 | 0ms | — |
| PA035 | abstain | NOT_CALLED | ✅ | 0 | 0ms | — |
| PA036 | abstain | NOT_CALLED | ✅ | 0 | 0ms | — |
| PA037 | abstain | NOT_CALLED | ✅ | 0 | 0ms | — |
| PA038 | abstain | SUCCESS | ❌ | 1 | 28228ms | P-CAP:MISSED_CLARIFICATION |
| PA039 | abstain | SUCCESS | ❌ | 1 | 16214ms | P-CAP:MISSED_CLARIFICATION |
| PA040 | abstain | NOT_CALLED | ✅ | 0 | 0ms | — |
| PA041 | plan | SUCCESS | ✅ | 3 | 13717ms | — |
| PA042 | plan | SUCCESS | ❌ | 3 | 4234ms | P-CAP:UNDER_PLAN |
| PA043 | plan | SUCCESS | ✅ | 3 | 6892ms | — |
| PA044 | plan | SUCCESS | ✅ | 2 | 3141ms | — |
| PA045 | plan | SUCCESS | ✅ | 3 | 4615ms | — |
| PA046 | plan | PROVIDER_ERROR | ❌ | — | 45008ms | P-PROV:INTERNAL |
| PA047 | plan | SUCCESS | ✅ | 1 | 3228ms | — |
| PA048 | plan | SUCCESS | ✅ | 1 | 1823ms | — |
| PA049 | plan | SUCCESS | ❌ | 1 | 26396ms | P-CAP:UNDER_PLAN |
| PA050 | plan | SUCCESS | ✅ | 1 | 3561ms | — |

## Interpretation rules

- Provider/API, contract/oracle, and runtime failures are reported separately from Planner capability.
- Each selected case is submitted once; internal production fallback calls, if any, are recorded as evidence and are not altered.
- Deterministic pre-Planner abstentions are classified as P-UNCERTAINTY and excluded from the Planner capability denominator.
- No golden plan, case-specific correction, or JSON repair is used in the Planner score.
- A new capability score requires this real run; v1.0 results are not re-scored in place.
