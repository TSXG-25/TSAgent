# v2.4A-2 Real Planner Capability Baseline

- Harness: v2.4A-2-real-planner-v1
- HEAD: 6c1743078d67cf1644c22779f40649fb4b9880d2
- Dataset: v2.4A-planner-v1
- Dataset hash: 7f5b28f608194a324f4244c860a8ed9101bcb7afa3b68e5129632ebfb0290291
- Provider: deepseek / deepseek-v4-flash
- Provider mode: deepseek (fallback disabled)
- Automatic case retry: False
- Golden-plan fallback: False

## Summary

| Metric | Value |
| --- | ---: |
| Cases submitted | 50 |
| Evaluable cases | 48 |
| Capability pass | 20/48 (41.7%) |
| Raw case pass | 20/50 (40.0%) |
| Schema validity | 81.2% |
| Dependency validity | 81.2% |
| Executable plan rate | 41.7% |
| Missing task rate | 34.1% |
| Overplanning rate | 66.7% |
| Clarification accuracy | 89.6% |
| Unnecessary planning rate | 66.7% |
| Average / P95 tasks | 2.31 / 6 |
| Average / P95 latency | 13218ms / 44177ms |

## Failure separation

- Provider/API failures: **2**
- Contract/Oracle failures: **0**
- Runtime/integration failures: **0**

## Failure clustering

| Cluster | Count |
| --- | ---: |
| P-CAP:FALSE_CLARIFICATION | 1 |
| P-CAP:MISSED_CLARIFICATION | 4 |
| P-CAP:OVER_PLAN | 4 |
| P-CAP:UNDER_PLAN | 19 |
| P-PROV:INTERNAL | 2 |

## Case results

| Case | Family | Mode | Provider | Pass | Tasks | Latency | Failure |
| --- | --- | --- | --- | :---: | ---: | ---: | --- |
| PA001 | P1 | chat | SUCCESS_WITH_PROVIDER_FALLBACK | ❌ | 1 | 44177ms | P-CAP:OVER_PLAN |
| PA002 | P1 | chat | SUCCESS | ❌ | 1 | 2291ms | P-CAP:OVER_PLAN |
| PA003 | P1 | chat | SUCCESS | ❌ | 1 | 2451ms | P-CAP:OVER_PLAN |
| PA004 | P1 | chat | SUCCESS | ❌ | 1 | 1755ms | P-CAP:OVER_PLAN |
| PA005 | P2 | plan | SUCCESS | ✅ | 2 | 8671ms | — |
| PA006 | P2 | plan | SUCCESS | ✅ | 3 | 13176ms | — |
| PA007 | P2 | plan | SUCCESS | ✅ | 1 | 7507ms | — |
| PA008 | P2 | plan | SUCCESS | ✅ | 2 | 12060ms | — |
| PA009 | P3 | plan | SUCCESS | ✅ | 5 | 17922ms | — |
| PA010 | P3 | plan | SUCCESS | ✅ | 6 | 36072ms | — |
| PA011 | P3 | plan | SUCCESS | ❌ | 7 | 10391ms | P-CAP:UNDER_PLAN |
| PA012 | P3 | plan | SUCCESS | ❌ | 6 | 37108ms | P-CAP:UNDER_PLAN |
| PA013 | P4 | plan | SUCCESS | ❌ | 5 | 25905ms | P-CAP:UNDER_PLAN |
| PA014 | P4 | plan | SUCCESS | ❌ | 3 | 27784ms | P-CAP:UNDER_PLAN |
| PA015 | P4 | plan | PROVIDER_ERROR | ❌ | — | 45003ms | P-PROV:INTERNAL |
| PA016 | P4 | plan | SUCCESS | ❌ | 4 | 13788ms | P-CAP:UNDER_PLAN |
| PA017 | P5 | plan | SUCCESS | ❌ | 2 | 10117ms | P-CAP:UNDER_PLAN |
| PA018 | P5 | plan | SUCCESS | ❌ | 3 | 14646ms | P-CAP:UNDER_PLAN |
| PA019 | P5 | plan | SUCCESS | ✅ | 3 | 5098ms | — |
| PA020 | P5 | plan | SUCCESS | ✅ | 3 | 6125ms | — |
| PA021 | P6 | plan | SUCCESS | ❌ | 2 | 5733ms | P-CAP:UNDER_PLAN |
| PA022 | P6 | plan | NOT_CALLED | ❌ | 0 | 0ms | P-CAP:FALSE_CLARIFICATION |
| PA023 | P6 | plan | SUCCESS | ❌ | 2 | 9243ms | P-CAP:UNDER_PLAN |
| PA024 | P6 | plan | SUCCESS | ❌ | 3 | 6665ms | P-CAP:UNDER_PLAN |
| PA025 | P7 | plan | SUCCESS | ❌ | 2 | 5733ms | P-CAP:UNDER_PLAN |
| PA026 | P7 | plan | SUCCESS | ❌ | 2 | 6179ms | P-CAP:UNDER_PLAN |
| PA027 | P7 | plan | SUCCESS | ✅ | 2 | 9090ms | — |
| PA028 | P7 | plan | SUCCESS | ❌ | 2 | 5891ms | P-CAP:UNDER_PLAN |
| PA029 | P8 | plan | SUCCESS | ❌ | 5 | 20323ms | P-CAP:UNDER_PLAN |
| PA030 | P8 | plan | SUCCESS | ❌ | 4 | 11338ms | P-CAP:UNDER_PLAN |
| PA031 | P8 | plan | SUCCESS | ❌ | 4 | 27448ms | P-CAP:UNDER_PLAN |
| PA032 | P8 | plan | PROVIDER_ERROR | ❌ | — | 45006ms | P-PROV:INTERNAL |
| PA033 | P9 | abstain | NOT_CALLED | ✅ | 0 | 0ms | — |
| PA034 | P9 | abstain | SUCCESS | ❌ | 1 | 18418ms | P-CAP:MISSED_CLARIFICATION |
| PA035 | P9 | abstain | NOT_CALLED | ✅ | 0 | 0ms | — |
| PA036 | P9 | abstain | SUCCESS | ❌ | 1 | 6640ms | P-CAP:MISSED_CLARIFICATION |
| PA037 | P10 | abstain | NOT_CALLED | ✅ | 0 | 0ms | — |
| PA038 | P10 | abstain | SUCCESS | ❌ | 1 | 26872ms | P-CAP:MISSED_CLARIFICATION |
| PA039 | P10 | abstain | SUCCESS | ❌ | 1 | 30632ms | P-CAP:MISSED_CLARIFICATION |
| PA040 | P10 | abstain | NOT_CALLED | ✅ | 0 | 0ms | — |
| PA041 | P11 | plan | SUCCESS | ✅ | 3 | 4027ms | — |
| PA042 | P11 | plan | SUCCESS | ❌ | 3 | 13399ms | P-CAP:UNDER_PLAN |
| PA043 | P11 | plan | SUCCESS | ✅ | 3 | 11360ms | — |
| PA044 | P11 | plan | SUCCESS | ✅ | 2 | 2540ms | — |
| PA045 | P11 | plan | SUCCESS | ✅ | 3 | 5831ms | — |
| PA046 | P12 | plan | SUCCESS | ❌ | 2 | 15353ms | P-CAP:UNDER_PLAN |
| PA047 | P12 | plan | SUCCESS | ✅ | 1 | 4938ms | — |
| PA048 | P12 | plan | SUCCESS | ✅ | 1 | 7860ms | — |
| PA049 | P12 | plan | SUCCESS | ❌ | 1 | 15728ms | P-CAP:UNDER_PLAN |
| PA050 | P12 | plan | SUCCESS | ✅ | 1 | 2622ms | — |

## Interpretation rules

- Provider/API failures are not included in capability-rate denominators.
- The harness submits each Dataset case exactly once and does not retry the case.
- Production Planner internal structured/JSON fallback calls are recorded, not altered.
- No golden plan, case-specific correction, or output repair is used.
