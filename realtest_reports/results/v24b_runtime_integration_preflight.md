# v2.4B-4b Runtime Integration Preflight

- Evaluated HEAD: `2d01778dacf50c44bf8f87dcf803c996a764a25c`
- Status: `BLOCKED_PRECONDITION`
- Provider calls: `0`
- Tool execution: `0`
- Runtime execution: `0`
- Mode: read-only production source scan

## Discovery

```text
production NextActionSelector consumers   0
Runtime NEXT_ACTION                       passthrough → EXECUTE
ToolExecutor execution unit               complete ExecutionPlan
Compiler multi-step plans                 present
```

The Selector contract returns exactly one `NextAction`. Production execution
currently compiles a Task into a complete `ExecutionPlan` and executes all plan
steps in one ToolExecutor call. Wiring the Selector before that call would not
make its action authoritative; wiring it around the Compiler would create a
second Tool-selection and execution path.

## Blockers

```text
SELECTOR_NOT_CONSUMED_BY_RUNTIME       P-INT
NEXT_ACTION_STATE_IS_PASSTHROUGH       P-INT
NEXT_ACTION_EXECUTION_UNIT_MISMATCH    P-CON
```

## Required ownership decision

Choose exactly one production model before implementation:

1. Preserve Compiler-owned whole-plan execution and scope Selector ownership to
   explicit dynamic/ReAct Tasks; or
2. Migrate Runtime/Executor to execute one selected `ExecutionStep` per state
   transition, making Selector the general action owner.

The implementation must not select one action and then execute an unrelated
whole plan, and must not preserve both models as fallback paths.
