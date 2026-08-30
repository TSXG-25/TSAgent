# v2.4B-3 Residual Reasoning Audit

- Baseline: `fc1835a8` — 11/24
- Candidate: `b25d85fd` — 21/24
- Dataset: `v2.4B-tool-selection-v1`
- Provider runs per HEAD: `1`
- Automatic reruns: `0`

This audit compares the unchanged frozen case input, raw Provider output,
normalized action and Oracle result. It does not change the Selector, Dataset,
Oracle or projection and does not rerun the Provider.

## Decision table

| Case | Baseline | Candidate | Finding |
| --- | --- | --- | --- |
| B014 | FAIL | FAIL | Persistent `P-CAP:PROJECTED_FACT_BINDING`. Both outputs say content is absent although the projection contains it twice. |
| B018 | PASS | FAIL | New mechanical regression. The input supports verification, but no specific B-3 rule is proven causal from one sample per HEAD. |
| B021 | PASS | FAIL | New mechanical regression. The changed-query switch is observable, but no specific B-3 rule is proven causal from one sample per HEAD. |
| B017 | FAIL | PASS | Mechanical improvement, while canonical argument-schema ownership remains absent from the projection. |

## Regression anatomy

B018 and B021 do not justify another prompt patch. The baseline and candidate
share the retry and verification instructions. B-3 added canonical envelopes,
strengthened state authority for `answer_ready`, and tightened non-invention
wording, but paired evidence cannot isolate any one addition as the cause of
either branch change.

The two cases instead expose an underspecified action-class boundary:

```text
retry  = same tool + task + args after failure
verify = obtain machine evidence after ok but unverified
switch = use a different tool or args after an observation
```

`retry` requires `retryable=true` and Runtime budget. `verify` and `switch` are
not implicit retries, but Runtime must explicitly project them as allowed. This
authority cannot be invented by Selector prose.

## B014

B014 is the only stable residual capability gap. In both rounds:

```text
state.facts.content                    present
observation.last_result.content       present
Selector claim                        content absent
```

The attribution remains `P-CAP:PROJECTED_FACT_BINDING`. One isolated residual
does not justify another general prompt candidate.

## Tool argument schema ownership

The ownership decision is option B: Runtime composition projects canonical Tool
schemas to the Selector.

```text
Tool Registry args_schema
  + canonical identity/alias resolution
        ↓
available_actions[]
  - canonical tool name
  - read-only canonical args schema
        ↓
NextActionSelector
```

The Selector must not import Tool Registry, Compiler or Executor. The projection
contains only actions already allowed for the current Run/Task. Dataset v1 and
its evidence remain immutable; a projection change requires a new explicit
contract/hash.

## Decision

- Keep the B-3 candidate and its 21/24 evidence.
- Do not create another prompt candidate from these three cases.
- Do not enter Runtime integration yet.
- Implement and verify canonical `available_actions` projection first, then
  start B-4 integration using that contract.
