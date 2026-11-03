# v2.4D-1 Memory Learning Discovery

## Result

```text
Evaluated HEAD              79305fe355258b79ad0e91a654f5ddb0ebc251b5
Status                      BLOCKED_PRECONDITION
Provider calls              0
Memory writes               0
Memory store imports        0
```

This is a source-only discovery and production preflight result. It is not a
Memory Learning capability score.

## Dataset / Oracle

```text
Dataset                     v2.4D-memory-learning-v1
Cases                       24
Dataset hash                e821b67e7da66b40a7c1cc38ac6e18d1636b6d01c6aeaa3e873f03bb95f928b7
Golden self-check           24/24 PASS
```

The six frozen families are eligibility, source authority, scope, dedup/conflict,
sensitivity/volatility, and lifecycle boundary. The Oracle is deterministic and
does not import or write any production Memory store.

## Production audit

The current production code exposes six independent writer paths (16 named
writer symbols including service/view facades):

| Layer | Current owner | Storage | Main gap |
| --- | --- | --- | --- |
| Session | `agent/memory/session.py` | process-global `_sessions` | namespace only; no evidence provenance |
| Short-term | `agent/memory/short_term.py` | `data/short_term/<namespace>.json` | append/window semantics; no fact provenance or conflict policy |
| Long-term summary | `agent/memory/long_term.py` | Chroma `long_term_memory` | append-only; metadata is user/type/timestamp only |
| User facts | `agent/memory/long_term.py` | `data/user_facts.db` | unique key plus `INSERT OR REPLACE`; no source/confidence/revision |
| Preference extraction | `agent/memory/preference.py` | delegates to facts | extractor directly persists LLM/regex output |
| Resolution memory | `agent/memory/resolution.py` | `data/resolution_memory/<namespace>.json` | append/max-entry; current Planner omits provenance metadata |

The following are intentionally not reclassified as Memory Learning:

- `ConversationTracker` / `ConversationState` — current conversation Runtime state;
- `RunOutput`, Checkpoint, Artifact and Event — durable Runtime facts;
- `RepositoryIndexer` — workspace grounding index.

## Blockers

1. `PRODUCTION_MEMORY_LEARNING_ENTRY_MISSING` (`P-INT`): no production
   `MemoryLearner`, `MemoryLearningDecision`, or equivalent entry consumes
   `InteractionEvidence + MemoryPolicyProjection`.
2. `MEMORY_WRITE_AUTHORIZATION_FRAGMENTED` (`P-CON`): MemoryService, Runtime,
   Planner, preference extraction and short-term compression can reach different
   writers without one eligibility decision.
3. `MEMORY_PROVENANCE_CONTRACT_MISSING` (`P-CON`): facts and summaries cannot
   return durable evidence tying a write to an evidence id and source reference.

## Watchlist

```text
MEMORY_NAMESPACE_SCOPE_AMBIGUOUS
  SessionRuntime may use session id or tenant:user as a namespace; this is not
  a typed session/user/repository learning scope.

MEMORY_COMMIT_EVIDENCE_MISSING
  save_fact/store_summary swallow persistence exceptions and return no commit fact.

RETRIEVAL_SCOPE_FALLBACK_PRESENT
  filtered summary retrieval retries an unfiltered similarity query when the
  filtered query raises, which is unsafe for a scoped Memory contract.
```

## Decision

Do not run a real Provider baseline yet. Do not add `MemoryLearner` or modify
the existing stores in this discovery commit. The next implementation decision
must first establish one canonical learning boundary with:

```text
InteractionEvidence + MemoryPolicyProjection
    → STORE | UPDATE | IGNORE
    → one scoped persistence boundary
    → durable commit evidence
```

`docs/adr/0032-memory-learning-capability-contract.md` records the proposed
contract and the boundaries that must be preserved.
