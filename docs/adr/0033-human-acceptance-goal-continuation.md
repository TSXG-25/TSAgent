# ADR-0033: Human acceptance goal and result continuation

Status: Implemented; offline verification recorded separately. Real Provider acceptance pending.

## Problem

Keyword routing could interpret a request to create a calculator as a math answer. The same lexical extraction supplied completion requirements, so the missing artifact requirement escaped both execution and finalization. Short requests could be treated as chat solely because of their length. Normal follow-up requests did not consume durable prior Run results.

## Contract

The existing IntentEngine is the semantic boundary. Its structured output carries `resolved_request`, `conversation_relation`, `requested_outcomes`, and `artifact_required` alongside domain/action. Domain describes the topic and cannot remove required execution. The old general keyword dispatch table and the separate synchronous LLM reference-resolution pass are removed. ReferenceResolver supplies deterministic candidates; IntentEngine owns semantic resolution. Concrete command/code execution and source-grounding constraints retain their deterministic lowering. Malformed semantic responses fail before effects; Provider failures retain their classification.

A resolved request preserves all original requirements, adding only context-grounded referents. Original explicit write scopes remain authoritative. New artifacts without user-selected paths may be planned under `output/`; a follow-up can target a previously verified artifact. A model-selected path cannot broaden an explicit user scope.

The existing SQLite Store supplies the last four terminal Runs in the same tenant/session, excluding the current Run. Terminal events atomically retain the request, resolved goal, missing requirements and bounded observation projection beside RunOutput and Artifact facts. Historical answer text is separate from verified effects and artifacts. Context is available to intent, planner and answer consumers regardless of Memory retrieval; it survives a Service restart. Historical files must be read again before modification. No second conversation persistence store is introduced.

Artifact delivery requires all planned write/modify targets to have passed execution and current scoped filesystem verification. A generic successful response or unrelated existing file is insufficient. Final artifact paths come from the workspace; failures disclose verified partial files. Requirement projection may add constraints but cannot erase established outcomes during finalization.

TSAgent identity and available capability classes are projected from the current registry into answer prompts. Registered capability is distinct from successful invocation. Research synthesis consumes actual tool observations and reports source limitations. Complete command output, including whitespace, is preserved for rendering. Compiler normalization applies to resource targets, not executable command text. Nonzero process exits become execution failures with their original error code. Explicit path-before-write syntax is bound by the resource parser.

## Validation boundary

Local regression fixtures substitute only model/search responses; Runtime, Compiler, Executor, filesystem, SQLite, Service events and verifiers execute normally. The four-turn regression covers research → summary → local creation → modification. Additional cases cover restart, corrective continuation, isolation, malformed model output, fabricated completion, scope preservation and partial failure.

These tests prove wiring and evidence invariants for the supplied model decisions, not natural-language accuracy or real search availability. Human acceptance with an explicit Provider remains required. Reports, scripts and test products stay local.
