from __future__ import annotations

import asyncio
import ast
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, cast

import pytest

from agent.service import AgentServiceError, EventType, RunStatus, ServiceErrorCode
from agent.service.contracts import (
    ArtifactSummary,
    RunEvent,
    RunHandle,
    RunSnapshot,
)
from agent.service.local_protocol import (
    LocalRpcFailure,
    LocalRpcRequest,
    LocalRpcSuccess,
    LocalTransportMethod,
    decode_response,
)
from agent.service.local_sidecar.dispatcher import SidecarDispatcher
from agent.service.run_projector import encode_request_reference
from agent.runtime_store import SqliteRuntimeStore


ROOT = Path(__file__).parents[1]


def _params(run_id: str = "run-1", *, request_id: str = "request-1") -> dict[str, Any]:
    return {
        "tenant_id": "tenant-1",
        "user_id": "user-1",
        "session_id": "session-1",
        "run_id": run_id,
        "request_id": request_id,
    }


class FakeService:
    def __init__(self) -> None:
        self.closed = False
        self.calls: list[str] = []

    async def close(self) -> None:
        self.closed = True

    async def start_run(self, request: Any) -> RunHandle:
        self.calls.append(f"start:{request.request_id}")
        return RunHandle(
            tenant_id=request.tenant_id,
            session_id=request.session_id,
            run_id="run-started",
            request_id=request.request_id,
            status=RunStatus.CREATED,
            created_at="2026-08-11T00:00:00Z",
        )

    async def get_run(self, request: Any) -> RunSnapshot:
        self.calls.append(f"get:{request.run_id}")
        return RunSnapshot(
            tenant_id=request.tenant_id,
            session_id=request.session_id,
            run_id=request.run_id,
            request_id=request.request_id,
            status=RunStatus.CANCELLING,
            request_text="test",
            active_workflow_id="workflow-a",
            created_at="2026-08-11T00:00:00Z",
            updated_at="2026-08-11T00:00:01Z",
        )

    async def resume_run(self, request: Any) -> RunHandle:
        self.calls.append(f"resume:{request.run_id}")
        return await self.start_run(request)

    async def cancel_run(self, request: Any) -> RunSnapshot:
        self.calls.append(f"cancel:{request.run_id}")
        return await self.get_run(request)

    async def list_artifacts(self, request: Any) -> tuple[ArtifactSummary, ...]:
        self.calls.append(f"artifacts:{request.run_id}")
        return (
            ArtifactSummary(
                artifact_id="artifact-1",
                artifact_type="text",
                digest="sha256:1",
                reference="workspace://result.txt",
                exists=True,
                verified=True,
                run_id=request.run_id,
                display_name="result.txt",
                size=2,
                created_at="2026-08-11T00:00:01Z",
            ),
        )

    def stream_events(self, request: Any):
        self.calls.append(f"events:{request.after_sequence}")

        async def events():
            yield RunEvent(
                event_id="event-2",
                sequence_number=2,
                tenant_id=request.tenant_id,
                session_id=request.session_id,
                run_id=request.run_id,
                workflow_id=None,
                stage_id=None,
                task_id=None,
                event_type=EventType.RUN_CANCELLING,
                timestamp="2026-08-11T00:00:01Z",
                payload={},
                run_revision=2,
            )

        return events()


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_dispatcher_uses_explicit_service_methods_and_dto_projection() -> None:
    async def scenario() -> None:
        service = FakeService()
        dispatcher = SidecarDispatcher(service, diagnostics=sys.stderr)
        try:
            start = await dispatcher.dispatch(
                LocalRpcRequest(
                    "transport-1",
                    LocalTransportMethod.START_RUN,
                    {
                        **_params(),
                        "request_text": "生成结果",
                    },
                )
            )
            assert isinstance(start, LocalRpcSuccess)
            assert start.to_dict()["result"]["run_id"] == "run-started"

            artifacts = await dispatcher.dispatch(
                LocalRpcRequest(
                    "transport-2",
                    LocalTransportMethod.LIST_ARTIFACTS,
                    _params(),
                )
            )
            assert isinstance(artifacts, LocalRpcSuccess)
            assert artifacts.to_dict()["result"][0]["verified"] is True

            events = await dispatcher.dispatch(
                LocalRpcRequest(
                    "transport-3",
                    LocalTransportMethod.READ_EVENTS,
                    {**_params(), "after_sequence": 1, "limit": 10},
                )
            )
            assert isinstance(events, LocalRpcSuccess)
            assert events.to_dict()["result"][0]["sequence_number"] == 2
            assert service.calls == ["start:request-1", "artifacts:run-1", "events:1"]
        finally:
            await dispatcher.close()
        assert service.closed is True

    _run(scenario())


def test_dispatcher_maps_service_errors_without_internal_details() -> None:
    class ErrorService(FakeService):
        async def get_run(self, request: Any) -> RunSnapshot:
            raise AgentServiceError(
                ServiceErrorCode.RUN_NOT_FOUND,
                "/private/secret.sqlite traceback must not leak",
                run_id=request.run_id,
                request_id=request.request_id,
                details={"path": "/private/secret.sqlite"},
            )

    async def scenario() -> None:
        dispatcher = SidecarDispatcher(ErrorService(), diagnostics=sys.stderr)
        response = await dispatcher.dispatch(
            LocalRpcRequest("transport-1", LocalTransportMethod.GET_RUN, _params())
        )
        assert isinstance(response, LocalRpcFailure)
        payload = response.to_dict()
        serialized = json.dumps(payload)
        assert payload["error"]["code"] == "RUN_NOT_FOUND"
        assert payload["error"]["message"] == "run was not found"
        assert "/private/secret.sqlite" not in serialized
        assert "traceback" not in serialized.lower()
        await dispatcher.close()

    _run(scenario())


def test_cancel_projection_remains_cancelling() -> None:
    async def scenario() -> None:
        service = FakeService()
        dispatcher = SidecarDispatcher(service, diagnostics=sys.stderr)
        response = await dispatcher.dispatch(
            LocalRpcRequest(
                "transport-cancel",
                LocalTransportMethod.CANCEL_RUN,
                {**_params(), "requested_by": "desktop"},
            )
        )
        assert isinstance(response, LocalRpcSuccess)
        assert response.to_dict()["result"]["status"] == "CANCELLING"
        await dispatcher.close()

    _run(scenario())


def test_sidecar_bootstraps_builtin_tools_for_scoped_runtime_compile(
    tmp_path: Path,
) -> None:
    """A real sidecar must expose compiler registrations before a Run starts.

    This is the deterministic regression for Desktop-H1 DT02: without the
    application bootstrap, a literal file-write request reaches the compiler
    with an empty ToolRegistry and is reported as a generic RUNTIME_EXCEPTION.
    The check stops before any Provider call or filesystem side effect.
    """

    from agent.compiler.context import CompilerContext
    from agent.compiler.rules import DEFAULT_RULES
    from agent.compiler.tool_selector import Compiler
    from agent.registry.tool_registry import registry
    from agent.service.local_sidecar.lifecycle import SidecarConfig, create_service
    from agent.task import Task

    service = create_service(
        SidecarConfig(
            database_path=tmp_path / "h1.sqlite",
            workspace_root=tmp_path / "workspace",
        )
    )
    try:
        assert registry.get("write_file") is not None
        compiler = Compiler()
        for rule in DEFAULT_RULES:
            compiler.add_rule(rule)
        task = Task.from_dict(
            {
                "id": "h1-write",
                "verb": "write",
                "target": "output/result.txt",
                "target_type": "file",
                "inputs": {"content": "desktop-h1"},
            }
        )
        plan = compiler.compile(
            task,
            context=CompilerContext(
                workspace=tmp_path / "workspace",
                registry=registry,
            ),
        )
        assert plan.executor == "tool"
        assert [step.tool for step in plan.steps] == [
            "workspace",
            "filesystem.write",
        ]
    finally:
        _run(cast(Any, service).close())


def test_sidecar_has_no_direct_runtime_or_store_imports() -> None:
    sidecar_root = ROOT / "agent" / "service" / "local_sidecar"
    forbidden_prefixes = (
        "agent.runtime",
        "agent.orchestrator",
        "agent.runtime_store",
        "agent.interruption.store",
        "agent.services",
    )
    for source_path in sidecar_root.glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        imported_modules: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported_modules.append(node.module)
        assert not any(
            module.startswith(forbidden)
            for module in imported_modules
            for forbidden in forbidden_prefixes
        ), source_path


def test_subprocess_health_shutdown_stdout_is_protocol_only(tmp_path: Path) -> None:
    process = _spawn_sidecar(tmp_path)
    try:
        _send(process, {"id": "health-1", "method": "health", "params": {}})
        health = _read_response(process)
        assert health["ok"] is True
        assert health["result"]["protocol_version"] == "desktop-local-jsonl-v1"

        _send(process, {"id": "shutdown-1", "method": "shutdown", "params": {}})
        shutdown = _read_response(process)
        assert shutdown["ok"] is True
        assert process.wait(timeout=5) == 0
        stderr = process.stderr.read() if process.stderr is not None else ""
        assert "Traceback" not in stderr
    finally:
        _terminate(process)


def test_subprocess_start_run_returns_before_background_completion(tmp_path: Path) -> None:
    process = _spawn_sidecar(tmp_path, extra_env={"TSAGENT_LLM_TIMEOUT": "0.05"})
    started_at = time.monotonic()
    try:
        _send(
            process,
            {
                "id": "start-1",
                "method": "start_run",
                "params": {
                    "tenant_id": "tenant-1",
                    "user_id": "user-1",
                    "session_id": "session-1",
                    "request_id": "start-1",
                    "request_text": "生成一个简短结果",
                },
            },
        )
        response = _read_response(process)
        elapsed = time.monotonic() - started_at
        assert response["ok"] is True, response
        assert response["result"]["run_id"]
        assert elapsed < 5
    finally:
        process.kill()
        process.wait(timeout=5)


def test_subprocess_restart_reads_durable_run_without_runtime_call(tmp_path: Path) -> None:
    database = tmp_path / "restart.sqlite"
    request = {
        "tenant_id": "tenant-1",
        "user_id": "user-1",
        "session_id": "session-1",
        "request_id": "seed-1",
        "request_text": "seeded durable run",
    }
    from agent.service.contracts import StartRunRequest

    start_request = StartRunRequest(**request)
    store = SqliteRuntimeStore.open(database)
    try:
        store.reserve_service_start(
            "tenant-1",
            "session-1",
            requested_run_id="run-seeded",
            request_id=start_request.request_id,
            request_digest=start_request.request_digest,
            writer_id="seed-writer",
            external_reference=encode_request_reference(start_request, run_id="run-seeded"),
        )
    finally:
        store.close()

    first = _spawn_sidecar(tmp_path, database_path=database)
    try:
        _send(first, {"id": "get-1", "method": "get_run", "params": {**_params("run-seeded", request_id="get-1")}})
        response = _read_response(first)
        assert response["ok"] is True, response
        assert response["result"]["run_id"] == "run-seeded"
        _send(first, {"id": "shutdown-1", "method": "shutdown", "params": {}})
        assert _read_response(first)["ok"] is True
        assert first.wait(timeout=5) == 0
    finally:
        _terminate(first)

    second = _spawn_sidecar(tmp_path, database_path=database)
    try:
        _send(second, {"id": "get-2", "method": "get_run", "params": {**_params("run-seeded", request_id="get-2")}})
        response = _read_response(second)
        assert response["ok"] is True, response
        assert response["result"]["run_id"] == "run-seeded"
        _send(second, {"id": "shutdown-2", "method": "shutdown", "params": {}})
        assert _read_response(second)["ok"] is True
        assert second.wait(timeout=5) == 0
    finally:
        _terminate(second)


def test_subprocess_invalid_request_is_jsonl_error(tmp_path: Path) -> None:
    process = _spawn_sidecar(tmp_path)
    try:
        _send_raw(process, b'{"id":"bad-1","method":"unknown","params":{}}\n')
        response = _read_response(process)
        assert response == {
            "id": "bad-1",
            "ok": False,
            "error": {
                "code": "UNSUPPORTED_OPERATION",
                "message": "unsupported local method: unknown",
                "retryable": False,
            },
        }
        _send(process, {"id": "shutdown-1", "method": "shutdown", "params": {}})
        _read_response(process)
        assert process.wait(timeout=5) == 0
    finally:
        _terminate(process)


def _spawn_sidecar(
    tmp_path: Path,
    *,
    database_path: Path | None = None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.Popen[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    if extra_env:
        env.update(extra_env)
    return subprocess.Popen(
        [
            sys.executable,
            "-m",
            "agent.service.local_sidecar",
            "--database",
            str(database_path or (tmp_path / "runtime.sqlite")),
            "--workspace-root",
            str(tmp_path / "workspace"),
        ],
        cwd=ROOT,
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )


def _send(process: subprocess.Popen[str], message: dict[str, Any]) -> None:
    assert process.stdin is not None
    process.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
    process.stdin.flush()


def _send_raw(process: subprocess.Popen[str], message: bytes) -> None:
    assert process.stdin is not None
    process.stdin.write(message.decode("utf-8"))
    process.stdin.flush()


def _read_response(process: subprocess.Popen[str]) -> dict[str, Any]:
    assert process.stdout is not None
    line = process.stdout.readline()
    assert line, "sidecar exited without a protocol response"
    decoded = decode_response(line)
    return decoded.to_dict()


def _terminate(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.kill()
        process.wait(timeout=5)
    if process.stdin is not None:
        process.stdin.close()
    if process.stdout is not None:
        process.stdout.close()
    if process.stderr is not None:
        process.stderr.close()


def _workspace_file_service(
    tmp_path: Path, *, workspace_root: Path | None = None
) -> Any:
    from agent.service.factory import create_default_agent_service

    return create_default_agent_service(
        tmp_path / "runtime.sqlite", workspace_root=workspace_root
    )


def test_workspace_file_apis_scope_to_bound_session_workspace(
    tmp_path: Path,
) -> None:
    """Mars file APIs must resolve the session→folder binding the same way
    create_run does — a bound session reads/writes its folder, an unbound
    session falls back to the factory root."""
    from agent.service.contracts import (
        AssignSessionWorkspaceRequest,
        CreateSessionRequest,
        ListWorkspaceFilesRequest,
        UpsertWorkspaceRequest,
        WorkspaceFileRequest,
    )

    factory_root = tmp_path / "factory-ws"
    bound_root = tmp_path / "bound-ws"
    factory_root.mkdir()
    bound_root.mkdir()
    (factory_root / "factory.txt").write_text("factory-content")
    (bound_root / "bound.txt").write_text("bound-content")

    service = _workspace_file_service(tmp_path, workspace_root=factory_root)
    ident = {"tenant_id": "tenant-1", "user_id": "user-1"}

    async def scenario() -> None:
        await service.create_session(
            CreateSessionRequest(
                **ident, session_id="s-bound", request_id="req-c1"
            )
        )
        await service.upsert_workspace(
            UpsertWorkspaceRequest(
                **ident,
                session_id="s-bound",
                request_id="req-w1",
                workspace_id="ws-dir",
                path=str(bound_root),
            )
        )
        await service.assign_session_workspace(
            AssignSessionWorkspaceRequest(
                **ident,
                session_id="s-bound",
                request_id="req-a1",
                workspace_id="ws-dir",
            )
        )

        listed = await service.list_workspace_files(
            ListWorkspaceFilesRequest(
                **ident, session_id="s-bound", request_id="req-l1"
            )
        )
        assert [entry.path for entry in listed] == ["bound.txt"]

        content = await service.read_workspace_file(
            WorkspaceFileRequest(
                **ident,
                session_id="s-bound",
                request_id="req-r1",
                path="bound.txt",
            )
        )
        assert content.text == "bound-content"

        await service.write_workspace_file(
            WorkspaceFileRequest(
                **ident,
                session_id="s-bound",
                request_id="req-wr1",
                path="created.txt",
                content="created-content",
            )
        )
        assert (bound_root / "created.txt").read_text() == "created-content"
        assert not (factory_root / "created.txt").exists()

        unbound = await service.list_workspace_files(
            ListWorkspaceFilesRequest(
                **ident, session_id="s-free", request_id="req-l2"
            )
        )
        assert [entry.path for entry in unbound] == ["factory.txt"]

    try:
        _run(scenario())
    finally:
        _run(service.close())


def test_workspace_file_apis_fail_loud_without_workspace_root(
    tmp_path: Path,
) -> None:
    """No binding and no factory root → INVALID_REQUEST, not a silent
    fallback to the process directory."""
    from agent.service.contracts import ListWorkspaceFilesRequest

    service = _workspace_file_service(tmp_path)
    try:
        with pytest.raises(AgentServiceError) as exc:
            _run(
                service.list_workspace_files(
                    ListWorkspaceFilesRequest(
                        tenant_id="tenant-1",
                        user_id="user-1",
                        session_id="s-free",
                        request_id="req-l1",
                    )
                )
            )
        assert exc.value.code == ServiceErrorCode.INVALID_REQUEST
    finally:
        _run(service.close())


def test_attach_files_copies_external_files_into_session_workspace(
    tmp_path: Path,
) -> None:
    """Finder-picked files land in the session workspace's .attachments/ dir
    and come back as file: refs; identical content reuses the same ref."""
    from agent.service.contracts import (
        AssignSessionWorkspaceRequest,
        AttachFilesRequest,
        CreateSessionRequest,
        ListWorkspaceFilesRequest,
        UpsertWorkspaceRequest,
    )

    bound_root = tmp_path / "bound-ws"
    outside = tmp_path / "outside"
    bound_root.mkdir()
    outside.mkdir()
    (bound_root / "bound.txt").write_text("bound-content")
    (outside / "report.txt").write_text("external-content")
    (outside / "report2.txt").write_text("different-content")

    service = _workspace_file_service(tmp_path, workspace_root=tmp_path / "factory-ws")
    ident = {"tenant_id": "tenant-1", "user_id": "user-1"}

    async def scenario() -> None:
        await service.create_session(
            CreateSessionRequest(
                **ident, session_id="s-bound", request_id="req-c1"
            )
        )
        await service.upsert_workspace(
            UpsertWorkspaceRequest(
                **ident,
                session_id="s-bound",
                request_id="req-w1",
                workspace_id="ws-dir",
                path=str(bound_root),
            )
        )
        await service.assign_session_workspace(
            AssignSessionWorkspaceRequest(
                **ident,
                session_id="s-bound",
                request_id="req-a1",
                workspace_id="ws-dir",
            )
        )

        result = await service.attach_files(
            AttachFilesRequest(
                **ident,
                session_id="s-bound",
                request_id="req-f1",
                paths=(str(outside / "report.txt"),),
            )
        )
        assert result == {"context_refs": ["file:.attachments/report.txt"]}
        assert (bound_root / ".attachments" / "report.txt").read_text() == "external-content"

        again = await service.attach_files(
            AttachFilesRequest(
                **ident,
                session_id="s-bound",
                request_id="req-f2",
                paths=(str(outside / "report.txt"),),
            )
        )
        assert again == result

        listed = await service.list_workspace_files(
            ListWorkspaceFilesRequest(
                **ident, session_id="s-bound", request_id="req-l1"
            )
        )
        assert all(not e.path.startswith(".attachments") for e in listed)

    try:
        _run(scenario())
    finally:
        _run(service.close())


def test_attach_files_rejects_missing_file_and_missing_workspace(
    tmp_path: Path,
) -> None:
    """Missing file path and missing session workspace both fail
    INVALID_REQUEST — no silent skip or process-root fallback."""
    from agent.service.contracts import AttachFilesRequest

    service = _workspace_file_service(tmp_path, workspace_root=tmp_path / "ws")
    ident = {"tenant_id": "tenant-1", "user_id": "user-1"}
    try:
        with pytest.raises(AgentServiceError) as exc:
            _run(
                service.attach_files(
                    AttachFilesRequest(
                        **ident,
                        session_id="s-free",
                        request_id="req-f1",
                        paths=("/nonexistent/x.txt",),
                    )
                )
            )
        assert exc.value.code == ServiceErrorCode.INVALID_REQUEST

        no_root = _workspace_file_service(tmp_path / "other")
        try:
            with pytest.raises(AgentServiceError) as exc2:
                _run(
                    no_root.attach_files(
                        AttachFilesRequest(
                            **ident,
                            session_id="s-free",
                            request_id="req-f2",
                            paths=("/nonexistent/x.txt",),
                        )
                    )
                )
            assert exc2.value.code == ServiceErrorCode.INVALID_REQUEST
        finally:
            _run(no_root.close())
    finally:
        _run(service.close())


def test_read_artifact_serves_workspace_file_and_flags_stale(
    tmp_path: Path,
) -> None:
    """read_artifact reads the committed artifact's file through the session
    workspace — bytes matching the digest are fresh; edits mark it stale."""
    import hashlib

    from agent.runtime_store import (
        ArtifactCommitFact,
        DurableRuntimeStoreView,
        SqliteRuntimeStore,
    )
    from agent.service.contracts import ReadArtifactRequest, StartRunRequest
    from agent.service.run_projector import encode_request_reference

    workspace_root = tmp_path / "workspace"
    (workspace_root / "output").mkdir(parents=True)
    target = workspace_root / "output" / "result.txt"
    target.write_text("artifact-body", encoding="utf-8")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()

    database = tmp_path / "runtime.sqlite"
    store = SqliteRuntimeStore.open(database)
    request = StartRunRequest(
        tenant_id="tenant-1",
        user_id="user-1",
        session_id="session-1",
        request_id="request-art",
        request_text="write output/result.txt",
        run_id="run-art",
    )
    view = None
    try:
        store.reserve_service_start(
            request.tenant_id,
            request.session_id,
            requested_run_id=request.run_id,
            request_id=request.request_id,
            request_digest=request.request_digest,
            writer_id="writer-art",
            external_reference=encode_request_reference(
                request, run_id=request.run_id or ""
            ),
        )
        view = DurableRuntimeStoreView(
            store,
            tenant_id=request.tenant_id,
            session_id=request.session_id,
            run_id=request.run_id or "",
            request_id=request.request_id,
            writer_id="writer-art",
        )
        view.transition_run_with_event(
            run_status="RUNNING",
            event_id="run-started:art",
            event_type="run_started",
            timestamp="2026-08-11T00:00:00Z",
            payload={"request_id": request.request_id},
            expected_status="CREATED",
        )
        artifact = ArtifactCommitFact(
            artifact_id="artifact-art-result",
            artifact_type="file",
            reference="output/result.txt",
            digest=digest,
            producer_workflow_id="runtime-execution",
            producer_stage_id="task:art-write",
            producer_task_id="art-write",
        )
        view.transition_run_with_event(
            run_status="COMPLETED",
            event_id="run-completed:art",
            event_type="run_completed",
            timestamp="2026-08-11T00:00:01Z",
            payload={"request_id": request.request_id},
            run_output={
                "text": "written",
                "artifact_ids": [artifact.artifact_id],
            },
            artifacts=(artifact,),
        )
    finally:
        if view is not None:
            view.close()
        store.close()

    service = _workspace_file_service(tmp_path, workspace_root=workspace_root)
    lookup = {
        "tenant_id": "tenant-1",
        "user_id": "user-1",
        "session_id": "session-1",
        "run_id": "run-art",
    }

    async def scenario() -> None:
        content = await service.read_artifact(
            ReadArtifactRequest(
                **lookup, request_id="req-ra1", artifact_id="artifact-art-result"
            )
        )
        assert content.text == "artifact-body"
        assert content.stale is False
        assert content.reference == "output/result.txt"

        target.write_text("edited-after-commit", encoding="utf-8")
        edited = await service.read_artifact(
            ReadArtifactRequest(
                **lookup, request_id="req-ra2", artifact_id="artifact-art-result"
            )
        )
        assert edited.text == "edited-after-commit"
        assert edited.stale is True

        with pytest.raises(AgentServiceError) as exc:
            await service.read_artifact(
                ReadArtifactRequest(
                    **lookup, request_id="req-ra3", artifact_id="artifact-missing"
                )
            )
        assert exc.value.code == ServiceErrorCode.INVALID_REQUEST

    try:
        _run(scenario())
    finally:
        _run(service.close())


def test_start_run_validates_and_persists_context_refs(tmp_path: Path) -> None:
    """file:/task:/run: refs on start_run are validated against the resolved
    workspace root and persisted on the request record together with it."""
    from agent.runtime_store import SqliteRuntimeStore
    from agent.service.context_factory import ServiceContextFactory
    from agent.service.contracts import StartRunRequest
    from agent.service.run_projector import (
        request_context_refs,
        request_workspace_root,
    )
    from agent.service.runtime_launcher import RuntimeExecutionLauncher
    from agent.service.service import AgentService

    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "note.md").write_text("hello", encoding="utf-8")
    captured: list[Any] = []

    class CapturingRuntime:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            captured.append(kwargs["run_context"])

        async def run(self, _text: str) -> str:
            return "ok"

        def close(self) -> None:
            return None

    store = SqliteRuntimeStore.open(tmp_path / "refs.sqlite")
    service = AgentService(
        runtime_store=store,
        launcher=RuntimeExecutionLauncher(runtime_factory=CapturingRuntime),
        context_factory=ServiceContextFactory(store, workspace_root=workspace),
    )
    request = StartRunRequest(
        tenant_id="tenant-1", user_id="user-1", session_id="session-1",
        request_id="req-refs", request_text="read the note",
        context_refs=("file:note.md",),
    )

    async def scenario() -> None:
        handle = await service.start_run(request)
        await asyncio.sleep(0.05)
        read = store.read_run_snapshot(
            "tenant-1", handle.run_id, session_id="session-1"
        )
        assert request_workspace_root(read) == str(workspace.resolve())
        assert request_context_refs(read) == ("file:note.md",)
        assert captured and captured[0].context_refs == ("file:note.md",)

        with pytest.raises(AgentServiceError) as missing:
            await service.start_run(StartRunRequest(
                tenant_id="tenant-1", user_id="user-1",
                session_id="session-2", request_id="req-miss",
                request_text="bad", context_refs=("file:nope.txt",),
            ))
        assert missing.value.code == ServiceErrorCode.INVALID_REQUEST

        with pytest.raises(AgentServiceError) as artifact:
            await service.start_run(StartRunRequest(
                tenant_id="tenant-1", user_id="user-1",
                session_id="session-3", request_id="req-art",
                request_text="bad", context_refs=("artifact:artifact-x",),
            ))
        assert artifact.value.code == ServiceErrorCode.INVALID_REQUEST

    try:
        _run(scenario())
    finally:
        _run(service.close())


def test_read_artifact_uses_recorded_explicit_workspace_root(
    tmp_path: Path,
) -> None:
    """A run started with an explicit root resolves artifacts there even
    when the session binding points elsewhere."""
    import hashlib

    from agent.runtime_store import (
        ArtifactCommitFact,
        DurableRuntimeStoreView,
        SqliteRuntimeStore,
    )
    from agent.service.contracts import ReadArtifactRequest, StartRunRequest
    from agent.service.run_projector import encode_request_reference

    explicit = tmp_path / "explicit"
    bound = tmp_path / "bound"
    for root in (explicit, bound):
        (root / "output").mkdir(parents=True)
    (explicit / "output" / "a.txt").write_text("explicit-body", encoding="utf-8")
    (bound / "output" / "a.txt").write_text("bound-body", encoding="utf-8")
    digest = hashlib.sha256(
        (explicit / "output" / "a.txt").read_bytes()
    ).hexdigest()

    database = tmp_path / "runtime.sqlite"
    store = SqliteRuntimeStore.open(database)
    store.ensure_session("tenant-1", "session-1", "user-1")
    store.upsert_workspace("tenant-1", "ws-bound", path=str(bound))
    store.assign_session_workspace("tenant-1", "session-1", "ws-bound")
    request = StartRunRequest(
        tenant_id="tenant-1", user_id="user-1", session_id="session-1",
        request_id="req-exp", request_text="write", run_id="run-exp",
    )
    view = None
    try:
        store.reserve_service_start(
            "tenant-1", "session-1",
            requested_run_id="run-exp", request_id="req-exp",
            request_digest=request.request_digest,
            writer_id="writer-exp",
            external_reference=encode_request_reference(
                request, run_id="run-exp",
                workspace_root=str(explicit.resolve()),
                context_refs=(),
            ),
        )
        view = DurableRuntimeStoreView(
            store, tenant_id="tenant-1", session_id="session-1",
            run_id="run-exp", request_id="req-exp", writer_id="writer-exp",
        )
        view.transition_run_with_event(
            run_status="RUNNING", event_id="run-started:exp",
            event_type="run_started", timestamp="2026-08-11T00:00:00Z",
            payload={"request_id": "req-exp"}, expected_status="CREATED",
        )
        view.transition_run_with_event(
            run_status="COMPLETED", event_id="run-completed:exp",
            event_type="run_completed", timestamp="2026-08-11T00:00:01Z",
            payload={"request_id": "req-exp"},
            run_output={"text": "done", "artifact_ids": ["artifact-exp"]},
            artifacts=(ArtifactCommitFact(
                artifact_id="artifact-exp", artifact_type="file",
                reference="output/a.txt", digest=digest,
                producer_workflow_id="runtime-execution",
                producer_stage_id="task:w",
            ),),
        )
    finally:
        if view is not None:
            view.close()
        store.close()

    service = _workspace_file_service(tmp_path, workspace_root=bound)
    try:
        content = _run(service.read_artifact(ReadArtifactRequest(
            tenant_id="tenant-1", user_id="user-1", session_id="session-1",
            request_id="req-ra", run_id="run-exp", artifact_id="artifact-exp",
        )))
        assert content.text == "explicit-body"
        assert content.stale is False
    finally:
        _run(service.close())


@pytest.mark.host_integration
def test_http_sidecar_enforces_host_and_origin(tmp_path: Path) -> None:
    """Loopback RPC has no auth — foreign Origins and non-loopback Host
    headers (DNS rebinding) are rejected; curl-style no-Origin stays fine."""
    import http.client
    import socket
    import threading

    from agent.service.local_sidecar.http_server import run_http_sidecar

    service = _workspace_file_service(tmp_path)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    allowed = frozenset({"http://localhost:4173"})
    server_thread = threading.Thread(
        target=run_http_sidecar,
        kwargs={
            "service": service,
            "port": port,
            "diagnostics": open(os.devnull, "w"),
            "allowed_origins": allowed,
        },
        daemon=True,
    )
    server_thread.start()
    for _ in range(50):
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.05)
    else:  # pragma: no cover
        raise AssertionError("sidecar did not start listening")

    host = f"127.0.0.1:{port}"

    def request(method: str, path: str, *, headers: dict[str, str] | None = None) -> http.client.HTTPResponse:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request(method, path, body=b"{}", headers=headers or {})
        response = conn.getresponse()
        response.read()
        acao = response.getheader("Access-Control-Allow-Origin")
        conn.close()
        return response.status, acao

    try:
        # allowed origin → 200 + echoed ACAO
        status, acao = request("POST", "/rpc", headers={"Host": host, "Origin": "http://localhost:4173"})
        assert status in (200, 400)  # 400 acceptable: {} is not a valid envelope, but it reached dispatch
        assert acao == "http://localhost:4173"

        # foreign origin → 403 on POST /rpc; GET /events handler is gone → 501
        status, _ = request("POST", "/rpc", headers={"Host": host, "Origin": "http://evil.example"})
        assert status == 403
        status, _ = request("GET", "/events?tenant_id=t&session_id=s&run_id=r&user_id=u", headers={"Host": host, "Origin": "http://evil.example"})
        assert status == 501

        # DNS-rebinding Host → 403
        status, _ = request("POST", "/rpc", headers={"Host": "rebind.example.com"})
        assert status == 403

        # no Origin (curl/e2e) → allowed
        status, acao = request("POST", "/rpc", headers={"Host": host})
        assert status in (200, 400)
        assert acao is None
    finally:
        _run(service.close())


def test_workspace_file_document_contract(tmp_path: Path) -> None:
    """A22: 脱敏文件只读且原字节保留；基线过期返回 STALE_DOCUMENT。"""
    import asyncio

    import tests.test_ha_r1_harness as harness
    from agent.service.contracts import WorkspaceFileRequest
    from agent.service.errors import AgentServiceError

    async def scenario() -> None:
        service, _store = harness.service_at(tmp_path)
        try:
            ident = {"tenant_id": "t", "user_id": "u", "session_id": "s"}
            target = tmp_path / "notes.txt"
            target.write_text("line1\n")
            read = await service.read_workspace_file(WorkspaceFileRequest(
                **ident, request_id="r1", path="notes.txt"))
            assert read.digest and read.redacted is False

            target.write_text("changed on disk\n")
            with pytest.raises(AgentServiceError) as stale:
                await service.write_workspace_file(WorkspaceFileRequest(
                    **ident, request_id="r2", path="notes.txt",
                    content="overwrite", base_digest=read.digest))
            assert stale.value.code.value == "STALE_DOCUMENT"
            assert target.read_text() == "changed on disk\n"

            read2 = await service.read_workspace_file(WorkspaceFileRequest(
                **ident, request_id="r3", path="notes.txt"))
            saved = await service.write_workspace_file(WorkspaceFileRequest(
                **ident, request_id="r4", path="notes.txt",
                content="merged\n", base_digest=read2.digest))
            assert saved.digest
            # 连续保存：回执 digest 推进基线，第二次保存不得误报冲突。
            saved2 = await service.write_workspace_file(WorkspaceFileRequest(
                **ident, request_id="r4b", path="notes.txt",
                content="third\n", base_digest=saved.digest))
            assert saved2.digest != saved.digest
            assert target.read_text() == "third\n"

            # 带基线保存已删除的文件：必须判为过期，不得用旧草稿重建。
            doomed = tmp_path / "doomed.txt"
            doomed.write_text("v1\n")
            read4 = await service.read_workspace_file(WorkspaceFileRequest(
                **ident, request_id="r5a", path="doomed.txt"))
            doomed.unlink()
            with pytest.raises(AgentServiceError) as gone:
                await service.write_workspace_file(WorkspaceFileRequest(
                    **ident, request_id="r5b", path="doomed.txt",
                    content="resurrect", base_digest=read4.digest))
            assert gone.value.code.value == "STALE_DOCUMENT"
            assert not doomed.exists()

            secret = tmp_path / "config.txt"
            secret.write_text('api_key = "sk-SECRET000111222333"\n')
            read3 = await service.read_workspace_file(WorkspaceFileRequest(
                **ident, request_id="r5", path="config.txt"))
            assert read3.redacted is True
            assert "sk-SECRET000111222333" not in read3.text
            with pytest.raises(AgentServiceError) as ro:
                await service.write_workspace_file(WorkspaceFileRequest(
                    **ident, request_id="r6", path="config.txt",
                    content='api_key = "x"\n', base_digest=read3.digest))
            assert ro.value.code.value == "READ_ONLY_DOCUMENT"
            assert "sk-SECRET000111222333" in secret.read_text()

            # 同会话改绑工作区：读取绑定 A，改绑 B 后旧草稿不得写入新目录。
            ws_a = tmp_path / "ws-a"
            ws_b = tmp_path / "ws-b"
            for ws in (ws_a, ws_b):
                ws.mkdir()
                (ws / "shared.txt").write_text("identical\n")
            _store.ensure_session("t", "s-ws")
            _store.upsert_workspace("t", "ws-a", path=str(ws_a))
            _store.upsert_workspace("t", "ws-b", path=str(ws_b))
            _store.assign_session_workspace("t", "s-ws", "ws-a")
            ws_ident = {"tenant_id": "t", "user_id": "u", "session_id": "s-ws"}
            read5 = await service.read_workspace_file(WorkspaceFileRequest(
                **ws_ident, request_id="r7", path="shared.txt"))
            assert read5.workspace_root == str(ws_a.resolve())
            _store.assign_session_workspace("t", "s-ws", "ws-b")
            with pytest.raises(AgentServiceError) as changed:
                await service.write_workspace_file(WorkspaceFileRequest(
                    **ws_ident, request_id="r8", path="shared.txt",
                    content="draft", base_digest=read5.digest,
                    workspace_root=read5.workspace_root))
            assert changed.value.code.value == "WORKSPACE_CHANGED"
            assert (ws_a / "shared.txt").read_text() == "identical\n"
            assert (ws_b / "shared.txt").read_text() == "identical\n"
        finally:
            await service.close()

    asyncio.run(scenario())


def test_start_failure_is_durable_across_reopen(tmp_path: Path) -> None:
    """A04: 已接纳 Run 的 Context 构造失败必须落持久终态，
    重开 Service 后仍能通过 get_run 读到失败原因。"""
    import asyncio

    import tests.test_ha_r1_harness as harness
    from agent.runtime_store.sqlite import SqliteRuntimeStore
    from agent.service.context_factory import ServiceContextFactory
    from agent.service.contracts import RunLookupRequest, StartRunRequest
    from agent.service.runtime_launcher import RuntimeExecutionLauncher
    from agent.service.service import AgentService

    async def scenario() -> None:
        store = SqliteRuntimeStore.open(tmp_path / "runtime.sqlite")
        service = AgentService(
            runtime_store=store,
            launcher=RuntimeExecutionLauncher(),
            context_factory=ServiceContextFactory(
                store, workspace_root=tmp_path
            ),
            defer_context_creation=True,
        )
        try:
            def broken_create(*_a, **_k):
                raise RuntimeError("context boom")

            service._contexts.create_run = broken_create
            handle = await service.start_run(StartRunRequest(
                tenant_id="t", user_id="u", session_id="s",
                request_id="r1", request_text="会失败的启动"))
            head = None
            for _ in range(200):
                head = store.get_run_head(
                    "t", handle.run_id, session_id="s"
                )
                if head is not None and head.run_status == "FAILED_TERMINAL":
                    break
                await asyncio.sleep(0.05)
            assert head is not None
            assert head.run_status == "FAILED_TERMINAL"
        finally:
            await service.close()

        service2, _ = harness.service_at(tmp_path)
        try:
            snapshot = await service2.get_run(RunLookupRequest(
                tenant_id="t", user_id="u", session_id="s",
                run_id=handle.run_id, request_id="l1"))
            assert snapshot.status.value == "FAILED_TERMINAL"
            assert snapshot.failure_summary is not None
            assert "boom" in snapshot.failure_summary.details["failure_detail"]
        finally:
            await service2.close()

    asyncio.run(scenario())


def test_close_releases_paused_run(monkeypatch, tmp_path: Path) -> None:
    """A04: 暂停中的 Run 允许正常退出；退出不落隐式用户取消。"""
    import asyncio

    import tests.test_ha_r1_harness as harness
    from agent.service.contracts import (
        PauseRunRequest,
        RunLookupRequest,
        StartRunRequest,
    )

    request = "写一个文件"
    harness.setup_provider(monkeypatch, [
        (request, harness.intent(
            request, artifact=True, outcomes=["FILE_MUTATION"])),
        (request, harness.call_tool(
            "write_file", path="output/p.txt", content="x")),
        ("已写入", "完成"),
    ])

    async def scenario() -> None:
        from agent.runtime_store.sqlite import SqliteRuntimeStore
        from agent.service.context_factory import ServiceContextFactory
        from agent.service.runtime_launcher import RuntimeExecutionLauncher
        from agent.service.service import AgentService

        class SpyLauncher:
            """捕获真正执行 Run 的 worker-lease 子任务。"""

            def __init__(self) -> None:
                self._inner = RuntimeExecutionLauncher()
                self.launch_task: asyncio.Task | None = None

            async def start(self, **kwargs):
                self.launch_task = asyncio.current_task()
                return await self._inner.start(**kwargs)

            async def resume(self, **kwargs):
                return await self._inner.resume(**kwargs)

        store = SqliteRuntimeStore.open(tmp_path / "runtime.sqlite")
        spy = SpyLauncher()
        service = AgentService(
            runtime_store=store,
            launcher=spy,
            context_factory=ServiceContextFactory(
                store, workspace_root=tmp_path
            ),
        )
        handle = await service.start_run(StartRunRequest(
            tenant_id="t", user_id="u", session_id="s",
            request_id="r1", request_text=request))
        for _ in range(200):
            head = store.get_run_head("t", handle.run_id, session_id="s")
            if head is not None and head.run_status == "RUNNING":
                break
            await asyncio.sleep(0.05)
        await service.pause_run(PauseRunRequest(
            tenant_id="t", user_id="u", session_id="s",
            run_id=handle.run_id, request_id="p1"))
        await asyncio.wait_for(service.close(), timeout=10)
        # 取消必须传到 worker-lease 子任务 — 不得留在 pause gate 上挂起。
        assert spy.launch_task is not None
        assert spy.launch_task.done()

        service2, _ = harness.service_at(tmp_path)
        try:
            snapshot = await service2.get_run(RunLookupRequest(
                tenant_id="t", user_id="u", session_id="s",
                run_id=handle.run_id, request_id="l1"))
            assert snapshot.status.value == "SUSPENDED"
        finally:
            await service2.close()

    asyncio.run(scenario())


def test_scheduler_autostarts_and_routes_target_planet(monkeypatch, tmp_path: Path) -> None:
    """A05: 重建 Service 后 scheduler 自启并消费到期计划；
    触发的 Run 携带 schedule 的 target_planet 路由。"""
    import asyncio

    import tests.test_ha_r1_harness as harness

    request_text = "定时任务触发"
    harness.setup_provider(monkeypatch, [
        (request_text, harness.intent(request_text, artifact=False)),
        (request_text, "定时完成"),
    ])

    async def scenario() -> None:
        service, store = harness.service_at(tmp_path)
        try:
            store.create_schedule(
                "t", "sched-1", "s", request_text, 3600,
                user_id="u", target_planet="mars",
                next_fire_at="2020-01-01T00:00:00Z",
            )
            # 不调用任何 schedule API — 自启的 tick 必须自己认领并触发。
            record = None
            for _ in range(200):
                records = store.list_schedules("t")
                if records and records[0].fire_count and records[0].last_run_id:
                    record = records[0]
                    break
                await asyncio.sleep(0.05)
            assert record is not None and record.last_run_id, records
            heads = store.list_task_heads("t", "s:sched:sched-1")
            assert heads
            assert heads[0].origin_planet == "mars"
        finally:
            await service.close()

    asyncio.run(scenario())


def test_sidecar_subprocess_autostarts_scheduler(tmp_path: Path) -> None:
    """A05: 在事件循环外构造 Service 的真实 sidecar，空闲时也必须触发
    已到期计划 — health 不需要经过 _ensure_open。"""
    import json
    import os
    import subprocess
    import sys
    import time

    from agent.runtime_store.sqlite import SqliteRuntimeStore

    database = tmp_path / "sidecar-idle.sqlite"
    store = SqliteRuntimeStore.open(database)
    store.create_schedule(
        "t", "sched-idle", "s", "idle fire", 3600,
        user_id="u", next_fire_at="2020-01-01T00:00:00Z",
    )
    store.close()

    env = dict(os.environ)
    env.update({
        "OPENAI_API_KEY": "",
        "LLM_API_KEY": "",
        "P2_SECONDARY_API_KEY": "",
        "TSAGENT_LLM_PROVIDER": "ollama",
        "OLLAMA_BASE_URL": "http://127.0.0.1:1/v1",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
    })
    process = subprocess.Popen(
        [
            sys.executable, "-m", "agent.service.local_sidecar",
            "--database", str(database),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        cwd=Path(__file__).resolve().parents[1],
    )
    try:
        assert process.stdin is not None and process.stdout is not None
        process.stdin.write(json.dumps(
            {"id": "h1", "method": "health", "params": {}}
        ) + "\n")
        process.stdin.flush()
        process.stdout.readline()
        record = None
        for _ in range(100):
            store = SqliteRuntimeStore.open(database)
            records = store.list_schedules("t")
            store.close()
            if records and records[0].fire_count:
                record = records[0]
                break
            time.sleep(0.1)
        assert record is not None and record.fire_count >= 1
        assert record.last_run_id
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_scheduler_routes_profile_to_execution(monkeypatch, tmp_path: Path) -> None:
    """A05: target_planet 的执行路由 — 匹配 planet_affinity 的 profile
    经 metadata.agent_profile 进入 RunContext。"""
    import asyncio

    import tests.test_ha_r1_harness as harness
    from agent.runtime_store.sqlite import SqliteRuntimeStore
    from agent.service.context_factory import ServiceContextFactory
    from agent.service.runtime_launcher import RuntimeExecutionLauncher
    from agent.service.service import AgentService

    harness.setup_provider(monkeypatch, [
        ("profile route", harness.intent("profile route", artifact=False)),
        ("profile route", "done"),
    ])

    class SpyLauncher:
        def __init__(self) -> None:
            self._inner = RuntimeExecutionLauncher()
            self.seen_profile: dict = {}

        async def start(self, **kwargs):
            context = kwargs["run_context"]
            self.seen_profile = dict(
                getattr(context, "agent_profile", {}) or {}
            )
            return await self._inner.start(**kwargs)

        async def resume(self, **kwargs):
            return await self._inner.resume(**kwargs)

    async def scenario() -> None:
        store = SqliteRuntimeStore.open(tmp_path / "runtime.sqlite")
        spy = SpyLauncher()
        service = AgentService(
            runtime_store=store,
            launcher=spy,
            context_factory=ServiceContextFactory(
                store, workspace_root=tmp_path
            ),
        )
        try:
            store.upsert_profile(
                "t", "prof-mars",
                planet_affinity="mars",
                model_ref="fake-mars-model",
                skill_refs_json='["cap"]',
                permission_policy_json='{"tier":"low"}',
            )
            store.create_schedule(
                "t", "sched-m", "s", "profile route", 3600,
                user_id="u", target_planet="mars",
                next_fire_at="2020-01-01T00:00:00Z",
            )
            for _ in range(200):
                if spy.seen_profile:
                    break
                await asyncio.sleep(0.05)
            assert spy.seen_profile["profile_id"] == "prof-mars"
            assert spy.seen_profile["model_ref"] == "fake-mars-model"
        finally:
            await service.close()

    asyncio.run(scenario())
