# v2.3B Durable SQLite Runtime Store Dataset

This Dataset freezes the transaction and crash semantics before the SQLite
implementation is written.

It covers:

- canonical schema/codec facts;
- durable Preparation intent before an external side effect;
- rollback at each pre-commit write window;
- same-key/same-digest retry, same-key conflict, and different-key independence;
- revision CAS, monotonic fence takeover, and stale-writer fencing;
- external side-effect reconciliation and unknown-result blocking;
- process restart rehydration;
- consistent multi-table read snapshots.

Run the contract validator from the repository root:

```bash
python -B -m benchmarks.v23b.validate
```

`PASS` means that the Dataset and pure oracle are deterministic and complete.
It does not by itself claim that the full production transaction bundle has
been verified.  v2.3B-2 now has an independent SQLite primitive gate:

```bash
pytest -q tests/test_sqlite_runtime_store.py
mypy agent/runtime_store tests/test_sqlite_runtime_store.py
```

That gate covers bootstrap, fence, revision CAS, idempotency and Preparation
intent.  Checkpoint/Artifact finalization and crash injection remain deferred
to v2.3B-4.  The B-3 Finalization Bundle gate is:

```bash
pytest -q tests/test_sqlite_finalization.py
```

It proves the verified Checkpoint, Artifact metadata, committed idempotency
result, RunResume revision and Run Head become visible as one SQLite commit;
it does not yet switch the WorkflowExecutor to this Store.

v2.3B-4 now adds the production-path and crash gate:

```bash
pytest -q \
  tests/test_durable_runtime_migration.py \
  tests/test_v23b_crash_harness.py \
  tests/test_v23b_architecture_gate.py
```

This gate exercises the configured `ApplicationContext`/`RunContext` SQLite
view, the Coordinator → WorkflowExecutor prepare/finalize boundary, and eight
real subprocess cases (R01–R08).  The subprocesses use independent SQLite
connections and `os._exit` for process-crash windows.  The execution-stage
Checkpoint facts are held in an explicit staging buffer and are published
only by the Finalization Bundle; there is no production JSON/InMemory writer.

The B-4 acceptance condition is:

```text
production durable path       PASS
prepare-before-effect         PASS
atomic finalization           PASS
crash/restart R01-R08         8/8 PASS
duplicate side effects        0
stale writer acceptance       0
architecture boundary         PASS
```
