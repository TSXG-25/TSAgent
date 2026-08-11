# ADR-0027: Desktop Composition Root and Local Backend Bootstrap

- **状态**: Accepted — Desktop-4a Implemented and Verified
- **日期**: 2026-08-11
- **范围**: Desktop-4a
- **相关**: ADR-0021, ADR-0025, ADR-0026

## Context

Desktop-3 established the TypeScript `LocalAgentServiceClient` and the injected
Tauri sidecar transport. The UI still needs one explicit composition root so
that Mock and Local are selectable without leaking transport, Runtime, or
SQLite details into React components.

The local sidecar protocol does not yet expose a durable `list_runs` query.
The first Local desktop slice therefore cannot promise historical Run
browsing after an App restart. It must not compensate by reading SQLite from
the frontend.

## Decision

### Explicit runtime mode

`createAgentServiceClient()` selects exactly one mode:

```text
VITE_AGENT_SERVICE_MODE=mock   → MockAgentServiceClient
VITE_AGENT_SERVICE_MODE=local  → LocalAgentServiceClient
```

An omitted mode defaults to Mock for browser development. An invalid mode is
an explicit configuration error. Local health failure, missing identity, or a
missing sidecar bridge produces a visible unavailable backend; it never falls
back to Mock.

### Identity ownership

The composition root owns one explicit desktop identity:

```text
tenantId + userId + sessionId
```

`tenantId` and `userId` are required for Local mode. `sessionId` may be
configured with `VITE_TSAGENT_SESSION_ID`; otherwise it is generated once for
the composition-root lifetime and reused for every Run created by that App
instance. A new Run does not create a new Session identity.

The Local client validates tenant/session scope before sending requests and
injects the configured `userId` into the sidecar request. Public UI code uses
the client identity rather than hard-coded defaults.

### Readiness

The App startup sequence is:

```text
createAgentServiceClient()
→ client.ready()
→ client.listRuns()
→ render ready state or Backend unavailable
```

`ready()` is a health probe. It does not start a Run or create a fallback
client.

### Run catalog boundary

`MockAgentServiceClient` can expose its deterministic seed catalog. The Local
client exposes only snapshots observed by that client instance through
`getRun`/operation responses. This is a deliberately bounded desktop catalog,
not a durable history reconstruction. A future AgentService `list_runs`
contract is required for historical Run browsing; the UI must not access
SQLite, Checkpoint payloads, or workspace paths directly.

### Host bridge boundary

The Tauri host injects `window.__TSAGENT_SIDECAR_BRIDGE__`. The browser code
knows only the `TauriSidecarBridge` line-I/O lifecycle seam. Rust process
management, FastAPI, WebSocket, and Provider execution remain outside this
slice.

## Invariants

- One App composition root uses one explicit mode and identity.
- Local backend failure never creates or selects a Mock client.
- Local readiness failure is visible before Run operations are enabled.
- Every new Run uses the configured Session identity.
- Frontend production code does not import Runtime, Orchestrator, SQLite, or
  process-global workspace state.
- `listRuns()` in Local mode never reads SQLite or invents historical Runs.

## Verification

- TypeScript/Vite build passes.
- Desktop-3 Local client and Python sidecar protocol regressions pass.
- Composition-root tests cover explicit Mock mode, Local health/identity
  bootstrap, missing bridge visibility, and no Mock fallback.
