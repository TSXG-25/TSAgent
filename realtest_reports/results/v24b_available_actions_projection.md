# v2.4B-4a Available Actions Projection Evidence

- HEAD: `fc488bc6b614fd3e2b78bbe525fa74adfcab7ddb`
- Status: `PASS`
- Contract: `v2.4B-available-actions-v1`
- Contract hash: `eb38faa4c12a2c8f8a89ff9973c64bf17a8d7aaf11e08fe0b43bb93bff6ee3bd`
- Clean-checkout related tests: `62 passed`
- Provider calls: `0`
- Tool execution: `0`

## Production boundary

```text
policy-approved canonical Tool names
        +
Tool Registry args_schema
        ↓
project_available_actions()
        ↓
ToolActionProjection[]
```

The projection preserves the canonical execution identity while obtaining
argument names, types and required fields from the registered implementation's
real schema. It contains only the names supplied by the policy caller and fails
fast when a Registry Tool or schema is missing.

The filesystem canonical-to-Registry alias table now has one production source
in `agent.tool_identity`; Compiler, Executor and projection use that source.

## Boundary evidence

```text
canonical identity preserved          PASS
Registry schema projected             PASS
policy-approved actions only          PASS
missing Registry Tool fail-fast       PASS
canonical alias source count          1
Selector → Registry imports           0
Selector → Compiler imports           0
Selector → Executor imports           0
Dataset v1 mutation                    0
Runtime integration                    0
```

B-4a intentionally does not add a compatibility field to
`ExecutionStateProjection` and does not wire the Selector into Runtime. B-4b
must perform one explicit state-contract migration to `available_actions` and
then wire the composition projection at the Runtime boundary.
