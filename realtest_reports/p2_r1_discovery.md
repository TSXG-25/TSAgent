# P2-R1 Process Crash Discovery

## Round 1 status

The first true-subprocess run intentionally preserves the production failure
instead of patching it inside the harness:

```text
R01  FAIL before crash marker
R02  FAIL before crash marker
R03  FAIL before crash marker
R04  FAIL before crash marker
```

No case is reported as a process-restart capability failure yet because the
worker did not reach the durable milestone required for the parent to send
`SIGKILL`.

## Root cause

`AgentService.start_run()` durably reserves `service.start_run` as revision 1.
The production `DurableRuntimeStoreView.bootstrap_run_index()` still requires
an empty revision-0 head.  The first Run-level workflow index therefore fails
with:

```text
RUN_INDEX_CONFLICT:
cannot bootstrap an index after the Run has begun writing
```

The second worker can acquire a new fence after the first worker closes from
that error, but no `RunResumeIndex` exists to recover, so the coordinator
reports the Run as missing.

This is an AgentService-to-RunResumeStore integration defect.  The raw report
is archived in `results/p2_r1_discovery_round1.json`.  A separate RH1 change
must fix the production bootstrap transaction before R01-R04 are rerun.

## Resolution

The discovery evidence remains immutable.  Production fixes were committed in
`39e5aea7` and addressed four boundaries exposed by the subprocess run:

1. bootstrap a `RunResumeIndex` immediately behind the proven durable
   `service.start_run` reservation;
2. explicitly take over and monotonically advance the writer fence on resume;
3. reconcile committed file effects through the scoped Run workspace;
4. finalize an older PREPARED workflow intent against the current fenced head
   after Service resume metadata advances the revision.

The authoritative rerun at `39e5aea7` reached every marker, received four real
`SIGKILL` exits, and passed R01–R04.  Its permanent report is
`results/p2_r1_round1.json` and was archived by `e0c4340d`.

## Evidence discipline

- Crash injection only writes an fsync'd observation marker.
- The parent, not the child hook, sends `SIGKILL`.
- Deterministic Workflows replace Provider variance; AgentService, SQLite,
  scoped Context/Workspace, ResumeCoordinator, WorkflowExecutor, artifacts,
  checkpoints, fences, and durable events remain production paths.
- This report is discovery evidence, not a P2-R acceptance result.
