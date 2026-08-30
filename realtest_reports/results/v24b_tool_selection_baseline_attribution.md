# v2.4B-2b Attribution Audit

- Evaluated HEAD: `fc1835a8fd776dadbf23cf4b23f0fa3fc83fbca7`
- Source evidence SHA-256: `392ea56539906c00d832ce49824e9cc4298eb9909f3335100db497a28119676e`
- Mechanical result remains `11/24 (45.8%)`; this audit does not rescore Provider output.
- Provider reruns: `0`
- Selector / Dataset / Oracle / projection changes: `0`

## Primary clusters

| Cluster | Cases | Interpretation |
| --- | --- | --- |
| Non-tool canonical envelope | B005, B007, B010, B011, B012, B016, B020, B023, B024 | Semantic `answer/ask` kind is correct, but output uses `tool=null`, carries content/question in `args`, or retains `task_id`. This is a systemic schema-conformance gap. |
| Projected fact binding | B014 | `state.facts.content` exists, but the Selector asks for the content and also emits an invalid ask envelope. |
| State projection watchlist | B019 | The user input names list comprehensions, but the Selector receives no Task and only a generic state goal. Asking is epistemically reasonable; retain mechanical `P-CAP:SCHEMA_INVALID` until contract review. |
| Answer-ready continuation | B006 | Chooses another fetch despite `answer_ready=true`; primary attribution is `WRONG_KIND`. |
| Canonical argument binding | B017 | Correctly selects `filesystem.read`, but binds `source` instead of `path`. |

## Provider path

All 24 cases used DeepSeek only. DeepSeek rejected structured output with HTTP 400
(`This response_format type is unavailable now`), then the same Provider completed
the raw JSON path. Therefore:

```text
provider_path = SINGLE_PROVIDER
format_path   = STRUCTURED_TO_RAW_FALLBACK
cross-provider fallback = 0
```

## Safety evidence

```text
duplicate verified effect = 0
premature answer          = 0
dependency violation      = 0
P-PROV / P-CON / P-ORACLE / P-INT = 0 mechanically
```

The only clearly systemic capability cluster is canonical non-tool output shape.
B019 remains a projection-contract watchlist item and should not be prompt-tuned
before ownership is decided.
