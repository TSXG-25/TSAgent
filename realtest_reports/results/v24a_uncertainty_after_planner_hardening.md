# v2.4A-2c Uncertainty Policy Baseline

> This is a deterministic policy baseline, not a Provider or Planner acceptance run.

- Status: **DETERMINISTIC_POLICY_BASELINE**
- Dataset: `v2.4A-uncertainty-v1`
- Dataset hash: `8f1479bdded0f00e20fd4d283082869d078b4fa217719dc768ae8a6afaaf1cdb`
- Production rule: `agent.planner.constraint_extractor.detect_abstention`
- Provider calls: **0**

## Metrics

| Metric | Value |
| --- | ---: |
| Cases | 27 |
| Exact decisions | 26/27 |
| True abstain | 11 |
| False abstain | 0 |
| Missed abstention | 1 |
| Abstain precision | 100.0% |
| Abstain recall | 91.7% |
| False abstention rate | 0.0% |
| Missed abstention rate | 8.3% |

## Case evidence

| Case | Pair | Context | Expected | Actual | Outcome |
| --- | --- | --- | ---: | ---: | --- |
| U001 | vague_module | none | True | True | TRUE_ABSTAIN |
| U002 | vague_module | none | False | False | TRUE_PROCEED |
| U003 | vague_object | none | True | True | TRUE_ABSTAIN |
| U004 | vague_destination | none | False | False | TRUE_PROCEED |
| U005 | vague_destination | none | True | True | TRUE_ABSTAIN |
| U006 | continuation | none | False | False | TRUE_PROCEED |
| U007 | continuation | none | True | False | MISSED_ABSTENTION |
| U008 | continuation | valid_continuation | False | False | TRUE_PROCEED |
| U009 | feature | none | True | True | TRUE_ABSTAIN |
| U010 | feature | none | False | False | TRUE_PROCEED |
| U011 | save_result | none | True | True | TRUE_ABSTAIN |
| U012 | save_result | none | False | False | TRUE_PROCEED |
| U013 | run_object | none | True | True | TRUE_ABSTAIN |
| U014 | run_object | none | False | False | TRUE_PROCEED |
| U015 | function_reference | none | True | True | TRUE_ABSTAIN |
| U016 | function_reference | grounding_candidate | False | False | TRUE_PROCEED |
| U017 | issue_reference | none | True | True | TRUE_ABSTAIN |
| U018 | issue_reference | repo_context | False | False | TRUE_PROCEED |
| U019 | modifier_reference | none | True | True | TRUE_ABSTAIN |
| U020 | modifier_reference | none | False | False | TRUE_PROCEED |
| U021 | remote_effect | none | True | True | TRUE_ABSTAIN |
| U022 | remote_effect | none | False | False | TRUE_PROCEED |
| U023 | config_target | none | True | True | TRUE_ABSTAIN |
| U024 | config_target | none | False | False | TRUE_PROCEED |
| U025 | immediate_antecedent | none | False | False | TRUE_PROCEED |
| U026 | immediate_antecedent | grounding_candidate | False | False | TRUE_PROCEED |
| U027 | vague_object | none | False | False | TRUE_PROCEED |

## Interpretation

- `P-UNCERTAINTY` records a current production-policy mismatch; this harness does not modify the policy.
- Context signals are supplied through the detector's existing `grounding` / `repo_context` contract.
- No Provider, Planner prompt, golden decision, or automatic retry is used.
