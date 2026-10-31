# ADR-0034: Preserve goal semantics through lowering and final delivery

Status: Implemented for the bounded regressions below. Wider audit remains open.

## Evidence

Human testing exposed a correct resolved append goal containing “不得覆盖或删除原有内容” being lowered into a delete Task. A separate create-and-run request bypassed semantic intent because a Python path and execution outcome had been found. Its creation requirement and write authorization were lost. A successful write/read/explain chain returned only the artifact path; the explain step had historical Run context but no current dependency result. Research synthesis failure lost the Provider category at Runtime finalization.

## Decision

An execution requirement alone is not a complete intent. Named Python requests consume the same semantic contract as other compound goals, retaining deterministic execution requirements additively. When lexical operation binding is absent, a semantic mutation target may authorize only the matching explicitly named original path. A resolved request cannot broaden original scope.

Literal file-operation lowering accepts only complete standalone operations. A verb occurring in a compound goal or preservation constraint cannot select a destructive action. Such goals remain owned by the generic Planner.

ExecutionStage supplies each reasoning Task's dependency results from the current Run inbox. LLMExecutor presents these separately from historical data, with the current Task last. Artifact delivery preserves terminal read/explain/analyze results alongside actual paths and execution stdout. This fixes data loss, but does not prove the semantic correctness of arbitrary generated prose.

LLMRouter raises ProviderError with its original failure category and cause. Runtime retains the category through terminal publication. Failure rendering reports already completed steps and verified partial files without claiming the remaining goal was fulfilled.

## Limits

No new Capability, persistence source, retry loop or Provider fallback is introduced. Artifact publication still depends on task-level verb/target projection; memory and repository scope/trust issues remain open in the goal-contract audit. Full ConversationTracker restoration and checkpoint resume are not implemented by this decision.

## Validation

`tests/test_ha_r2_goal_pipeline.py` covers constraint handling, semantic scope, actual append bytes, Service write/read/explain with unrelated history, real local Python execution, Provider timeout after successful search, and a 35-case deliverable family with supplied semantic decisions. `tests/test_llm_provider_mode.py` verifies Router timeout classification. These are offline contract tests, not real Provider acceptance.
