# ADR-0036: Action effect state gates retry and replan

- Status: Accepted — implemented and offline verified
- Scope: Runtime action failure recovery and Workflow checkpoint effect projection

## Problem

`ActionResult.retryable` describes whether another attempt may be useful, but does not prove that repeating an action is safe. Treating every failed write/execute task as `FAILED_BEFORE_COMMIT` when the executor supplied no effect evidence also converted an unknown outcome into permission to replay.

## Decision

`ActionResult` carries an optional `side_effect_state`. Missing or unrecognized evidence is `UNKNOWN` at recovery boundaries; only an executor with a concrete pre-effect guarantee may report `NONE`, `NOT_STARTED`, or `FAILED_BEFORE_COMMIT`.

The current PlanExecutor reports `NONE` only when the first tool step fails schema argument validation or the execution environment rejects invocation before the tool runs. The same failures after an earlier step, and ordinary tool failures, remain `UNKNOWN`.

Runtime can retry or replan an action only when its effect is known absent. `REPLAN` additionally requires an allowlisted failure code; read-only failures retain the existing bounded path. Unknown-effect writes/commands converge to failure without replay. Workflow checkpoints infer successful declared effects as `COMMITTED`, failed effectful tasks without explicit metadata as `UNKNOWN`, and read-only tasks as `NONE`.

This decision keeps recovery in the existing Runtime state loop. It does not authorize retries of unknown external outcomes, infer successful effects from error text, or broaden REPLAN to arbitrary tool failures.

## Verification

Offline tests cover ActionResult serialization, first-step versus later-step argument validation, Runtime no-effect replan gating, unknown-effect retry rejection, and checkpoint fallback. The full offline suite passed with 978 tests; five real Provider/network test files were excluded. Real Provider recovery and real DuckDuckGo MCP/network behavior remain unverified.
