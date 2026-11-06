# Service Wire Contract — frontend integration reference

The desktop talks to `AgentService` over a single HTTP endpoint. This
document covers everything a frontend consumer needs: transport,
envelopes, identities, method semantics, and the traps we have already
hit in production.

## Transport

```
POST http://127.0.0.1:8765/rpc          — every method, JSON body
GET  http://127.0.0.1:8765/events?...   — SSE event stream (long-lived)
```

Request envelope — flat JSON-RPC style, `id`/`method`/`params`:

```json
{ "id": "req-1", "method": "start_run", "params": { "tenant_id": "...", ... } }
```

Response envelope — exactly one of two shapes (validated with `exactKeys`):

```json
{ "id": "req-1", "ok": true,  "result": { ... } }
{ "id": "req-1", "ok": false, "error": { "code": "...", "message": "...", "retryable": false } }
```

A response with extra top-level keys, a missing `id`, or an `ok` flag that
is not a boolean is a transport bug — the client surfaces it as a
`LocalTransportProtocolError`.

## Identity & sessions

Every params object carries `tenant_id`/`user_id`/`session_id`/`request_id`.

- `tenant_id`/`user_id` are the real identity scope. The desktop uses
  `tenant-local`/`user-local` (env-driven).
- **`session_id` is a locator, not an identity.** Sessions are
  first-class — the sidebar selects which session a send targets, and a
  client may read runs/events from any session. `assertIdentity` only
  enforces the tenant; enforcing session equality breaks session
  switching.
- `request_id` is the idempotency key — same `request_id` + params
  replays the stored response without re-executing. Generate one per
  user action (`requestIdForRun(n)` or `crypto.randomUUID()`).
- The page-load identity session is `session-desktop-<uuid>`, generated
  once per load. "新建任务" calls `create_session` and should then
  `setSession` so sends land in it.

## Runs

```
start_run   { request_text, task_id?, metadata? } → run handle {run_id, status}
get_run     { run_id }                             → snapshot (status/conversation/output)
list_runs                                            → RunSnapshot[] (identity scope)
read_events { run_id, after_sequence }              → RunEvent[]
subscribe_events { run_id, after_sequence }         → long-poll (~8s hold) → new events
pause_run / resume_run / cancel_run { run_id }      → lifecycle transitions
```

- `start_run` alone doesn't execute — the runtime picks up CREATED heads
  and transitions them through the loop (CREATED → RUNNING →
  COMPLETED/FAILED_TERMINAL/BLOCKED/SUSPENDED). `RUN_ALREADY_ACTIVE` means
  the session already has a live run — one active run per session.
- `output` carries the final reply (`run.output.text`) and is what the
  chat thread should render; `conversation` is the interim message list.
- `assistant_delta` events stream live tokens — fold them into the last
  assistant message for typing-effect output.
- **`refreshRun` must accept runs it has never seen** — pass the owning
  `session_id` so the locator can fetch cold runs. Runs not in the
  in-memory map are invisible to a plain `refreshRun(runId)`.

## Delegation & tasks

```
delegate_run { parent_run_id, request_text, target_planet, profile_id?, context_refs? }
             → { task_id, handoff_id, session_id, target_planet }
create_task / list_tasks / list_handoffs / list_participants / list_approvals
request_approval { run_id, action, params, risk_level } → approval record
decide_approval  { approval_id, decision }              — APPROVED / DENIED
```

- `delegate_run` needs a real `parent_run_id` — the child run anchors
  under the parent's session (`main:subtask:<task_id>`). Sending with an
  empty parent is `INVALID_REQUEST`; with no active run, fall back to a
  standalone `start_run`.
- The returned `session_id` is the **parent's** session — the child run
  lives at `main:subtask:<task_id>`, derived by convention.
- Every risk-2 tool call (`browser_click`, `browser_type`, …) stops at
  `WAITING_USER` with a `PENDING` approval — `decide_approval` unblocks it.

## Motifs (Pluto)

```
create_motif  { text, source: "user"|"auto" }         → motif_id
assign_motif  { motif_id, parent_run_id }             → delegate to pluto
resolve_motif { motif_id, resolution, resolution_run_id }
list_motifs                                           → motif rail (open/exploring/resolved)
```

Resolving writes a governed fact (`category="motif"`,
`source_kind="motif"`) into long-term memory — visible via `list_facts`.

## Workflow graphs (Uranus)

```
upsert_workflow_graph { graph_id, name, definition_json }
list_workflow_graphs / get_workflow_graph
run_graph { graph_id, parent_run_id } → per-task-node child runs
```

`run_graph` executes `task` nodes as real `delegate_run` + `start_run`
pairs in edge order; every node is a real run with approvals/events.

## Everything else

```
list_skills / list_profiles / upsert_profile / list_workspaces / upsert_workspace
create_session / list_sessions           — session registry
list_facts / forget_fact                 — governed memory
search_artifacts / list_artifacts        — generated outputs
list_schedules / upsert_schedule         — Saturn timers
record_evaluation / list_evaluations     — Earth verdicts
list_workflows                           — code-registered workflows
web_search                               — server-side search
get_settings / update_settings           — provider/model config
```

## Traps already hit — don't re-learn them

1. **`session_id` is a locator.** `assertIdentity` rejecting runs in other
   sessions killed refresh loops — only `tenant_id` is an identity check.
2. **One ACTIVE run per session.** Composer guards before sending; the
   service enforces it too — surface the message, don't send anyway.
3. **`parent_run_id` must be real.** Delegation anchors to a live run;
   empty string is INVALID_REQUEST, stale ids hit `RUN_NOT_FOUND` (the
   service now maps cross-session parents to a clean error, not 500).
4. **Output ≠ conversation.** `run.output.text` is the final reply and
   must be rendered separately — early UI only painted `conversation`
   and replies looked missing.
5. **`request_id` is the idempotency key.** Re-using it replays a stored
   result — mint a fresh one per user gesture, not per page.
6. **`refreshRun` needs the session locator.** A run absent from the
   client's map cannot be fetched by run_id alone — always pass the
   owning `session_id`.
7. **`subscribe_events` long-polls** — hold ~8s for new durable events;
   abort on navigation is normal, not an error.
8. **Response envelopes are strict** — `exactKeys({id, ok, result})`; an
   extra key or a numeric `id` fails the whole response.
