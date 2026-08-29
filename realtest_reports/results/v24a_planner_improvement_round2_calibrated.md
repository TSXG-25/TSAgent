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
- Source raw report: `realtest_reports/results/v24a_planner_improvement_round2.json`

## Summary

| Metric | Value |
| --- | ---: |
| Cases submitted | 46 |
| Evaluable cases | 45 |
| Capability pass | 35/45 (77.8%) |
| Raw case pass | 35/46 (76.1%) |
| Schema validity | 100.0% |
| Dependency validity | 100.0% |
| Executable plan rate | 77.8% |
| Missing task rate | 16.5% |
| Overplanning rate | 42.2% |
| Clarification accuracy | 100.0% |
| Unnecessary planning rate | 0.0% |
| Average / P95 tasks | 2.22 / 5 |
| Average / P95 latency | 10862ms / 38754.988 |

## Failure separation

- Provider/API failures: **1**
- Contract/Oracle failures: **0**
- Runtime/integration failures: **0**

| Attribution | Cases |
| --- | ---: |
| P-CAP | 10 |
| P-PROV | 1 |

## Failure clusters

| Cluster | Count |
| --- | ---: |
| P-CAP:UNDER_PLAN | 10 |
| P-PROV:INTERNAL | 1 |

## Case results

| Case | Mode | Provider | Pass | Tasks | Latency | Failure |
| --- | --- | --- | :---: | ---: | ---: | --- |
| PA005 | plan | SUCCESS_WITH_PROVIDER_FALLBACK | ✅ | 2 | 17627ms | — |
| PA006 | plan | SUCCESS | ✅ | 1 | 5037ms | — |
| PA007 | plan | SUCCESS | ✅ | 1 | 9757ms | — |
| PA008 | plan | SUCCESS | ✅ | 2 | 6284ms | — |
| PA009 | plan | SUCCESS | ✅ | 5 | 15932ms | — |
| PA010 | plan | SUCCESS | ✅ | 5 | 24920ms | — |
| PA011 | plan | SUCCESS | ✅ | 7 | 15086ms | — |
| PA012 | plan | PROVIDER_ERROR | ❌ | — | 45007ms | P-PROV:INTERNAL |
| PA013 | plan | SUCCESS | ✅ | 3 | 20789ms | — |
| PA014 | plan | SUCCESS | ✅ | 3 | 12432ms | — |
| PA015 | plan | SUCCESS | ✅ | 4 | 13850ms | — |
| PA016 | plan | SUCCESS | ❌ | 4 | 41038ms | P-CAP:UNDER_PLAN |
| PA017 | plan | SUCCESS | ❌ | 2 | 9913ms | P-CAP:UNDER_PLAN |
| PA018 | plan | SUCCESS | ❌ | 3 | 18033ms | P-CAP:UNDER_PLAN |
| PA019 | plan | SUCCESS | ✅ | 3 | 5363ms | — |
| PA020 | plan | SUCCESS | ✅ | 3 | 8679ms | — |
| PA021 | plan | SUCCESS | ❌ | 2 | 5027ms | P-CAP:UNDER_PLAN |
| PA022 | plan | SUCCESS | ❌ | 2 | 6912ms | P-CAP:UNDER_PLAN |
| PA023 | plan | SUCCESS | ❌ | 2 | 6710ms | P-CAP:UNDER_PLAN |
| PA024 | plan | SUCCESS | ❌ | 3 | 8126ms | P-CAP:UNDER_PLAN |
| PA025 | plan | SUCCESS | ✅ | 2 | 3390ms | — |
| PA026 | plan | SUCCESS | ✅ | 2 | 5096ms | — |
| PA027 | plan | SUCCESS | ✅ | 2 | 4283ms | — |
| PA028 | plan | SUCCESS | ✅ | 2 | 13737ms | — |
| PA029 | plan | SUCCESS | ❌ | 3 | 16680ms | P-CAP:UNDER_PLAN |
| PA030 | plan | SUCCESS | ❌ | 4 | 11232ms | P-CAP:UNDER_PLAN |
| PA031 | plan | SUCCESS | ✅ | 4 | 23518ms | — |
| PA032 | plan | SUCCESS | ✅ | 5 | 38755ms | — |
| PA033 | abstain | NOT_CALLED | ✅ | 0 | 0ms | — |
| PA034 | abstain | NOT_CALLED | ✅ | 0 | 0ms | — |
| PA035 | abstain | NOT_CALLED | ✅ | 0 | 0ms | — |
| PA036 | abstain | NOT_CALLED | ✅ | 0 | 0ms | — |
| PA037 | abstain | NOT_CALLED | ✅ | 0 | 0ms | — |
| PA038 | abstain | NOT_CALLED | ✅ | 0 | 1ms | — |
| PA039 | abstain | NOT_CALLED | ✅ | 0 | 0ms | — |
| PA040 | abstain | NOT_CALLED | ✅ | 0 | 0ms | — |
| PA041 | plan | SUCCESS | ✅ | 3 | 5327ms | — |
| PA042 | plan | SUCCESS | ✅ | 3 | 7722ms | — |
| PA043 | plan | SUCCESS | ✅ | 3 | 4270ms | — |
| PA044 | plan | SUCCESS | ✅ | 2 | 6154ms | — |
| PA045 | plan | SUCCESS | ✅ | 3 | 6501ms | — |
| PA046 | plan | SUCCESS | ✅ | 1 | 12961ms | — |
| PA047 | plan | SUCCESS | ✅ | 1 | 3215ms | — |
| PA048 | plan | SUCCESS | ✅ | 1 | 2488ms | — |
| PA049 | plan | SUCCESS | ❌ | 1 | 34087ms | P-CAP:UNDER_PLAN |
| PA050 | plan | SUCCESS | ✅ | 1 | 3719ms | — |

## Interpretation rules

- Provider/API, contract/oracle, and runtime failures are reported separately from Planner capability.
- Each selected case is submitted once; internal production fallback calls, if any, are recorded as evidence and are not altered.
- Deterministic pre-Planner abstentions are classified as P-UNCERTAINTY and excluded from the Planner capability denominator.
- No golden plan, case-specific correction, or JSON repair is used in the Planner score.
- A new capability score requires this real run; v1.0 results are not re-scored in place.
