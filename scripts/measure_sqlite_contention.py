#!/usr/bin/env python3
"""A17 measurement: SQLite lock contention + history growth.

Two-connection probe — a raw sqlite3 writer holds BEGIN IMMEDIATE on the
same file while the store serves a control read (get_run_head) and a write
(append_event).  WAL mode is expected to keep reads unblocked; writes must
wait for the writer to release or hit busy_timeout.  History growth is
measured by seeding representative runs/events and diffing the file size.

Reproducible:  python scripts/measure_sqlite_contention.py
Prints sample sizes, lock hold time, per-op latencies, and bytes/event.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.runtime_store.sqlite import SqliteRuntimeStore  # noqa: E402
from agent.runtime_store.errors import (  # noqa: E402
    DurableStoreError,
    StoreErrorCode,
)

RUNS = 20
EVENTS_PER_RUN = 25
PAYLOAD_BYTES = 512
HOLD_SECONDS = 0.20
SAMPLES = 10


def _seed_run(store: SqliteRuntimeStore, idx: int) -> tuple[str, str]:
    # one active run per session — seed each run under its own session
    session_id = f"session-{idx:03d}"
    reservation = store.reserve_service_start(
        "tenant-m", session_id,
        task_id=f"task-{idx}",
        requested_run_id=f"run-{idx:03d}",
        request_id=f"req-{idx:03d}",
        request_digest="d",
        writer_id="probe",
        external_reference="",
    )
    return session_id, reservation.head.run_id


def _append(store: SqliteRuntimeStore, session_id: str, run_id: str, seq: int) -> None:
    store.append_event(
        "tenant-m", session_id, run_id,
        event_id=f"ev-{run_id}-{seq}",
        event_type="agent_event",
        timestamp="2026-01-01T00:00:00Z",
        payload={"data": "x" * PAYLOAD_BYTES, "seq": seq},
    )


def _timed(fn, samples: int) -> tuple[float, float]:
    best, worst = float("inf"), 0.0
    for _ in range(samples):
        t0 = time.perf_counter()
        fn()
        dt = time.perf_counter() - t0
        best, worst = min(best, dt), max(worst, dt)
    return best, worst


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "runtime.sqlite"
        store = SqliteRuntimeStore.open(db)
        seeded = [_seed_run(store, i) for i in range(RUNS)]
        for session_id, rid in seeded:
            for seq in range(EVENTS_PER_RUN):
                _append(store, session_id, rid, seq)

        size_before = os.path.getsize(db)
        session_0, run_0 = seeded[0]

        # --- baseline latencies (no contention) ---
        read_base = _timed(
            lambda: store.get_run_head("tenant-m", run_0, session_id=session_0),
            SAMPLES,
        )
        # 控制请求路径 — list_runs 类 RPC 落到 list_run_heads。
        control_base = _timed(
            lambda: store.list_run_heads("tenant-m", session_0),
            SAMPLES,
        )
        # worker lease — 运行中的 Run 周期性 renew，锁争用决定心跳延迟。
        fence = store.get_current_fence(
            "tenant-m", run_0, session_id=session_0
        )

        def lease_once() -> None:
            store.renew_worker_lease(
                "tenant-m", session_0, run_0,
                fence.writer_id, fence.fence_token,
            )

        lease_base = _timed(lease_once, SAMPLES)
        seq = [EVENTS_PER_RUN]

        def write_once() -> None:
            seq[0] += 1
            _append(store, session_0, run_0, seq[0])

        write_base = _timed(write_once, SAMPLES)

        def timed_expect_busy() -> float:
            t0 = time.perf_counter()
            try:
                write_once()
            except DurableStoreError as exc:
                if exc.code is not StoreErrorCode.STORE_BUSY:
                    raise
                return time.perf_counter() - t0
            raise AssertionError("write under held lock did not raise STORE_BUSY")

        # --- contended: a raw second connection holds BEGIN IMMEDIATE ---
        released = threading.Event()

        def hold_writer(seconds: float) -> None:
            # BEGIN IMMEDIATE alone acquires the WAL write lock — no write
            # needed to starve a second writer.
            contender = sqlite3.connect(db, check_same_thread=False)
            try:
                contender.execute("BEGIN IMMEDIATE")
                time.sleep(seconds)
            finally:
                contender.rollback()
                contender.close()
            released.set()

        t = threading.Thread(target=hold_writer, args=(HOLD_SECONDS,))
        t.start()
        time.sleep(0.05)  # let the writer acquire the lock

        read_lock = _timed(
            lambda: store.get_run_head("tenant-m", run_0, session_id=session_0),
            3,
        )
        control_lock = _timed(
            lambda: store.list_run_heads("tenant-m", session_0),
            3,
        )
        lease_lock = _timed(lease_once, 3)
        write_lock = _timed(write_once, 3)

        t.join(timeout=10)
        assert released.is_set(), "contending writer never released"

        # --- hold beyond busy_timeout: the store surfaces STORE_BUSY ---
        released.clear()
        t2 = threading.Thread(target=hold_writer, args=(6.0,))
        t2.start()
        time.sleep(0.05)
        busy_wait = timed_expect_busy()
        busy_error = "STORE_BUSY"
        t2.join(timeout=15)
        assert released.is_set()

        store.close()

        # --- growth ---
        size_after = os.path.getsize(db)
        wal = Path(str(db) + "-wal")
        wal_size = os.path.getsize(wal) if wal.exists() else 0
        total_events = RUNS * EVENTS_PER_RUN + SAMPLES + 3

    print("=== A17 SQLite contention + growth ===")
    print(f"sample: {RUNS} runs x {EVENTS_PER_RUN} events (+{SAMPLES + 3} probe writes), payload {PAYLOAD_BYTES}B")
    print(f"lock hold: {HOLD_SECONDS * 1000:.0f} ms (raw second connection, BEGIN IMMEDIATE)")
    print(f"read  baseline min/max: {read_base[0] * 1e3:.1f}/{read_base[1] * 1e3:.1f} ms")
    print(f"read  under-lock min/max: {read_lock[0] * 1e3:.1f}/{read_lock[1] * 1e3:.1f} ms")
    print(f"control(list_run_heads) baseline min/max: {control_base[0] * 1e3:.1f}/{control_base[1] * 1e3:.1f} ms")
    print(f"control(list_run_heads) under-lock min/max: {control_lock[0] * 1e3:.1f}/{control_lock[1] * 1e3:.1f} ms")
    print(f"lease(renew_worker_lease) baseline min/max: {lease_base[0] * 1e3:.1f}/{lease_base[1] * 1e3:.1f} ms")
    print(f"lease(renew_worker_lease) under-lock min/max: {lease_lock[0] * 1e3:.1f}/{lease_lock[1] * 1e3:.1f} ms")
    print(f"write baseline min/max: {write_base[0] * 1e3:.1f}/{write_base[1] * 1e3:.1f} ms")
    print(f"write under-lock min/max: {write_lock[0] * 1e3:.1f}/{write_lock[1] * 1e3:.1f} ms")
    print(f"hold > busy_timeout: write failed after {busy_wait:.1f}s as {busy_error}")
    print(f"db file: {size_before} -> {size_after} bytes (+{size_after - size_before})")
    print(f"wal size at close: {wal_size} bytes")
    print(f"bytes/event approx: {(size_after + wal_size) / total_events:.0f}")


if __name__ == "__main__":
    main()
