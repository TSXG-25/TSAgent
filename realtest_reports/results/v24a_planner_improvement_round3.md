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
| Evaluable cases | 45 |
| Capability pass | 37/45 (82.2%) |
| Raw case pass | 37/46 (80.4%) |
| Schema validity | 100.0% |
| Dependency validity | 100.0% |
| Executable plan rate | 82.2% |
| Missing task rate | 12.9% |
| Overplanning rate | 35.6% |
| Clarification accuracy | 100.0% |
| Unnecessary planning rate | 0.0% |
| Average / P95 tasks | 2.16 / 4 |
| Average / P95 latency | 9988ms / 35275.092 |

## Failure separation

- Provider/API failures: **1**
- Contract/Oracle failures: **0**
- Runtime/integration failures: **0**

| Attribution | Cases |
| --- | ---: |
| P-CAP | 8 |
| P-PROV | 1 |

## Failure clusters

| Cluster | Count |
| --- | ---: |
| P-CAP:UNDER_PLAN | 8 |
| P-PROV:INTERNAL | 1 |

## Case results

| Case | Mode | Provider | Pass | Tasks | Latency | Failure |
| --- | --- | --- | :---: | ---: | ---: | --- |
| PA005 | plan | SUCCESS_WITH_PROVIDER_FALLBACK | ✅ | 2 | 10539ms | — |
| PA006 | plan | SUCCESS | ✅ | 1 | 22471ms | — |
| PA007 | plan | SUCCESS | ✅ | 1 | 14236ms | — |
| PA008 | plan | SUCCESS | ✅ | 2 | 5473ms | — |
| PA009 | plan | SUCCESS | ✅ | 4 | 15586ms | — |
| PA010 | plan | SUCCESS | ✅ | 5 | 25282ms | — |
| PA011 | plan | SUCCESS | ✅ | 4 | 36122ms | — |
| PA012 | plan | PROVIDER_ERROR | ❌ | — | 45008ms | P-PROV:INTERNAL |
| PA013 | plan | SUCCESS | ✅ | 6 | 30446ms | — |
| PA014 | plan | SUCCESS | ✅ | 2 | 9804ms | — |
| PA015 | plan | SUCCESS | ✅ | 4 | 8876ms | — |
| PA016 | plan | SUCCESS | ❌ | 4 | 35275ms | P-CAP:UNDER_PLAN |
| PA017 | plan | SUCCESS | ✅ | 2 | 5898ms | — |
| PA018 | plan | SUCCESS | ❌ | 2 | 7121ms | P-CAP:UNDER_PLAN |
| PA019 | plan | SUCCESS | ✅ | 3 | 6559ms | — |
| PA020 | plan | SUCCESS | ✅ | 3 | 5646ms | — |
| PA021 | plan | SUCCESS | ❌ | 2 | 4682ms | P-CAP:UNDER_PLAN |
| PA022 | plan | SUCCESS | ✅ | 2 | 6728ms | — |
| PA023 | plan | SUCCESS | ❌ | 2 | 3619ms | P-CAP:UNDER_PLAN |
| PA024 | plan | SUCCESS | ✅ | 3 | 6653ms | — |
| PA025 | plan | SUCCESS | ✅ | 2 | 4540ms | — |
| PA026 | plan | SUCCESS | ✅ | 2 | 4245ms | — |
| PA027 | plan | SUCCESS | ✅ | 2 | 4568ms | — |
| PA028 | plan | SUCCESS | ✅ | 2 | 4127ms | — |
| PA029 | plan | SUCCESS | ❌ | 4 | 17040ms | P-CAP:UNDER_PLAN |
| PA030 | plan | SUCCESS | ✅ | 4 | 5975ms | — |
| PA031 | plan | SUCCESS | ✅ | 4 | 15943ms | — |
| PA032 | plan | SUCCESS | ✅ | 3 | 21918ms | — |
| PA033 | abstain | NOT_CALLED | ✅ | 0 | 0ms | — |
| PA034 | abstain | NOT_CALLED | ✅ | 0 | 0ms | — |
| PA035 | abstain | NOT_CALLED | ✅ | 0 | 0ms | — |
| PA036 | abstain | NOT_CALLED | ✅ | 0 | 0ms | — |
| PA037 | abstain | NOT_CALLED | ✅ | 0 | 0ms | — |
| PA038 | abstain | NOT_CALLED | ✅ | 0 | 0ms | — |
| PA039 | abstain | NOT_CALLED | ✅ | 0 | 0ms | — |
| PA040 | abstain | NOT_CALLED | ✅ | 0 | 0ms | — |
| PA041 | plan | SUCCESS | ✅ | 3 | 7230ms | — |
| PA042 | plan | SUCCESS | ❌ | 3 | 6928ms | P-CAP:UNDER_PLAN |
| PA043 | plan | SUCCESS | ✅ | 3 | 9340ms | — |
| PA044 | plan | SUCCESS | ✅ | 2 | 3770ms | — |
| PA045 | plan | SUCCESS | ✅ | 3 | 4777ms | — |
| PA046 | plan | SUCCESS | ❌ | 2 | 15031ms | P-CAP:UNDER_PLAN |
| PA047 | plan | SUCCESS | ✅ | 1 | 2435ms | — |
| PA048 | plan | SUCCESS | ✅ | 1 | 7148ms | — |
| PA049 | plan | SUCCESS | ❌ | 1 | 15645ms | P-CAP:UNDER_PLAN |
| PA050 | plan | SUCCESS | ✅ | 1 | 2761ms | — |

## Interpretation rules

- Provider/API, contract/oracle, and runtime failures are reported separately from Planner capability.
- Each selected case is submitted once; internal production fallback calls, if any, are recorded as evidence and are not altered.
- Deterministic pre-Planner abstentions are classified as P-UNCERTAINTY and excluded from the Planner capability denominator.
- No golden plan, case-specific correction, or JSON repair is used in the Planner score.
- A new capability score requires this real run; v1.0 results are not re-scored in place.
