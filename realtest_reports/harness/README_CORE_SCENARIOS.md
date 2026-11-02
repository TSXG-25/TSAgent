# Core Scenarios — real-provider multi-turn regression harness

`core_scenarios.py` drives a fixed set of ~16 multi-turn cases
(`core_scenarios_cases.json`) through the **real configured LLM provider**
against the durable `AgentService` spine: fresh SQLite store per case,
per-case workspace directory, real intent → plan → execute → verify flow.

> **Caveat — this is a real-scenario test artifact.** Per the AGENTS.md
> testing boundary, these runs validate real provider behavior that offline
> tests cannot prove. They are **not** part of the offline pytest suite and
> produce no offline-proof claim. Results depend on the live provider/model;
> a `FAIL` here is a real-scenario signal to investigate, not a deterministic
> contract verdict on its own.

## Running

```bash
# list cases offline (no provider needed)
python -m realtest_reports.harness.core_scenarios --list

# run everything against the configured provider (.env OPENAI_API_KEY)
python -m realtest_reports.harness.core_scenarios

# one case / a subset
python -m realtest_reports.harness.core_scenarios --case CS05 --case CS08
python -m realtest_reports.harness.core_scenarios --case CS05,CS13

# force a provider (same values as TSAGENT_LLM_PROVIDER)
python -m realtest_reports.harness.core_scenarios --provider ollama
```

Provider gate: the harness reads `.env` (same `load_dotenv` convention as
`agent.llm`) and **aborts with exit code 2 before any provider call** when
no usable provider exists — `auto`/`deepseek` require `OPENAI_API_KEY`,
`ollama` requires a reachable `OLLAMA_BASE_URL`. It never fakes a provider.

Outputs per run:

- `realtest_reports/results/core_scenarios_<timestamp>.json` — full record:
  per-turn request text, intent output, call counts, status, output text,
  verified artifacts, check verdicts, and the actual file contents produced.
- `realtest_reports/results/core_scenarios_<timestamp>.md` — pass/fail
  summary table + failed-check details.
- `realtest_reports/results/core_scenarios_<timestamp>_workspace/<case>/` —
  the per-case workspace (produced files + `runtime.sqlite`) kept for
  inspection.

Exit code: `0` when no case failed/errored (skips allowed), `1` otherwise,
`2` for the provider gate.

## What is recorded per turn

| Field | Meaning |
| --- | --- |
| `status` | terminal `RunStatus` of the durable run |
| `output` | `RunOutput.text` (the user-visible answer) |
| `artifacts` | `ArtifactSummary` list (reference, digest, verified flag) |
| `provider_calls` | calls through the shared `agent.llm` router this turn |
| `plan_calls` / `replan_calls` | orchestrator `plan` / `replan` + `recover_structural_failure` invocations |
| `tool_calls` | `_exec_tool` boundary invocations (name + truncated args) |
| `intent` | last `IntentResult` this turn projected to JSON: domain, action, `artifact_required`, `requested_outcomes`, `delivery_requirements`, `acceptance_criteria`, `write_targets`, `conversation_relation`, `resolved_request`, `memory_action`, `prohibited_effects`, `external_effects`, `search_*` |
| `failure_summary` / `error` | durable failure payload or harness-level error/timeout |

Instrumentation is a recording wrapper on the production singletons
(`agent.llm.llm`, `intent_engine.engine`, `ExecutionOrchestrator`,
`PlanExecutor._exec_tool`) — the same objects the runtime uses.

## How checks are declared

Each case in `core_scenarios_cases.json`:

```json
{
  "id": "CS05",
  "title": "follow-up modifies the artifact created in turn 1",
  "turns": ["...", "..."],
  "seed_files": {"input/note.txt": "..."},          // optional, written before turn 1
  "restart_after_turn": 1,                            // optional: close+reopen
                                                    // the service on the same sqlite
                                                    // before the next turn
  "skip_if": "no_search_provider",                    // optional: mark SKIP when the
                                                    // DuckDuckGo MCP server is absent.
                                                    // "no_execution_environment" skips
                                                    // when neither Docker nor
                                                    // TSAGENT_ALLOW_LOCAL_EXECUTION
                                                    // provides command execution.
  "checks": {"turns": [ { /* per-turn check spec */ } ]}
}
```

Per-turn check keys (all deterministic):

- `status` / `status_in` — terminal status expectation (default `COMPLETED`).
- `output_contains` / `output_not_contains` — token(s) in the answer text.
- `artifact_exists` / `artifact_absent` — workspace-relative glob(s) that
  must match ≥1 file / no files (`artifact_exists_any` for alternatives).
- `file_contains` / `file_not_contains` — `{glob: [tokens]}`: some matched
  file contains all tokens / no matched file contains any token.
- `provider_calls_max` / `provider_calls_min` — LLM call budget.
- `plan_calls_max` / `replan_calls_max` — plan/replan bounds.
- `no_tool_calls`, `no_tools`, `tools_any`, `tools_all` — tool boundary.
- `intent` — field expectations on the recorded intent: `domain`,
  `action`, `conversation_relation` (+ `_in` list variants),
  `memory_action`, `artifact_required`, `requires_execution`,
  `resolved_request_contains`, `write_targets_contains`, `target_contains`,
  `requested_outcomes_contains` / `_excludes`,
  `prohibited_effects_contains`.

Case results: `PASS` (all checks), `FAIL` (any check failed), `ERROR`
(harness-level exception), `SKIP` (declared skip condition met, e.g.
`CS10` without a search provider).
