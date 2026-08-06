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

`PASS` means only that the Dataset and pure oracle are deterministic and
complete. It does not claim that `SqliteRuntimeStore` exists or that any
production transaction has already been verified.

The future implementation gate must execute the same cases against a real
SQLite database with WAL, `synchronous=FULL`, process restart, injected crash
points, revision conflicts, fencing, and idempotency retries.
