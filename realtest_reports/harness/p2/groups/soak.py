"""Deterministic v2.3 P2-S1 soak and resource harness.

The harness deliberately uses a fake launcher, but does not mock the Runtime
infrastructure under test.  Each case goes through:

    AgentService -> ServiceContextFactory -> RunContext -> SQLite Store
    -> scoped WorkspaceService -> durable EventRepository

No Provider, Planner, or process-global workspace is involved.  The fake
launcher only supplies a deterministic, verified ``output/result.txt`` effect
so resource, scope, lifecycle, and replay invariants can be measured without
model or network variance.
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import hashlib
import json
import os
import resource
import sys
import tempfile
import time
from collections import Counter
from collections.abc import AsyncIterator, Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Keep the harness runnable both as a module and as the documented file path
# from a clean checkout.
if __package__ in {None, ""}:  # pragma: no cover - exercised by CLI smoke
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from agent.runtime_store import DurableStoreError, SqliteRuntimeStore, StoreErrorCode
from agent.service import (
    AgentService,
    EventStreamRequest,
    EventType,
    RunLookupRequest,
    RunStatus,
    ServiceContextFactory,
    StartRunRequest,
)


TERMINAL_STATUSES = frozenset(
    {RunStatus.COMPLETED, RunStatus.FAILED_TERMINAL, RunStatus.BLOCKED, RunStatus.CANCELLED}
)
ACTIVE_SQL_STATUSES = ("CREATED", "RUNNING", "SUSPENDED", "WAITING_USER", "FAILED_RECOVERABLE")


def _timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _rss_bytes() -> int:
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    # macOS reports bytes; Linux reports KiB.
    return value if sys.platform == "darwin" else value * 1024


def _open_fd_count() -> int | None:
    try:
        return len(os.listdir("/dev/fd"))
    except (FileNotFoundError, OSError):
        return None


@dataclass(frozen=True)
class ResourceSample:
    label: str
    rss_bytes: int
    open_file_descriptors: int | None
    pending_async_tasks: int
    active_run_contexts: int
    event_subscriptions: int
    sqlite_connections: int
    sqlite_busy_errors: int
    workspace_handles: int
    durable_active_runs: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "rss_bytes": self.rss_bytes,
            "open_file_descriptors": self.open_file_descriptors,
            "pending_async_tasks": self.pending_async_tasks,
            "active_run_contexts": self.active_run_contexts,
            "event_subscriptions": self.event_subscriptions,
            "sqlite_connections": self.sqlite_connections,
            "sqlite_busy_errors": self.sqlite_busy_errors,
            "workspace_handles": self.workspace_handles,
            "durable_active_runs": self.durable_active_runs,
        }


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    tenant_id: str
    session_id: str
    user_id: str
    status: str
    artifact_path: str
    artifact_digest: str
    execution_count: int
    side_effect_count: int
    event_sequences: tuple[int, ...]
    event_ids: tuple[str, ...]
    event_types: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "tenant_id": self.tenant_id,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "status": self.status,
            "artifact_path": self.artifact_path,
            "artifact_digest": self.artifact_digest,
            "execution_count": self.execution_count,
            "side_effect_count": self.side_effect_count,
            "event_sequences": list(self.event_sequences),
            "event_ids": list(self.event_ids),
            "event_types": list(self.event_types),
        }


@dataclass(frozen=True)
class SoakCaseResult:
    case_id: str
    provider: str
    run_count: int
    terminal_statuses: dict[str, int]
    records: tuple[RunRecord, ...]
    resource_samples: tuple[ResourceSample, ...]
    execution_counts: dict[str, int]
    side_effect_counts: dict[str, int]
    gates: dict[str, bool]
    runtime_correctness: str
    capability_outcome: str
    provider_errors: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    replay_cycles: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "provider": self.provider,
            "run_count": self.run_count,
            "terminal_statuses": dict(self.terminal_statuses),
            "records": [record.to_dict() for record in self.records],
            "resource_samples": [sample.to_dict() for sample in self.resource_samples],
            "execution_counts": dict(self.execution_counts),
            "side_effect_counts": dict(self.side_effect_counts),
            "gates": dict(self.gates),
            "runtime_correctness": self.runtime_correctness,
            "capability_outcome": self.capability_outcome,
            "provider_errors": list(self.provider_errors),
            "notes": list(self.notes),
            "replay_cycles": self.replay_cycles,
        }


class DeterministicSoakLauncher:
    """A real-infrastructure launcher with one deterministic local effect."""

    def __init__(self, *, barrier: asyncio.Barrier | None = None) -> None:
        self.barrier = barrier
        self.contexts: list[Any] = []
        self.execution_counts: Counter[str] = Counter()
        self.side_effect_counts: Counter[str] = Counter()
        self.artifact_digests: dict[str, str] = {}
        self.artifact_paths: dict[str, str] = {}
        self.errors: list[str] = []
        self.busy_errors = 0

    async def start(self, *, session_context: Any, run_context: Any, request: StartRunRequest) -> None:
        del session_context
        self.contexts.append(run_context)
        started = False
        try:
            run_context.event_bus.subscribe("soak", lambda _event: None)
            view = run_context.durable_store_view
            if view is None:
                raise RuntimeError("soak launcher requires a durable Run view")
            view.transition_run_with_event(
                run_status="RUNNING",
                event_id=f"soak-start:{run_context.run_id}",
                event_type="run_started",
                timestamp=_timestamp(),
                payload={"case": "P2-S1", "request_id": request.request_id},
                expected_status="CREATED",
            )
            started = True
            if self.barrier is not None:
                await self.barrier.wait()
            await asyncio.sleep(0)

            content = f"deterministic-result:{run_context.run_id}\n"
            relative_path = "output/result.txt"
            run_context.workspace.write_text(relative_path, content)
            await asyncio.sleep(0)
            artifact = run_context.workspace.resolve_path(relative_path, must_exist=True)
            actual = artifact.read_text(encoding="utf-8")
            if actual != content:
                raise RuntimeError("verified artifact content does not match the Run")

            self.execution_counts[run_context.run_id] += 1
            self.side_effect_counts[run_context.run_id] += 1
            self.artifact_digests[run_context.run_id] = _digest(actual)
            self.artifact_paths[run_context.run_id] = run_context.workspace.relative_path(artifact)
            view.transition_run_with_event(
                run_status="COMPLETED",
                event_id=f"soak-complete:{run_context.run_id}",
                event_type="run_completed",
                timestamp=_timestamp(),
                payload={
                    "case": "P2-S1",
                    "artifact_path": self.artifact_paths[run_context.run_id],
                    "artifact_digest": self.artifact_digests[run_context.run_id],
                },
            )
        except DurableStoreError as error:
            if error.code is StoreErrorCode.STORE_BUSY:
                self.busy_errors += 1
            self.errors.append(error.code.value)
            if started:
                self._fail(run_context, request, error.code.value)
            raise
        except Exception as error:
            self.errors.append(type(error).__name__)
            if started:
                self._fail(run_context, request, "SOAK_RUNTIME_FAILURE")
            raise

    async def resume(self, *, run_context: Any, request: Any) -> None:
        raise RuntimeError("P2-S1 does not exercise resume")

    @staticmethod
    def _fail(run_context: Any, request: Any, code: str) -> None:
        view = run_context.durable_store_view
        if view is None:
            return
        try:
            view.transition_run_with_event(
                run_status="FAILED_TERMINAL",
                event_id=f"soak-failed:{run_context.run_id}:{request.request_id}",
                event_type="run_failed",
                timestamp=_timestamp(),
                payload={"failure_code": code, "failed_component": "soak_launcher"},
            )
        except DurableStoreError:
            # Preserve the original launcher failure. The harness records the
            # failed transition as a runtime correctness failure.
            return


class _SoakEnvironment:
    def __init__(self, base: Path, *, barrier: asyncio.Barrier | None = None) -> None:
        self.base = base.resolve()
        self.base.mkdir(parents=True, exist_ok=True)
        self.workspace_base = self.base / "workspaces"
        self.workspace_base.mkdir(parents=True, exist_ok=True)
        self.store = SqliteRuntimeStore.open(self.base / "runtime.sqlite")
        self.launcher = DeterministicSoakLauncher(barrier=barrier)
        self.factory = ServiceContextFactory(
            self.store,
            workspace_root=self.workspace_base,
            workspace_for_run=self.workspace_for_run,
        )
        self.service = AgentService(
            runtime_store=self.store,
            launcher=self.launcher,
            context_factory=self.factory,
        )

    def workspace_for_run(self, tenant_id: str, session_id: str, run_id: str) -> Path:
        root = self.workspace_base / tenant_id / session_id / run_id
        root.mkdir(parents=True, exist_ok=True)
        return root

    def workspace_path_for_run(self, tenant_id: str, session_id: str, run_id: str) -> Path:
        return self.workspace_base / tenant_id / session_id / run_id

    def lookup(self, request: StartRunRequest, run_id: str) -> RunLookupRequest:
        return RunLookupRequest(
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            session_id=request.session_id,
            run_id=run_id,
            request_id=f"lookup:{run_id}",
        )

    def events_request(self, request: StartRunRequest, run_id: str, after: int = 0) -> EventStreamRequest:
        return EventStreamRequest(
            tenant_id=request.tenant_id,
            user_id=request.user_id,
            session_id=request.session_id,
            run_id=run_id,
            request_id=f"events:{run_id}:{after}",
            after_sequence=after,
        )

    async def wait_terminal(self, request: StartRunRequest, run_id: str) -> Any:
        for _ in range(2_000):
            snapshot = await self.service.get_run(self.lookup(request, run_id))
            if snapshot.status in TERMINAL_STATUSES:
                # The terminal state is committed before AgentService's task
                # finally block closes the RunContext. Yield until that close
                # has happened so lifecycle samples are not race-dependent.
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                return snapshot
            await asyncio.sleep(0.001)
        raise TimeoutError(f"Run did not reach a terminal state: {run_id}")

    async def collect_events(self, request: StartRunRequest, run_id: str, after: int = 0) -> tuple[Any, ...]:
        return tuple(
            [event async for event in self.service.stream_events(self.events_request(request, run_id, after))]
        )

    async def run_one(self, *, tenant_id: str, session_id: str, index: int) -> tuple[StartRunRequest, Any, tuple[Any, ...]]:
        request = StartRunRequest(
            tenant_id=tenant_id,
            user_id=f"user-{tenant_id}-{session_id}",
            session_id=session_id,
            request_id=f"soak-start:{tenant_id}:{session_id}:{index}",
            request_text="deterministic P2-S1 run",
        )
        handle = await self.service.start_run(request)
        snapshot = await self.wait_terminal(request, handle.run_id)
        events = await self.collect_events(request, handle.run_id)
        return request, snapshot, events

    def sample(self, label: str, *, loop: asyncio.AbstractEventLoop) -> ResourceSample:
        contexts = tuple(self.launcher.contexts)
        active_contexts = sum(not context.closed for context in contexts)
        subscriptions = sum(context.event_bus.subscriber_count() for context in contexts)
        workspaces = sum(
            not bool(getattr(context.workspace, "closed", True))
            for context in contexts
        )
        pending = sum(
            not task.done()
            for task in asyncio.all_tasks(loop)
            if task is not asyncio.current_task(loop)
        )
        durable_active = 0
        if not self.store.closed:
            placeholders = ",".join("?" for _ in ACTIVE_SQL_STATUSES)
            row = self.store.connection.execute(
                f"SELECT COUNT(*) FROM run_heads WHERE run_status IN ({placeholders})",
                ACTIVE_SQL_STATUSES,
            ).fetchone()
            durable_active = int(row[0]) if row is not None else 0
        return ResourceSample(
            label=label,
            rss_bytes=_rss_bytes(),
            open_file_descriptors=_open_fd_count(),
            pending_async_tasks=pending,
            active_run_contexts=active_contexts,
            event_subscriptions=subscriptions,
            sqlite_connections=0 if self.store.closed else 1,
            sqlite_busy_errors=self.launcher.busy_errors,
            workspace_handles=workspaces,
            durable_active_runs=durable_active,
        )

    async def close(self) -> None:
        await self.service.close()


def _record(
    environment: _SoakEnvironment,
    request: StartRunRequest,
    snapshot: Any,
    events: Iterable[Any],
) -> RunRecord:
    events = tuple(events)
    run_id = snapshot.run_id
    return RunRecord(
        run_id=run_id,
        tenant_id=request.tenant_id,
        session_id=request.session_id,
        user_id=request.user_id,
        status=snapshot.status.value,
        artifact_path=environment.launcher.artifact_paths.get(run_id, ""),
        artifact_digest=environment.launcher.artifact_digests.get(run_id, ""),
        execution_count=environment.launcher.execution_counts[run_id],
        side_effect_count=environment.launcher.side_effect_counts[run_id],
        event_sequences=tuple(event.sequence_number for event in events),
        event_ids=tuple(event.event_id for event in events),
        event_types=tuple(event.event_type.value for event in events),
    )


def _common_gates(
    environment: _SoakEnvironment,
    records: tuple[RunRecord, ...],
    samples: tuple[ResourceSample, ...],
) -> dict[str, bool]:
    expected_status = all(record.status == RunStatus.COMPLETED.value for record in records)
    expected_execution = all(record.execution_count == 1 for record in records)
    expected_effect = all(record.side_effect_count == 1 for record in records)
    artifact_ok = True
    for record in records:
        path = environment.workspace_path_for_run(
            record.tenant_id,
            record.session_id,
            record.run_id,
        ) / "output" / "result.txt"
        expected_content = f"deterministic-result:{record.run_id}\n"
        try:
            actual_content = path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError, UnicodeDecodeError):
            artifact_ok = False
            continue
        artifact_ok = artifact_ok and (
            record.artifact_path == "output/result.txt"
            and bool(record.artifact_digest)
            and actual_content == expected_content
            and _digest(actual_content) == record.artifact_digest
        )
    terminal_ok = all(
        record.event_types and record.event_types[-1] == EventType.RUN_COMPLETED.value
        for record in records
    )
    scope_ok = all(
        record.tenant_id and record.session_id and record.run_id
        and record.event_sequences == tuple(range(1, len(record.event_sequences) + 1))
        for record in records
    )
    post = samples[-1]
    memory_scope = all(
        context.session.memory_view.namespace
        == f"{context.tenant_id}:{context.user_id}"
        for context in environment.launcher.contexts
    )
    return {
        "cross_context_leakage": scope_ok and artifact_ok,
        "workspace_leakage": artifact_ok and all(
            record.artifact_path == "output/result.txt" for record in records
        ),
        "memory_scope": memory_scope,
        "duplicate_side_effect": expected_execution and expected_effect,
        "false_completed": expected_status and artifact_ok and terminal_ok,
        "orphan_active_run": post.durable_active_runs == 0 and post.active_run_contexts == 0,
        "subscriber_leak": post.event_subscriptions == 0 and post.workspace_handles == 0,
        "sqlite_deadlock_or_busy_failure": not environment.launcher.errors
        and environment.launcher.busy_errors == 0,
        "terminal_snapshot_event_mismatch": expected_status and terminal_ok,
    }


def _result(
    case_id: str,
    environment: _SoakEnvironment,
    records: tuple[RunRecord, ...],
    samples: tuple[ResourceSample, ...],
    *,
    replay_cycles: int = 0,
    extra_gates: dict[str, bool] | None = None,
    notes: tuple[str, ...] = (),
) -> SoakCaseResult:
    gates = _common_gates(environment, records, samples)
    if extra_gates:
        gates.update(extra_gates)
    return SoakCaseResult(
        case_id=case_id,
        provider="deterministic-fake",
        run_count=len(records),
        terminal_statuses=dict(Counter(record.status for record in records)),
        records=records,
        resource_samples=samples,
        execution_counts=dict(environment.launcher.execution_counts),
        side_effect_counts=dict(environment.launcher.side_effect_counts),
        gates=gates,
        runtime_correctness="PASS" if all(gates.values()) else "FAIL",
        capability_outcome="PASS" if all(record.status == RunStatus.COMPLETED.value for record in records) else "FAIL",
        provider_errors=tuple(environment.launcher.errors),
        notes=notes,
        replay_cycles=replay_cycles,
    )


async def _run_sequential(base: Path, *, sessions: int, runs_per_session: int, case_id: str) -> SoakCaseResult:
    environment = _SoakEnvironment(base)
    records: list[RunRecord] = []
    samples: list[ResourceSample] = [
        environment.sample("baseline", loop=asyncio.get_running_loop())
    ]
    try:
        for session_number in range(sessions):
            session_id = f"session-{session_number:02d}"
            for index in range(runs_per_session):
                request, snapshot, events = await environment.run_one(
                    tenant_id="tenant-soak",
                    session_id=session_id,
                    index=index,
                )
                records.append(_record(environment, request, snapshot, events))
                if len(records) % 10 == 0:
                    samples.append(
                        environment.sample(
                            f"run-{len(records)}", loop=asyncio.get_running_loop()
                        )
                    )
        samples.append(environment.sample("pre-close", loop=asyncio.get_running_loop()))
        await environment.close()
        gc.collect()
        samples.append(environment.sample("post-close-gc", loop=asyncio.get_running_loop()))
        return _result(case_id, environment, tuple(records), tuple(samples))
    finally:
        if not environment.service.closed:
            await environment.close()


async def run_s01(base: Path, *, run_count: int = 50) -> SoakCaseResult:
    if run_count < 1:
        raise ValueError("run_count must be positive")
    return await _run_sequential(base, sessions=1, runs_per_session=run_count, case_id="S01")


async def run_s02(base: Path, *, sessions: int = 10, runs_per_session: int = 5) -> SoakCaseResult:
    if sessions < 1 or runs_per_session < 1:
        raise ValueError("sessions and runs_per_session must be positive")
    return await _run_sequential(base, sessions=sessions, runs_per_session=runs_per_session, case_id="S02")


async def run_s03(base: Path, *, run_count: int = 10) -> SoakCaseResult:
    if run_count < 1:
        raise ValueError("run_count must be positive")
    barrier = asyncio.Barrier(run_count)
    environment = _SoakEnvironment(base, barrier=barrier)
    samples: list[ResourceSample] = [
        environment.sample("baseline", loop=asyncio.get_running_loop())
    ]
    try:
        started = await asyncio.gather(
            *(
                environment.service.start_run(
                    StartRunRequest(
                        tenant_id="tenant-soak",
                        user_id=f"user-concurrent-{index}",
                        session_id=f"session-concurrent-{index}",
                        request_id=f"soak-concurrent:{index}",
                        request_text="deterministic concurrent P2-S1 run",
                    )
                )
                for index in range(run_count)
            )
        )
        records: list[RunRecord] = []
        for index, handle in enumerate(started):
            request = StartRunRequest(
                tenant_id="tenant-soak",
                user_id=f"user-concurrent-{index}",
                session_id=f"session-concurrent-{index}",
                request_id=f"soak-concurrent:{index}",
                request_text="deterministic concurrent P2-S1 run",
            )
            snapshot = await environment.wait_terminal(request, handle.run_id)
            events = await environment.collect_events(request, handle.run_id)
            records.append(_record(environment, request, snapshot, events))
        samples.append(environment.sample("run-10", loop=asyncio.get_running_loop()))
        samples.append(environment.sample("pre-close", loop=asyncio.get_running_loop()))
        await environment.close()
        gc.collect()
        samples.append(environment.sample("post-close-gc", loop=asyncio.get_running_loop()))
        return _result("S03", environment, tuple(records), tuple(samples), notes=("forced_barrier_interleaving",))
    finally:
        if not environment.service.closed:
            await environment.close()


async def run_s04(base: Path, *, replay_cycles: int = 500) -> SoakCaseResult:
    if replay_cycles < 1:
        raise ValueError("replay_cycles must be positive")
    environment = _SoakEnvironment(base)
    samples: list[ResourceSample] = [
        environment.sample("baseline", loop=asyncio.get_running_loop())
    ]
    try:
        request, snapshot, initial = await environment.run_one(
            tenant_id="tenant-soak",
            session_id="session-replay",
            index=0,
        )
        expected_ids = tuple(event.event_id for event in initial)
        expected_sequences = tuple(event.sequence_number for event in initial)
        expected_types = tuple(event.event_type.value for event in initial)
        replay_failures: list[str] = []
        for cycle in range(replay_cycles):
            full = await environment.collect_events(request, snapshot.run_id, after=0)
            suffix = await environment.collect_events(request, snapshot.run_id, after=1)
            if tuple(event.event_id for event in full) != expected_ids:
                replay_failures.append(f"full_event_ids@{cycle}")
            if tuple(event.sequence_number for event in full) != expected_sequences:
                replay_failures.append(f"full_sequences@{cycle}")
            if tuple(event.event_id for event in suffix) != expected_ids[1:]:
                replay_failures.append(f"cursor_suffix@{cycle}")
            if tuple(event.event_type.value for event in full) != expected_types:
                replay_failures.append(f"event_types@{cycle}")
        samples.append(environment.sample("replay-500", loop=asyncio.get_running_loop()))
        rows_before_close = int(
            environment.store.connection.execute(
                "SELECT COUNT(*) FROM run_events WHERE tenant_id = ? AND run_id = ?",
                (request.tenant_id, snapshot.run_id),
            ).fetchone()[0]
        )
        samples.append(environment.sample("pre-close", loop=asyncio.get_running_loop()))
        await environment.close()
        gc.collect()
        samples.append(environment.sample("post-close-gc", loop=asyncio.get_running_loop()))
        extra = {
            "event_gap": not replay_failures and expected_sequences == tuple(range(1, len(initial) + 1)),
            "cross_context_leakage": not replay_failures,
            "subscriber_leak": samples[-1].event_subscriptions == 0,
            "terminal_snapshot_event_mismatch": (
                snapshot.status is RunStatus.COMPLETED
                and bool(initial)
                and initial[-1].event_type is EventType.RUN_COMPLETED
            ),
            "replay_does_not_append": rows_before_close == len(initial),
        }
        return _result(
            "S04",
            environment,
            (_record(environment, request, snapshot, initial),),
            tuple(samples),
            replay_cycles=replay_cycles,
            extra_gates=extra,
            notes=(
                f"event_rows_before_close={rows_before_close}",
                *(replay_failures[:10]),
            ),
        )
    finally:
        if not environment.service.closed:
            await environment.close()


async def run_case(case_id: str, base: Path) -> SoakCaseResult:
    normalized = case_id.upper()
    if normalized == "S01":
        return await run_s01(base)
    if normalized == "S02":
        return await run_s02(base)
    if normalized == "S03":
        return await run_s03(base)
    if normalized == "S04":
        return await run_s04(base)
    raise ValueError(f"unknown P2-S1 case: {case_id}")


async def run_all(base: Path) -> tuple[SoakCaseResult, ...]:
    results: list[SoakCaseResult] = []
    for case_id in ("S01", "S02", "S03", "S04"):
        case_base = base / case_id
        results.append(await run_case(case_id, case_base))
    return tuple(results)


def _write_report(path: Path, results: tuple[SoakCaseResult, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "harness": "p2-s1-deterministic-soak",
        "version": "v0.1",
        "provider": "deterministic-fake",
        "cases": [result.to_dict() for result in results],
        "all_runtime_gates_pass": all(
            result.runtime_correctness == "PASS" for result in results
        ),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run deterministic P2-S1 soak cases")
    parser.add_argument("--case", default="all", choices=("all", "S01", "S02", "S03", "S04"))
    parser.add_argument("--workspace", type=Path, default=None)
    parser.add_argument(
        "--results",
        type=Path,
        default=Path("realtest_reports/results/p2_s1_deterministic.json"),
    )
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="tsagent-p2-s1-") as temporary:
        base = args.workspace or Path(temporary)
        if args.case == "all":
            results: tuple[SoakCaseResult, ...] = asyncio.run(run_all(base))
        else:
            results = (asyncio.run(run_case(args.case, base / args.case)),)
    _write_report(args.results, results)
    for result in results:
        passed = sum(result.gates.values())
        print(
            f"{result.case_id}: {result.runtime_correctness} "
            f"({passed}/{len(result.gates)} gates, runs={result.run_count})"
        )
    return 0 if all(result.runtime_correctness == "PASS" for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ResourceSample",
    "RunRecord",
    "SoakCaseResult",
    "run_all",
    "run_case",
    "run_s01",
    "run_s02",
    "run_s03",
    "run_s04",
]
