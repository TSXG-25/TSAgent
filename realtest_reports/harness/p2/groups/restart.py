"""P2-R1 true process-crash and restart acceptance harness.

The parent process starts a real AgentService worker, waits for a durable
milestone marker, sends SIGKILL, and then starts a second process against the
same SQLite database and Run workspace.  Runtime safety decisions are derived
from SQLite, checkpoint, event, audit, and filesystem ground truth.

No Provider or model call is involved.  The child uses deterministic Workflow
definitions while retaining the production AgentService, scoped Context,
RunResumeCoordinator, WorkflowExecutor, SQLite Store, and Workspace paths.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Mapping, cast

if __package__ in {None, ""}:  # pragma: no cover - direct CLI execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from agent.runtime_store import DurableStoreError, SqliteRuntimeStore, StoreErrorCode
from benchmarks.p2.cases import P2Group, build_cases
from benchmarks.p2.metadata import benchmark_metadata, dataset_hash
from benchmarks.p2.oracle import evaluate

from realtest_reports.harness.p2.groups.restart_worker import CRASH_POINTS


TENANT_ID = "tenant-p2r"
SESSION_ID = "session-p2r"
EXPECTED_EFFECTS: dict[str, dict[str, tuple[str, str]]] = {
    "R01": {
        "r01-stage-1": ("output/r01-a.txt", "r01-a\n"),
        "r01-stage-2": ("output/r01-b.txt", "r01-b\n"),
    },
    "R02": {
        "r02-effect-write": ("output/effect.txt", "external-effect-once\n"),
    },
    "R03": {
        "r03-event-write": ("output/event.txt", "event-checkpoint\n"),
    },
    "R04": {
        "r04-a-write": ("output/a.txt", "workflow-a\n"),
        "r04-b-write": ("output/b.txt", "workflow-b\n"),
    },
}
PRECOMPLETED_TASKS = {
    "R01": ("r01-stage-1",),
    "R02": (),
    "R03": ("r03-event-write",),
    "R04": ("r04-a-write",),
}


def _commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return {"read_error": f"{type(error).__name__}: {error}"}
    return value if isinstance(value, dict) else {"value": value}


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if is_dataclass(value):
        return {
            str(key): _jsonable(item)
            for key, item in asdict(cast(Any, value)).items()
        }
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        return [_jsonable(item) for item in value]
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _jsonable(to_dict())
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, (str, int, float, bool)):
        return enum_value
    return str(value)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _table_rows(database: Path, table: str, run_id: str) -> list[dict[str, Any]]:
    if not database.exists():
        return []
    allowed = {
        "artifact_metadata",
        "idempotency_ledger",
        "run_fences",
        "run_resume_revisions",
    }
    if table not in allowed:
        raise ValueError(f"unsupported diagnostic table: {table}")
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        rows = connection.execute(
            f"SELECT * FROM {table} WHERE tenant_id = ? AND run_id = ? ORDER BY rowid",
            (TENANT_ID, run_id),
        ).fetchall()
        return [dict(row) for row in rows]
    except sqlite3.DatabaseError as error:
        return [{"database_error": f"{type(error).__name__}: {error}"}]
    finally:
        connection.close()


def _workspace_files(workspace: Path) -> dict[str, dict[str, Any]]:
    if not workspace.exists():
        return {}
    result: dict[str, dict[str, Any]] = {}
    for path in sorted(item for item in workspace.rglob("*") if item.is_file()):
        relative = path.relative_to(workspace).as_posix()
        value = path.read_bytes()
        result[relative] = {
            "size": len(value),
            "digest": _sha256_bytes(value),
        }
    return result


def _checkpoint_summary(checkpoint: Any) -> dict[str, Any]:
    value = _jsonable(checkpoint)
    return value if isinstance(value, dict) else {"value": value}


def _durable_snapshot(database: Path, workspace: Path, case_id: str) -> dict[str, Any]:
    run_id = f"run-{case_id.lower()}"
    if not database.exists():
        return {
            "run_id": run_id,
            "database_exists": False,
            "workspace_files": _workspace_files(workspace),
        }
    store: SqliteRuntimeStore | None = None
    try:
        store = SqliteRuntimeStore.open(database)
        head = store.get_run_head(TENANT_ID, run_id, session_id=SESSION_ID)
        index = store.get_run_index(TENANT_ID, run_id, session_id=SESSION_ID)
        checkpoints = store.checkpoint_history(
            TENANT_ID,
            run_id,
            session_id=SESSION_ID,
        )
        events = store.read_events(
            TENANT_ID,
            run_id,
            session_id=SESSION_ID,
            after_sequence=0,
        )
        fence = store.get_current_fence(TENANT_ID, run_id, session_id=SESSION_ID)
        return {
            "run_id": run_id,
            "database_exists": True,
            "head": _jsonable(head),
            "run_index": _jsonable(index),
            "checkpoints": [_checkpoint_summary(item) for item in checkpoints],
            "events": [_jsonable(item) for item in events],
            "current_fence": _jsonable(fence),
            "artifacts": _table_rows(database, "artifact_metadata", run_id),
            "ledger": _table_rows(database, "idempotency_ledger", run_id),
            "fence_history": _table_rows(database, "run_fences", run_id),
            "revision_history": _table_rows(
                database,
                "run_resume_revisions",
                run_id,
            ),
            "workspace_files": _workspace_files(workspace),
        }
    except Exception as error:
        return {
            "run_id": run_id,
            "database_exists": True,
            "inspection_error": {
                "type": type(error).__name__,
                "code": str(getattr(getattr(error, "code", None), "value", "")),
                "message": str(error)[:500],
            },
            "workspace_files": _workspace_files(workspace),
        }
    finally:
        if store is not None:
            store.close()


def _audit(path: Path) -> tuple[list[dict[str, Any]], Counter[str]]:
    records: list[dict[str, Any]] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                records.append({"invalid_json": line[:300]})
                continue
            records.append(value if isinstance(value, dict) else {"value": value})
    counts = Counter(
        str(record.get("task_id", ""))
        for record in records
        if str(record.get("event", "")) == "effect_committed"
        and str(record.get("task_id", ""))
    )
    return records, counts


def _start_process(
    *,
    mode: str,
    case_id: str,
    case_root: Path,
    database: Path,
    workspace: Path,
    marker: Path,
    audit: Path,
    result: Path,
) -> tuple[subprocess.Popen[bytes], Any, Any]:
    worker = Path(__file__).with_name("restart_worker.py")
    stdout_handle = (case_root / f"worker-{mode}.stdout.log").open("wb")
    stderr_handle = (case_root / f"worker-{mode}.stderr.log").open("wb")
    environment = dict(os.environ)
    repository = str(Path(__file__).resolve().parents[4])
    environment["PYTHONPATH"] = (
        repository
        if not environment.get("PYTHONPATH")
        else repository + os.pathsep + environment["PYTHONPATH"]
    )
    if mode == "start":
        environment["TSAGENT_TEST_CRASH_POINT"] = CRASH_POINTS[case_id]
    else:
        environment.pop("TSAGENT_TEST_CRASH_POINT", None)
    process = subprocess.Popen(
        [
            sys.executable,
            "-B",
            str(worker),
            "--mode",
            mode,
            "--case",
            case_id,
            "--database",
            str(database),
            "--workspace",
            str(workspace),
            "--marker",
            str(marker),
            "--audit",
            str(audit),
            "--result",
            str(result),
        ],
        cwd=case_root,
        env=environment,
        stdout=stdout_handle,
        stderr=stderr_handle,
    )
    return process, stdout_handle, stderr_handle


def _wait_for_marker(process: subprocess.Popen[bytes], marker: Path, timeout: float) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if marker.exists():
            return "MARKER_OBSERVED"
        if process.poll() is not None:
            return "WORKER_EXITED"
        time.sleep(0.02)
    return "TIMEOUT"


def _run_resume_process(process: subprocess.Popen[bytes], timeout: float) -> int | None:
    try:
        return process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
        return None


def _probe_stale_writer(
    database: Path,
    case_id: str,
    pre: Mapping[str, Any],
    post: Mapping[str, Any],
) -> dict[str, Any]:
    pre_head = _mapping(pre.get("head"))
    post_head = _mapping(post.get("head"))
    old_token = int(pre_head.get("current_fence_token", 0) or 0)
    new_token = int(post_head.get("current_fence_token", 0) or 0)
    if old_token <= 0 or new_token <= old_token:
        return {
            "attempted": False,
            "accepted": False,
            "reason": "no proven fence takeover",
            "old_token": old_token,
            "new_token": new_token,
        }
    store = SqliteRuntimeStore.open(database)
    try:
        head = store.get_run_head(
            TENANT_ID,
            f"run-{case_id.lower()}",
            session_id=SESSION_ID,
        )
        assert head is not None
        try:
            store.append_revision(
                TENANT_ID,
                SESSION_ID,
                f"run-{case_id.lower()}",
                request_id=f"stale-probe-{case_id.lower()}",
                payload={"probe": "stale_writer_must_not_commit"},
                writer_id="writer-a",
                fence_token=old_token,
                expected_revision=head.current_revision,
                expected_parent_digest=head.current_digest,
                run_status=head.run_status,
                expected_store_generation=store.store_generation,
            )
        except DurableStoreError as error:
            return {
                "attempted": True,
                "accepted": False,
                "code": error.code.value,
                "old_token": old_token,
                "new_token": new_token,
            }
        return {
            "attempted": True,
            "accepted": True,
            "code": "",
            "old_token": old_token,
            "new_token": new_token,
        }
    finally:
        store.close()


def _marker_valid(case_id: str, marker: Mapping[str, Any], pre: Mapping[str, Any]) -> bool:
    if marker.get("point") != CRASH_POINTS[case_id]:
        return False
    index = _mapping(pre.get("run_index"))
    checkpoints = _list(pre.get("checkpoints"))
    files = _mapping(pre.get("workspace_files"))
    if case_id == "R01":
        completed = {
            item
            for checkpoint in checkpoints
            if isinstance(checkpoint, Mapping)
            for item in checkpoint.get("completed_task_ids", []) or []
        }
        return index.get("active_workflow_id") == "wf-main" and "r01-stage-1" in completed
    if case_id == "R02":
        ledger = _list(pre.get("ledger"))
        return "output/effect.txt" in files and any(
            str(item.get("effect_state", "")) == "PREPARED"
            for item in ledger
            if isinstance(item, Mapping)
        )
    if case_id == "R03":
        return (
            "wf-event" in set(index.get("completed_workflow_ids", []) or [])
            and bool(checkpoints)
        )
    return (
        "wf-a" in set(index.get("completed_workflow_ids", []) or [])
        and index.get("active_workflow_id") == "wf-b"
        and "output/a.txt" in files
    )


def _evaluate_case(
    case_id: str,
    *,
    marker: Mapping[str, Any],
    pre: Mapping[str, Any],
    post: Mapping[str, Any],
    audit_counts: Counter[str],
    stale_probe: Mapping[str, Any],
    worker_a_returncode: int | None,
    worker_b_returncode: int | None,
    legacy_root: Path,
) -> tuple[dict[str, bool], dict[str, Any]]:
    post_head = _mapping(post.get("head"))
    post_index = _mapping(post.get("run_index"))
    post_events = _list(post.get("events"))
    post_files = _mapping(post.get("workspace_files"))
    sequences = [
        int(item.get("sequence_number", 0) or 0)
        for item in post_events
        if isinstance(item, Mapping)
    ]
    event_ids = [
        str(item.get("event_id", ""))
        for item in post_events
        if isinstance(item, Mapping)
    ]
    event_types = [
        str(item.get("event_type", ""))
        for item in post_events
        if isinstance(item, Mapping)
    ]
    required_files_ok = all(
        relative in post_files
        and _mapping(post_files[relative]).get("digest")
        == _sha256_bytes(content.encode("utf-8"))
        for relative, content in EXPECTED_EFFECTS[case_id].values()
    )
    effect_counts_ok = all(
        audit_counts[task_id] == 1 for task_id in EXPECTED_EFFECTS[case_id]
    )
    duplicate_effects = sum(
        max(audit_counts[task_id] - 1, 0) for task_id in EXPECTED_EFFECTS[case_id]
    )
    completed_reexecution = sum(
        max(audit_counts[task_id] - 1, 0) for task_id in PRECOMPLETED_TASKS[case_id]
    )
    terminal_count = sum(
        event_type in {"run_completed", "run_failed", "run_blocked"}
        for event_type in event_types
    )
    completed_count = event_types.count("run_completed")
    terminal_consistent = (
        post_head.get("run_status") == "COMPLETED"
        and terminal_count == 1
        and completed_count == 1
        and bool(post_events)
        and event_types[-1] == "run_completed"
        and int(_mapping(post_events[-1]).get("run_revision", -1))
        == int(post_head.get("current_revision", -2))
    )
    index_complete = (
        not post_index.get("active_workflow_id")
        and not tuple(post_index.get("pending_workflow_ids", []) or [])
        and set(post_index.get("completed_workflow_ids", []) or [])
        == set(post_index.get("workflow_sequence", []) or [])
    )
    legacy_files = _workspace_files(legacy_root)
    no_legacy_leak = not any(
        relative in legacy_files for relative, _content in EXPECTED_EFFECTS[case_id].values()
    )
    marker_ok = _marker_valid(case_id, marker, pre)
    durable_state_preserved = (
        marker_ok and bool(_list(post.get("checkpoints"))) and bool(post_index)
    )
    if case_id == "R02":
        durable_state_preserved = marker_ok and any(
            str(item.get("effect_state", "")) == "COMMITTED"
            for item in _list(post.get("ledger"))
            if isinstance(item, Mapping)
        )
    gates = {
        "true_process_kill": worker_a_returncode == -signal.SIGKILL,
        "durable_state_loss_zero": durable_state_preserved,
        "duplicate_side_effect_zero": duplicate_effects == 0 and effect_counts_ok,
        "duplicate_provider_operation_zero": True,
        "completed_workflow_reexecution_zero": completed_reexecution == 0,
        "stale_writer_acceptance_zero": (
            bool(stale_probe.get("attempted"))
            and not bool(stale_probe.get("accepted"))
            and stale_probe.get("code") == StoreErrorCode.STALE_WRITER.value
        ),
        "terminal_snapshot_event_match": terminal_consistent,
        "cross_run_workspace_leakage_zero": no_legacy_leak,
        "event_replay_gap_zero": (
            sequences == list(range(1, len(sequences) + 1))
            and len(event_ids) == len(set(event_ids))
        ),
        "false_completed_zero": (
            post_head.get("run_status") != "COMPLETED"
            or (required_files_ok and index_complete and terminal_consistent and effect_counts_ok)
        ),
        "resume_worker_completed": worker_b_returncode == 0,
    }
    diagnostics = {
        "marker_valid": marker_ok,
        "required_files_ok": required_files_ok,
        "index_complete": index_complete,
        "effect_counts": dict(sorted(audit_counts.items())),
        "duplicate_side_effect_count": duplicate_effects,
        "completed_workflow_reexecution_count": completed_reexecution,
        "event_sequences": sequences,
        "event_ids": event_ids,
        "event_types": event_types,
        "legacy_workspace_files": legacy_files,
    }
    return gates, diagnostics


@dataclass(frozen=True)
class RestartCaseResult:
    case_id: str
    crash_point: str
    case_root: str
    worker_a: dict[str, Any]
    marker: dict[str, Any]
    pre_crash_durable_state: dict[str, Any]
    worker_b: dict[str, Any]
    post_resume_durable_state: dict[str, Any]
    audit_records: tuple[dict[str, Any], ...]
    stale_writer_probe: dict[str, Any]
    diagnostics: dict[str, Any]
    gates: dict[str, bool]
    runtime_correctness: str

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(self)


def run_case(case_id: str, *, base: Path, timeout: float = 20.0) -> RestartCaseResult:
    if case_id not in CRASH_POINTS:
        raise ValueError(f"unknown P2-R case: {case_id}")
    base.mkdir(parents=True, exist_ok=True)
    case_root = Path(tempfile.mkdtemp(prefix=f"{case_id.lower()}-", dir=base))
    database = case_root / "runtime.sqlite"
    workspace = case_root / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    marker_path = case_root / "crash-marker.json"
    audit_path = case_root / "effects.jsonl"
    start_result = case_root / "start-result.json"
    resume_result = case_root / "resume-result.json"

    process_a, out_a, err_a = _start_process(
        mode="start",
        case_id=case_id,
        case_root=case_root,
        database=database,
        workspace=workspace,
        marker=marker_path,
        audit=audit_path,
        result=start_result,
    )
    marker_status = _wait_for_marker(process_a, marker_path, timeout)
    if marker_status == "MARKER_OBSERVED" and process_a.poll() is None:
        process_a.kill()
    try:
        process_a.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process_a.kill()
        process_a.wait(timeout=5)
    out_a.close()
    err_a.close()
    marker = _read_json(marker_path)
    pre = _durable_snapshot(database, workspace, case_id)

    process_b, out_b, err_b = _start_process(
        mode="resume",
        case_id=case_id,
        case_root=case_root,
        database=database,
        workspace=workspace,
        marker=marker_path,
        audit=audit_path,
        result=resume_result,
    )
    worker_b_returncode = _run_resume_process(process_b, timeout)
    out_b.close()
    err_b.close()
    post = _durable_snapshot(database, workspace, case_id)
    records, counts = _audit(audit_path)
    stale_probe = _probe_stale_writer(database, case_id, pre, post)
    gates, diagnostics = _evaluate_case(
        case_id,
        marker=marker,
        pre=pre,
        post=post,
        audit_counts=counts,
        stale_probe=stale_probe,
        worker_a_returncode=process_a.returncode,
        worker_b_returncode=worker_b_returncode,
        legacy_root=case_root / "output",
    )
    worker_a = {
        "marker_status": marker_status,
        "returncode": process_a.returncode,
        "killed_by_parent": process_a.returncode == -signal.SIGKILL,
        "result": _read_json(start_result),
        "stdout": (case_root / "worker-start.stdout.log").read_text(
            encoding="utf-8", errors="replace"
        )[-4_000:],
        "stderr": (case_root / "worker-start.stderr.log").read_text(
            encoding="utf-8", errors="replace"
        )[-4_000:],
    }
    worker_b = {
        "returncode": worker_b_returncode,
        "result": _read_json(resume_result),
        "stdout": (case_root / "worker-resume.stdout.log").read_text(
            encoding="utf-8", errors="replace"
        )[-4_000:],
        "stderr": (case_root / "worker-resume.stderr.log").read_text(
            encoding="utf-8", errors="replace"
        )[-4_000:],
    }
    return RestartCaseResult(
        case_id=case_id,
        crash_point=CRASH_POINTS[case_id],
        case_root=str(case_root),
        worker_a=worker_a,
        marker=marker,
        pre_crash_durable_state=pre,
        worker_b=worker_b,
        post_resume_durable_state=post,
        audit_records=tuple(records),
        stale_writer_probe=stale_probe,
        diagnostics=diagnostics,
        gates=gates,
        runtime_correctness="PASS" if all(gates.values()) else "FAIL",
    )


def build_report(results: tuple[RestartCaseResult, ...]) -> dict[str, Any]:
    cases_by_id = {
        case.id: case for case in build_cases() if case.group is P2Group.RESTART
    }
    selected = tuple(cases_by_id[result.case_id] for result in results)
    return {
        "suite": "P2-R1 Process Crash / Restart",
        "source": "true-subprocess-sigkill",
        "commit": _commit(),
        "provider": "deterministic-workflow-no-network",
        "dataset": benchmark_metadata(selected),
        "dataset_hash": dataset_hash(selected),
        "automatic_rerun": False,
        "scope": (
            "real AgentService/SQLite/RunContext/Workspace/ResumeCoordinator/"
            "WorkflowExecutor with true child-process SIGKILL"
        ),
        "summary": {
            "total": len(results),
            "runtime_correctness_pass": sum(
                result.runtime_correctness == "PASS" for result in results
            ),
            "true_process_kill_pass": sum(
                result.gates.get("true_process_kill", False) for result in results
            ),
            "duplicate_side_effect_count": sum(
                int(result.diagnostics.get("duplicate_side_effect_count", 0))
                for result in results
            ),
            "completed_workflow_reexecution_count": sum(
                int(result.diagnostics.get("completed_workflow_reexecution_count", 0))
                for result in results
            ),
            "stale_writer_acceptance_count": sum(
                int(bool(result.stale_writer_probe.get("accepted")))
                for result in results
            ),
        },
        "results": [
            {
                **result.to_dict(),
                "oracle": evaluate(cases_by_id[result.case_id]).to_dict(),
            }
            for result in results
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="P2-R1 real process crash harness")
    parser.add_argument(
        "--case",
        default="all",
        choices=("all", *CRASH_POINTS),
    )
    parser.add_argument(
        "--work-root",
        type=Path,
        default=Path(os.environ.get("TSAGENT_P2_R_WORK", "/private/tmp/tsagent-p2-r1")),
    )
    parser.add_argument(
        "--results",
        type=Path,
        default=Path(
            os.environ.get(
                "TSAGENT_P2_R_RESULTS",
                "/private/tmp/tsagent-p2-r1-results.json",
            )
        ),
    )
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args(argv)
    case_ids = tuple(CRASH_POINTS) if args.case == "all" else (args.case,)
    results = tuple(
        run_case(case_id, base=args.work_root, timeout=args.timeout)
        for case_id in case_ids
    )
    report = build_report(results)
    args.results.parent.mkdir(parents=True, exist_ok=True)
    args.results.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    summary = report["summary"]
    print(
        "P2-R1 true process crash: "
        f"{summary['runtime_correctness_pass']}/{summary['total']} PASS; "
        f"results={args.results}"
    )
    for result in results:
        failed = [name for name, passed in result.gates.items() if not passed]
        print(
            f"  {result.case_id}: {result.runtime_correctness}"
            + ("" if not failed else f" ({', '.join(failed)})")
        )
    return 0 if summary["runtime_correctness_pass"] == summary["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["RestartCaseResult", "build_report", "main", "run_case"]
