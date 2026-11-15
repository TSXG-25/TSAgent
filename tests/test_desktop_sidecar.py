from __future__ import annotations

import asyncio
import ast
import http.server
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, cast

import pytest

from agent.service import AgentServiceError, EventType, RunStatus, ServiceErrorCode
from agent.service.contracts import (
    ArtifactSummary,
    ListWorkflowGraphsRequest,
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
    process = _spawn_sidecar(tmp_path, extra_env={
        # Provider 必须确定性不可达 — 本机 Ollama 活着时真实推理会拖慢
        # Run 终态并把 shutdown 拖过 wait 超时；死端口 + 零冷却立即失败。
        "OLLAMA_BASE_URL": "http://127.0.0.1:1/v1",
        "TSAGENT_OLLAMA_TIMEOUT": "0.05",
        "TSAGENT_LLM_PROVIDER": "ollama",
        "TSAGENT_LLM_PROVIDER_RETRIES": "1",
        "TSAGENT_LLM_PROVIDER_COOLDOWN": "0",
        "TSAGENT_LLM_PROVIDER_RETRY_AFTER": "0",
        "OPENAI_API_KEY": "",
        "LLM_API_KEY": "",
        "P2_SECONDARY_API_KEY": "",
    })
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
                request,
                run_id=request.run_id or "",
                workspace_root=str(workspace_root.resolve()),
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
    # sidecar 为 Service 创建长期 loop — 关闭必须在同一 loop 上执行。
    sidecar_loops: list = []
    server_thread = threading.Thread(
        target=run_http_sidecar,
        kwargs={
            "service": service,
            "port": port,
            "diagnostics": open(os.devnull, "w"),
            "allowed_origins": allowed,
            "loop_ready": sidecar_loops.append,
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
        assert sidecar_loops, "sidecar never published its loop"
        asyncio.run_coroutine_threadsafe(
            service.close(), sidecar_loops[0]
        ).result(timeout=10)
        # 所属 loop 上的关闭收束全部自有任务。
        if service._scheduler_task is not None:
            assert service._scheduler_task.done()
        assert not service._runs


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
    provider = harness.setup_provider(monkeypatch, [
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
        # 把 Run 停在工具调用回复交付前（replies 剩 2 条）— pause 后释放，
        # 让任务继续推进并在工具边界上真正进入 _pause_gate，而不是取消一个
        # 仍堵在 Provider 里的任务。
        park = asyncio.Event()
        orig_achat, orig_ainvoke = provider.achat, provider.ainvoke

        async def gated_achat(messages: Any, tools: Any = None, **kwargs: Any) -> Any:
            if len(provider.replies) == 2:
                await park.wait()
            return await orig_achat(messages, tools, **kwargs)

        async def gated_ainvoke(messages: Any, **kwargs: Any) -> Any:
            if len(provider.replies) == 2:
                await park.wait()
            return await orig_ainvoke(messages, **kwargs)

        provider.achat, provider.ainvoke = gated_achat, gated_ainvoke

        handle = await service.start_run(StartRunRequest(
            tenant_id="t", user_id="u", session_id="s",
            request_id="r1", request_text=request))
        for _ in range(200):
            head = store.get_run_head("t", handle.run_id, session_id="s")
            if (
                head is not None
                and head.run_status == "RUNNING"
                and len(provider.replies) == 2
            ):
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("run did not reach the parked tool-call reply")
        await service.pause_run(PauseRunRequest(
            tenant_id="t", user_id="u", session_id="s",
            run_id=handle.run_id, request_id="p1"))
        park.set()
        managed = service._runs.get(handle.run_id)
        assert managed is not None
        gate = getattr(managed.run_context, "_pause_gate", None)
        assert gate is not None
        for _ in range(200):
            if gate._waiters:
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("run did not park on the pause gate")
        # 工具边界在 write_file 之前 — gate 停住后工件不得已写出。
        assert not (tmp_path / "output" / "p.txt").exists()
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


def test_close_on_foreign_loop_fails_instead_of_leaking_run(
    monkeypatch, tmp_path: Path
) -> None:
    """close() 必须在所属 loop 上执行 — 对仍活跃的外部 loop 任务
    直接报错（不产生状态变更），在所属 loop 上关闭则收束全部任务。"""
    import threading

    import tests.test_ha_r1_harness as harness
    from agent.runtime_store.sqlite import SqliteRuntimeStore
    from agent.service.context_factory import ServiceContextFactory
    from agent.service.contracts import StartRunRequest
    from agent.service.runtime_launcher import RuntimeExecutionLauncher
    from agent.service.service import AgentService

    request = "写一个文件"
    harness.setup_provider(monkeypatch, [
        (request, harness.intent(
            request, artifact=True, outcomes=["FILE_MUTATION"])),
        (request, harness.call_tool(
            "write_file", path="output/p.txt", content="x")),
        ("已写入", "完成"),
    ])

    store = SqliteRuntimeStore.open(tmp_path / "runtime.sqlite")
    service = AgentService(
        runtime_store=store,
        launcher=RuntimeExecutionLauncher(),
        context_factory=ServiceContextFactory(
            store, workspace_root=tmp_path
        ),
    )

    owner_loop = asyncio.new_event_loop()
    owner_thread = threading.Thread(
        target=owner_loop.run_forever, daemon=True
    )
    owner_thread.start()
    try:
        handle = asyncio.run_coroutine_threadsafe(
            service.start_run(StartRunRequest(
                tenant_id="t", user_id="u", session_id="s",
                request_id="r1", request_text=request)),
            owner_loop,
        ).result(timeout=10)
        managed = service._runs.get(handle.run_id)
        assert managed is not None
        # owner loop 仍持有活跃任务时在别的 loop 上关闭 → 明确报错。
        with pytest.raises(RuntimeError, match="different loop"):
            asyncio.run(service.close())
        assert not service.closed
        # 所属 loop 上执行关闭 → 全部自有任务收束。
        asyncio.run_coroutine_threadsafe(
            service.close(), owner_loop
        ).result(timeout=10)
        assert managed.task.done()
        if service._scheduler_task is not None:
            assert service._scheduler_task.done()
        assert not service._runs
    finally:
        owner_loop.call_soon_threadsafe(owner_loop.stop)
        owner_thread.join(timeout=5)


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


def test_accepted_run_executes_in_recorded_workspace(monkeypatch, tmp_path: Path) -> None:
    """A15: 接纳后改绑工作区不得影响已接纳 Run — 执行仍使用
    接纳时记录的 root，下一次接纳才使用新绑定。"""
    import asyncio
    import threading

    import tests.test_ha_r1_harness as harness
    from agent.runtime_store.sqlite import SqliteRuntimeStore
    from agent.service.context_factory import ServiceContextFactory
    from agent.service.contracts import StartRunRequest
    from agent.service.runtime_launcher import RuntimeExecutionLauncher
    from agent.service.service import AgentService

    request_text = "在 A 工作区执行"
    harness.setup_provider(monkeypatch, [
        (request_text, harness.intent(request_text, artifact=False)),
        (request_text, "完成"),
    ])

    async def scenario() -> None:
        ws_a = tmp_path / "ws-a"
        ws_b = tmp_path / "ws-b"
        for ws in (ws_a, ws_b):
            ws.mkdir()
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
            store.ensure_session("t", "s")
            store.upsert_workspace("t", "ws-a", path=str(ws_a))
            store.upsert_workspace("t", "ws-b", path=str(ws_b))
            store.assign_session_workspace("t", "s", "ws-a")

            entered = threading.Event()
            gate = threading.Event()
            seen: dict = {}
            real_create = service._contexts.create_run

            def delayed(request, *, run_id, **kwargs):
                entered.set()
                gate.wait()
                context = real_create(request, run_id=run_id, **kwargs)
                workspace = getattr(context, "workspace", None)
                seen["root"] = str(getattr(workspace, "root", ""))
                return context

            service._contexts.create_run = delayed
            handle = await service.start_run(StartRunRequest(
                tenant_id="t", user_id="u", session_id="s",
                request_id="r1", request_text=request_text))
            entered.wait(timeout=10)
            store.assign_session_workspace("t", "s", "ws-b")
            gate.set()
            for _ in range(200):
                if "root" in seen:
                    break
                await asyncio.sleep(0.05)
            assert seen["root"] == str(ws_a.resolve())
        finally:
            await service.close()

    asyncio.run(scenario())


def test_sidecar_subscription_does_not_block_control(tmp_path: Path) -> None:
    """A09: 挂起的 subscribe_events 不阻塞后续控制请求；
    shutdown 收掉订阅；stdout 逐行可解码。"""
    import asyncio
    import io
    import json
    import time

    import tests.test_ha_r1_harness as harness
    from agent.service.local_sidecar.dispatcher import SidecarDispatcher
    from agent.service.local_sidecar.server import serve

    async def scenario() -> None:
        service, store = harness.service_at(tmp_path)
        # 仅有 CREATED 预留、没有任何事件的 Run — 订阅会一直等到
        # first_timeout（8s）才返回，复现串行阻塞场景。
        from agent.service.contracts import StartRunRequest
        from agent.service.run_projector import encode_request_reference

        store.reserve_service_start(
            "t", "s",
            requested_run_id="run-sub",
            request_id="r1",
            request_digest="d1",
            writer_id="w1",
            external_reference=encode_request_reference(
                StartRunRequest(
                    tenant_id="t", user_id="u", session_id="s",
                    request_id="r1", request_text="x"),
                run_id="run-sub",
            ),
        )
        dispatcher = SidecarDispatcher(service, diagnostics=io.StringIO())
        stdin = io.StringIO(
            json.dumps({"id": "sub-1", "method": "subscribe_events",
                        "params": {"tenant_id": "t", "user_id": "u",
                                   "session_id": "s", "run_id": "run-sub",
                                   "request_id": "q1"}}) + "\n"
            + json.dumps({"id": "h1", "method": "health",
                          "params": {}}) + "\n"
            + json.dumps({"id": "x", "method": "shutdown",
                          "params": {}}) + "\n"
        )
        stdout = io.StringIO()
        started = time.monotonic()
        rc = await asyncio.wait_for(
            serve(dispatcher, stdin=stdin, stdout=stdout,
                  diagnostics=io.StringIO()),
            timeout=10,
        )
        elapsed = time.monotonic() - started
        assert rc == 0
        # 若订阅仍串行，serve 至少要等 first_timeout（8s）才会读到 shutdown。
        assert elapsed < 5
        responses = [
            json.loads(line) for line in stdout.getvalue().splitlines()
        ]
        ids = {r["id"] for r in responses}
        assert "h1" in ids and "x" in ids
        # 挂起的订阅被 shutdown 收掉，不落半截响应行。
        assert "sub-1" not in ids
        health = next(r for r in responses if r["id"] == "h1")
        assert health["result"]["status"] == "ok"

    asyncio.run(scenario())


def test_sidecar_exits_on_broken_transport(tmp_path: Path) -> None:
    """A09: 普通响应写入失败后主循环立即退出 — 不再继续分发
    后续请求；后台订阅和 dispatcher 都被清理。"""
    import asyncio
    import io
    import json
    import time

    import tests.test_ha_r1_harness as harness
    from agent.service.local_sidecar.dispatcher import SidecarDispatcher
    from agent.service.local_sidecar.server import serve

    class CountingDispatcher(SidecarDispatcher):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.methods: list[str] = []

        async def dispatch(self, request):
            self.methods.append(request.method.value)
            return await super().dispatch(request)

    class DeadStdout(io.StringIO):
        def write(self, _s):
            raise BrokenPipeError("transport gone")

    async def scenario() -> None:
        service, _store = harness.service_at(tmp_path)
        dispatcher = CountingDispatcher(
            service, diagnostics=io.StringIO()
        )
        stdin = io.StringIO(
            json.dumps({"id": "sub-1", "method": "subscribe_events",
                        "params": {"tenant_id": "t", "user_id": "u",
                                   "session_id": "s", "run_id": "r",
                                   "request_id": "q1"}}) + "\n"
            + json.dumps({"id": "h1", "method": "health",
                          "params": {}}) + "\n"
            + json.dumps({"id": "h2", "method": "health",
                          "params": {}}) + "\n"
            + json.dumps({"id": "x", "method": "shutdown",
                          "params": {}}) + "\n"
        )
        started = time.monotonic()
        rc = await asyncio.wait_for(
            serve(dispatcher, stdin=stdin, stdout=DeadStdout(),
                  diagnostics=io.StringIO()),
            timeout=10,
        )
        elapsed = time.monotonic() - started
        assert rc == 0
        # 首个 health 写入失败后循环退出：第二条 health 和 shutdown
        # 都不再被分发。
        assert dispatcher.methods.count("health") == 1
        assert "shutdown" not in dispatcher.methods
        # 挂起的订阅被取消（没有等满 8s 轮询窗口），dispatcher 已关闭。
        assert elapsed < 5
        assert dispatcher._closed

    asyncio.run(scenario())


def test_delegation_provisioning_is_shared_and_idempotent(
    monkeypatch, tmp_path: Path
) -> None:
    """A06: 委派入口共用同一持久化用例 — 同请求重试不新增 Handoff；
    图路径的子 Run 收到等价 agent_profile/workspace 配置。"""
    import asyncio

    import tests.test_ha_r1_harness as harness
    from agent.runtime_store.sqlite import SqliteRuntimeStore
    from agent.runtime_store.view import DurableRuntimeStoreView
    from agent.service.context_factory import ServiceContextFactory
    from agent.service.contracts import (
        DelegateRunRequest,
        RunWorkflowGraphRequest,
        StartRunRequest,
    )
    from agent.service.runtime_launcher import RuntimeExecutionLauncher
    from agent.service.run_projector import encode_request_reference
    from agent.service.service import AgentService

    request_text = "委托任务"
    harness.setup_provider(monkeypatch, [
        (request_text, harness.intent(request_text, artifact=False)),
        (request_text, "完成"),
    ])

    class SpyLauncher:
        def __init__(self) -> None:
            self._inner = RuntimeExecutionLauncher()
            self.profiles: list[dict] = []

        async def start(self, **kwargs):
            self.profiles.append(dict(
                getattr(kwargs["run_context"], "agent_profile", {}) or {}
            ))
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
            store.ensure_session("t", "s")
            store.upsert_workspace("t", "ws", path=str(tmp_path))
            store.assign_session_workspace("t", "s", "ws")
            store.upsert_profile(
                "t", "prof-a", planet_affinity="mars",
                model_ref="fake-model-a",
            )
            parent = StartRunRequest(
                tenant_id="t", user_id="u", session_id="s",
                request_id="r-parent", request_text="parent")
            store.reserve_service_start(
                "t", "s",
                requested_run_id="run-parent", request_id="r-parent",
                request_digest="d", writer_id="w",
                external_reference=encode_request_reference(
                    parent, run_id="run-parent",
                    workspace_root=str(tmp_path)),
            )
            view = DurableRuntimeStoreView(
                store, tenant_id="t", session_id="s",
                run_id="run-parent", request_id="r-parent", writer_id="w")
            view.transition_run_with_event(
                run_status="RUNNING",
                event_id="run-started:p", event_type="run_started",
                timestamp="2026-11-10T00:00:00Z", payload={})

            dr = DelegateRunRequest(
                tenant_id="t", user_id="u", session_id="s",
                request_id="del-1", parent_run_id="run-parent",
                request_text=request_text,
                target_planet="mars", profile_id="prof-a")
            first = await service.delegate_run(dr)
            second = await service.delegate_run(dr)
            assert first["task_id"] == second["task_id"]
            assert first["handoff_id"] == second["handoff_id"]
            assert len(store.list_handoffs("t", "s")) == 1
            assert first["agent_profile"]["model_ref"] == "fake-model-a"
            assert first["workspace_root"] == str(tmp_path)
            assert first["request_text"].startswith("[Agent:")

            # 图路径共用同一 provision — 同 request_id 重跑不新增 Handoff。
            store.upsert_workflow_graph("t", "g-1", definition_json=(
                '{"nodes":[{"id":"n1","type":"task",'
                '"request_text":"委托任务","profile_id":"prof-a",'
                '"target_planet":"mars"}],"edges":[]}'))
            steps = await service.run_graph(RunWorkflowGraphRequest(
                tenant_id="t", user_id="u", session_id="s",
                request_id="g-run-1", graph_id="g-1",
                parent_run_id="run-parent"))
            assert len(steps) == 1
            # 图执行在后台推进 — 等 n1 的委派落成。
            for _ in range(200):
                if len(store.list_handoffs("t", "s")) >= 2:
                    break
                await asyncio.sleep(0.05)
            assert len(store.list_handoffs("t", "s")) == 2
            steps2 = await service.run_graph(RunWorkflowGraphRequest(
                tenant_id="t", user_id="u", session_id="s",
                request_id="g-run-1", graph_id="g-1",
                parent_run_id="run-parent"))
            assert [s.status for s in steps2] == ["PENDING"]
            assert len(store.list_handoffs("t", "s")) == 2
            # 图启动的子 Run 收到等价 agent_profile（此前只传 depth）。
            for _ in range(100):
                if spy.profiles:
                    break
                await asyncio.sleep(0.05)
            assert spy.profiles[0]["model_ref"] == "fake-model-a"
        finally:
            await service.close()

    asyncio.run(scenario())


def _running_parent(service, store, tmp_path):
    """A RUNNING parent head + session/workspace for delegation tests."""
    from agent.runtime_store.view import DurableRuntimeStoreView
    from agent.service.contracts import StartRunRequest
    from agent.service.run_projector import encode_request_reference

    store.ensure_session("t", "s")
    store.upsert_workspace("t", "ws", path=str(tmp_path))
    store.assign_session_workspace("t", "s", "ws")
    parent = StartRunRequest(
        tenant_id="t", user_id="u", session_id="s",
        request_id="r-parent", request_text="parent")
    store.reserve_service_start(
        "t", "s",
        requested_run_id="run-parent", request_id="r-parent",
        request_digest="d", writer_id="w",
        external_reference=encode_request_reference(
            parent, run_id="run-parent", workspace_root=str(tmp_path)),
    )
    view = DurableRuntimeStoreView(
        store, tenant_id="t", session_id="s",
        run_id="run-parent", request_id="r-parent", writer_id="w")
    view.transition_run_with_event(
        run_status="RUNNING",
        event_id="run-started:p", event_type="run_started",
        timestamp="2026-11-10T00:00:00Z", payload={})


def test_run_graph_task_nodes_and_success_dependency(
    monkeypatch, tmp_path: Path
) -> None:
    """A07: task 节点 + {from,to} 边的图按成功依赖执行；
    未知节点类型在产生 Task/Handoff/Run 前拒绝；同请求重试不新增 Handoff。"""
    import asyncio
    import json as _json

    import tests.test_ha_r1_harness as harness
    from agent.service.contracts import RunWorkflowGraphRequest
    from agent.service.errors import AgentServiceError

    harness.setup_provider(monkeypatch, [
        ("step-one", harness.intent("step-one", artifact=False)),
        ("step-one", "完成一"),
        ("step-two", harness.intent("step-two", artifact=False)),
        ("step-two", "完成二"),
    ])

    async def scenario() -> None:
        service, store = harness.service_at(tmp_path)
        try:
            _running_parent(service, store, tmp_path)
            store.upsert_workflow_graph("t", "g-ok", definition_json=(
                _json.dumps({"nodes": [
                    {"id": "n1", "type": "task", "request_text": "step-one"},
                    {"id": "n2", "type": "task", "request_text": "step-two"},
                ], "edges": [{"from": "n1", "to": "n2"}]})))
            steps = await service.run_graph(RunWorkflowGraphRequest(
                tenant_id="t", user_id="u", session_id="s",
                request_id="g-1", graph_id="g-ok",
                parent_run_id="run-parent"))
            # run_graph 只返回接纳计划 — 执行在后台推进，不被串行等待。
            assert [s.status for s in steps] == ["PENDING", "PENDING"]

            async def _wait(check):
                for _ in range(200):
                    value = check()
                    if value:
                        return value
                    await asyncio.sleep(0.05)
                raise AssertionError("timeout waiting for graph chain")

            def _n1():
                handoffs = store.list_handoffs("t", "s")
                if not handoffs or not handoffs[0].target_run_id:
                    return None
                child = f"s:subtask:{handoffs[0].target_task_id}"
                head = store.get_run_head(
                    "t", handoffs[0].target_run_id, session_id=child)
                if head is not None and head.run_status == "COMPLETED":
                    return handoffs[0], child
            n1 = await _wait(_n1)

            def _n2():
                handoffs = store.list_handoffs("t", n1[1])
                if not handoffs or not handoffs[0].target_run_id:
                    return None
                child2 = f"{n1[1]}:subtask:{handoffs[0].target_task_id}"
                head = store.get_run_head(
                    "t", handoffs[0].target_run_id, session_id=child2)
                return head is not None and head.run_status == "COMPLETED"
            assert await _wait(_n2)
            # n1 的 Handoff 在父会话；n2 的 Handoff 在 n1 的派生会话。
            assert len(store.list_handoffs("t", "s")) == 1
            assert len(store.list_handoffs("t", n1[1])) == 1
            # 持久执行关联 — execution_id 由接纳响应给出，节点行携带
            # 真实 run_id/child_session_id 与终态，可重复查询。
            from agent.service.contracts import GraphExecutionLookupRequest
            execution_id = steps[0].execution_id
            assert execution_id == "gexec:g-ok:g-1"

            def _terminal():
                rows = store.list_graph_execution_nodes("t", execution_id)
                return rows if len(rows) == 2 and all(
                    r.status == "COMPLETED" for r in rows) else None
            nodes = await _wait(_terminal)
            assert nodes[0].node_id == "n1" and nodes[1].node_id == "n2"
            assert nodes[0].run_id and nodes[0].child_session_id.startswith("s:subtask:")
            assert nodes[1].child_session_id.startswith(nodes[0].child_session_id)
            summary = await service.get_graph_execution(
                GraphExecutionLookupRequest(
                    tenant_id="t", user_id="u", session_id="s",
                    request_id="req-gx", execution_id=execution_id))
            assert summary.execution_id == execution_id
            assert summary.graph_id == "g-ok"
            assert [st.status for st in summary.steps] == ["COMPLETED", "COMPLETED"]
            assert summary.steps[0].run_id == nodes[0].run_id
            assert summary.steps[1].session_id == nodes[1].child_session_id
            # list_graph_executions — 本会话能重新发现这次执行。
            execs = await service.list_graph_executions(
                ListWorkflowGraphsRequest(
                    tenant_id="t", user_id="u", session_id="s",
                    request_id="req-gxl"))
            assert any(e["execution_id"] == execution_id for e in execs)
            # 同 request_id 重跑 — 委派事实复用，不新增 Handoff。
            await service.run_graph(RunWorkflowGraphRequest(
                tenant_id="t", user_id="u", session_id="s",
                request_id="g-1", graph_id="g-ok",
                parent_run_id="run-parent"))
            assert len(store.list_handoffs("t", "s")) == 1
            assert len(store.list_handoffs("t", n1[1])) == 1

            # 未知节点类型 — 产生任何 Task/Handoff/Run 前拒绝。
            store.upsert_workflow_graph("t", "g-bad", definition_json=(
                _json.dumps({"nodes": [
                    {"id": "n1", "type": "read_file", "request_text": "x"},
                ], "edges": []})))
            try:
                await service.run_graph(RunWorkflowGraphRequest(
                    tenant_id="t", user_id="u", session_id="s",
                    request_id="g-2", graph_id="g-bad",
                    parent_run_id="run-parent"))
                raise AssertionError("expected INVALID_REQUEST")
            except AgentServiceError as error:
                assert error.code.value == "INVALID_REQUEST"
            assert len(store.list_handoffs("t", "s")) == 1
        finally:
            await service.close()

    asyncio.run(scenario())


def test_run_graph_skips_only_dependents_of_failed_node(
    monkeypatch, tmp_path: Path
) -> None:
    """A07: 上游失败只跳过它的依赖 — A→B 失败时 B 跳过，
    无依赖的 C 仍正常启动执行。"""
    import asyncio
    import hashlib
    import json as _json

    import tests.test_ha_r1_harness as harness
    from agent.runtime_store.sqlite import SqliteRuntimeStore
    from agent.service.context_factory import ServiceContextFactory
    from agent.service.contracts import RunWorkflowGraphRequest
    from agent.service.runtime_launcher import RuntimeExecutionLauncher
    from agent.service.service import AgentService

    harness.setup_provider(monkeypatch, [
        ("later", harness.intent("later", artifact=False)),
        ("later", "完成"),
    ])

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
            def broken_for_n1(request, *, run_id, **kwargs):
                if request.request_id.endswith("-n1-run"):
                    raise RuntimeError("n1 context boom")
                return real_create(request, run_id=run_id, **kwargs)

            real_create = service._contexts.create_run
            service._contexts.create_run = broken_for_n1
            _running_parent(service, store, tmp_path)
            store.upsert_workflow_graph("t", "g-fail", definition_json=(
                _json.dumps({"nodes": [
                    {"id": "n1", "type": "task", "request_text": "boom"},
                    {"id": "n2", "type": "task", "request_text": "dep"},
                    {"id": "n3", "type": "task", "request_text": "later"},
                ], "edges": [{"from": "n1", "to": "n2"}]})))
            steps = await service.run_graph(RunWorkflowGraphRequest(
                tenant_id="t", user_id="u", session_id="s",
                request_id="g-1", graph_id="g-fail",
                parent_run_id="run-parent"))
            assert [s.status for s in steps] == ["PENDING"] * 3

            async def _wait(check):
                for _ in range(200):
                    value = check()
                    if value:
                        return value
                    await asyncio.sleep(0.05)
                raise AssertionError("timeout")

            # C 独立 — 即使 A 失败也正常执行。
            def _n3():
                for handoff in store.list_handoffs("t", "s"):
                    if not handoff.target_run_id:
                        continue
                    child = f"s:subtask:{handoff.target_task_id}"
                    head = store.get_run_head(
                        "t", handoff.target_run_id, session_id=child)
                    if head and head.run_status == "COMPLETED":
                        return True
                return None
            assert await _wait(_n3)
            # A 失败（context 构造注入）— 终态可读。
            n1_task = (
                "task-delegate-"
                + hashlib.sha256(b"g-1-n1").hexdigest()[:12]
            )
            n1_child = f"s:subtask:{n1_task}"
            n1_handoff = next(
                h for h in store.list_handoffs("t", "s")
                if h.target_task_id == n1_task
            )
            head = await _wait(lambda: store.get_run_head(
                "t", n1_handoff.target_run_id, session_id=n1_child))
            assert head.run_status == "FAILED_TERMINAL"
            # B 从未被接纳 — 它的 Task/Handoff/Run 不存在。
            n2_task = (
                "task-delegate-"
                + hashlib.sha256(b"g-1-n2").hexdigest()[:12]
            )
            assert store.get_task_head("t", n2_task) is None
            assert all(
                h.target_task_id != n2_task
                for h in store.list_handoffs("t", n1_child)
            )
        finally:
            await service.close()

    asyncio.run(scenario())


def test_sidecar_graph_run_does_not_block_control(
    monkeypatch, tmp_path: Path
) -> None:
    """A07: 图执行在后台任务推进 — 子 Run 挂起时，同一连接上的
    控制请求仍能返回；释放后图继续推进。"""
    import asyncio
    import io
    import json
    import queue
    import threading

    import tests.test_ha_r1_harness as harness
    from agent.runtime_store.sqlite import SqliteRuntimeStore
    from agent.service.context_factory import ServiceContextFactory
    from agent.service.local_sidecar.dispatcher import SidecarDispatcher
    from agent.service.local_sidecar.server import serve
    from agent.service.runtime_launcher import RuntimeExecutionLauncher
    from agent.service.service import AgentService

    harness.setup_provider(monkeypatch, [
        ("parent task", harness.intent("parent task", artifact=False)),
        ("parent task", "parent done"),
        ("step-one", harness.intent("step-one", artifact=False)),
        ("step-one", "done one"),
        ("step-two", harness.intent("step-two", artifact=False)),
        ("step-two", "done two"),
    ])

    class _QueuedStdin(io.TextIOBase):
        def __init__(self) -> None:
            self._q: queue.Queue[str] = queue.Queue()

        def feed(self, line: str) -> None:
            self._q.put(line)

        def readline(self, _size: int = -1) -> str:
            return self._q.get()

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
        store.ensure_session("t", "s")
        store.upsert_workspace("t", "ws", path=str(tmp_path))
        store.assign_session_workspace("t", "s", "ws")
        # 把子 Run 的 Context 构造停在门后 — 复现"子 Run 未终结，
        # 图任务仍在等待"的阻塞面。
        entered = threading.Event()
        gate = threading.Event()
        real_create = service._contexts.create_run

        def delayed(request, *, run_id, **kwargs):
            if str(request.request_id).endswith("-n1-run"):
                entered.set()
                gate.wait()
            return real_create(request, run_id=run_id, **kwargs)

        service._contexts.create_run = delayed
        dispatcher = SidecarDispatcher(service, diagnostics=io.StringIO())
        stdin = _QueuedStdin()
        stdout = io.StringIO()
        server = asyncio.create_task(
            serve(dispatcher, stdin=stdin, stdout=stdout,
                  diagnostics=io.StringIO()))

        def feed(req_id: str, method: str, params: dict | None = None):
            stdin.feed(json.dumps(
                {"id": req_id, "method": method,
                 "params": params or {}}) + "\n")

        async def respond(req_id: str) -> dict:
            for _ in range(200):
                for line in stdout.getvalue().splitlines():
                    if not line.strip():
                        continue
                    parsed = json.loads(line)
                    if parsed.get("id") == req_id:
                        return parsed
                await asyncio.sleep(0.05)
            raise AssertionError(f"no response for {req_id}")

        try:
            feed("u1", "upsert_workflow_graph", {
                "tenant_id": "t", "user_id": "u", "session_id": "s",
                "request_id": "u1", "graph_id": "g-1",
                "definition": {"nodes": [
                    {"id": "n1", "type": "task",
                     "request_text": "step-one"},
                    {"id": "n2", "type": "task",
                     "request_text": "step-two"},
                ], "edges": [{"from": "n1", "to": "n2"}]},
            })
            assert "result" in await respond("u1")
            feed("r1", "start_run", {
                "tenant_id": "t", "user_id": "u", "session_id": "s",
                "request_id": "r1", "request_text": "parent task"})
            parent = await respond("r1")
            parent_run_id = parent["result"]["run_id"]
            for _ in range(200):
                head = store.get_run_head(
                    "t", parent_run_id, session_id="s")
                if head is not None and head.run_status == "COMPLETED":
                    break
                await asyncio.sleep(0.05)

            feed("g1", "run_graph", {
                "tenant_id": "t", "user_id": "u", "session_id": "s",
                "request_id": "g-1", "graph_id": "g-1",
                "parent_run_id": parent_run_id})
            # 若 run_graph 仍串行等待子 Run，子 Run 停在 gate 上时
            # 这里永远不会返回 — 死锁即回归。
            graph_resp = await respond("g1")
            assert [s["status"] for s in graph_resp["result"]] == [
                "PENDING", "PENDING"]
            assert entered.wait(timeout=10)

            # 子 Run 未终结、图任务仍在等待 — 同一连接的控制请求
            # 仍要立即返回。
            feed("h1", "health")
            assert "result" in await respond("h1")
            feed("lg", "list_workflow_graphs", {
                "tenant_id": "t", "user_id": "u", "session_id": "s",
                "request_id": "lg"})
            assert "result" in await respond("lg")

            gate.set()  # 放行 — 图链继续：n1 完成 → n2 启动。
            child_run_id = None
            for _ in range(200):
                handoffs = store.list_handoffs("t", "s")
                if handoffs and handoffs[0].target_run_id:
                    child_run_id = handoffs[0].target_run_id
                    break
                await asyncio.sleep(0.05)
            assert child_run_id
            feed("x", "shutdown")
            assert "result" in await respond("x")
        finally:
            gate.set()
            await asyncio.wait_for(server, timeout=10)
        for line in stdout.getvalue().splitlines():
            json.loads(line)  # 每行都是完整的协议响应

    asyncio.run(scenario())


@pytest.mark.host_integration
def test_sidecar_web_tools_registered_before_first_run(tmp_path: Path) -> None:
    """A21: 真实 python -m 入口在首个 Run 前完成 Web 注册 —
    假 Provider 断言首轮目录含 web_search/web_fetch；依赖缺失时
    目录不含；JSONL health 正常。意图为非研究执行任务，Planner
    不会按需加载 Web — 目录证据只能来自入口注册。"""
    import http.server
    import os
    import threading
    import time

    captured: list[list[str]] = []
    intent = {
        "domain": "开发", "action": "code", "target": "",
        "entities": [], "confidence": 0.95,
        "requires_execution": True, "summary": "写一个文件",
        "resolved_request": "写一个文件",
        "conversation_relation": "new_request",
        "requested_outcomes": ["USER_VISIBLE_OUTPUT"],
        "artifact_required": True,
        "delivery_requirements": [{
            "id": "d", "kind": "file", "quantity": 1,
            "target": "", "acceptance_criteria": [],
        }],
        "prohibited_effects": [], "acceptance_criteria": [],
        "write_targets": [], "external_effects": [],
        "search_query": "", "search_timeliness": "any",
        "context_needs": {"memory": False, "repository": False},
        "memory_action": "none",
        "freshness_required": False,
        "source_grounding_required": False,
    }

    class _FakeProvider(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0) or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            tools = [
                item["function"]["name"]
                for item in body.get("tools") or []
            ]
            captured.append(tools)
            # 意图/规划轮不带 tools — 回契约 JSON；执行轮带目录 — 回文本。
            content = (
                "done" if tools
                else json.dumps(intent, ensure_ascii=False)
            )
            payload = json.dumps({
                "model": "m", "created_at": "x",
                "message": {"role": "assistant", "content": content},
                "done": True, "done_reason": "stop",
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), _FakeProvider)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"

    # 可用替身 — duckduckgo-mcp-server 空壳固定在 PATH 上。
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "duckduckgo-mcp-server"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)

    def _launch(*, db_name: str, web_dep: bool):
        env = {
            "OPENAI_API_KEY": "",
            "LLM_API_KEY": "",
            "P2_SECONDARY_API_KEY": "",
            "TSAGENT_LLM_PROVIDER": "ollama",
            "OLLAMA_BASE_URL": base_url,
        }
        if web_dep:
            env["PATH"] = (
                str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
            )
        else:
            # duckduckgo-mcp-server 不在此 PATH 上 — 依赖缺失分支。
            env["PATH"] = "/usr/bin:/bin:/usr/local/bin"
        return _spawn_sidecar(
            tmp_path,
            database_path=tmp_path / db_name,
            extra_env=env,
        )

    def _drive(process) -> set[str]:
        _send(process, {"id": "h1", "method": "health", "params": {}})
        health = _read_response(process)
        assert health.get("id") == "h1" and health.get("result")
        _send(process, {
            "id": "r1", "method": "start_run",
            "params": {
                "tenant_id": "t", "user_id": "u", "session_id": "s",
                "request_id": "r1", "request_text": "写一个文件",
            },
        })
        started = _read_response(process)
        assert started.get("id") == "r1" and started.get("result")
        deadline = time.time() + 90
        while time.time() < deadline:
            for tools in captured:
                if tools:
                    return set(tools)
            time.sleep(0.1)
        raise AssertionError("provider never received a tool catalog")

    try:
        process = _launch(db_name="web-ok.sqlite", web_dep=True)
        try:
            catalog = _drive(process)
            assert "web_search" in catalog
            assert "web_fetch" in catalog
        finally:
            _terminate(process)

        captured.clear()
        process = _launch(db_name="web-missing.sqlite", web_dep=False)
        try:
            catalog = _drive(process)
            assert "web_search" not in catalog
            assert "web_fetch" not in catalog
        finally:
            _terminate(process)
    finally:
        server.shutdown()


def test_read_artifact_body_and_stale_come_from_one_byte_read(
    monkeypatch, tmp_path: Path
) -> None:
    """A27: 预览正文与 stale 校验来自同一次字节读取 — 文件替换
    注入不得拼出混合版本。缺失、二进制、脱敏各有独立结果。"""
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
    body_a = "artifact-body-A"
    target = workspace_root / "output" / "result.txt"
    target.write_text(body_a, encoding="utf-8")
    digest_a = hashlib.sha256(target.read_bytes()).hexdigest()
    binary = workspace_root / "output" / "blob.bin"
    binary.write_bytes(b"\xff\xfe\x00\x01")

    store = SqliteRuntimeStore.open(tmp_path / "runtime.sqlite")
    request = StartRunRequest(
        tenant_id="tenant-1", user_id="user-1",
        session_id="session-1", request_id="request-art",
        request_text="write output/result.txt", run_id="run-art",
    )
    view = None
    try:
        store.reserve_service_start(
            request.tenant_id, request.session_id,
            requested_run_id=request.run_id, request_id=request.request_id,
            request_digest=request.request_digest, writer_id="writer-art",
            external_reference=encode_request_reference(
                request, run_id=request.run_id or "",
                workspace_root=str(workspace_root.resolve()),
            ),
        )
        view = DurableRuntimeStoreView(
            store, tenant_id=request.tenant_id,
            session_id=request.session_id, run_id=request.run_id or "",
            request_id=request.request_id, writer_id="writer-art",
        )
        view.transition_run_with_event(
            run_status="RUNNING", event_id="run-started:art",
            event_type="run_started", timestamp="2026-08-11T00:00:00Z",
            payload={"request_id": request.request_id},
            expected_status="CREATED",
        )
        artifacts = (
            ArtifactCommitFact(
                artifact_id="artifact-art-result", artifact_type="file",
                reference="output/result.txt", digest=digest_a,
                producer_workflow_id="runtime-execution",
                producer_stage_id="task:art-write",
                producer_task_id="art-write",
            ),
            ArtifactCommitFact(
                artifact_id="artifact-art-bin", artifact_type="file",
                reference="output/blob.bin",
                digest=hashlib.sha256(b"\xff\xfe\x00\x01").hexdigest(),
                producer_workflow_id="runtime-execution",
                producer_stage_id="task:art-write",
                producer_task_id="art-write",
            ),
        )
        view.transition_run_with_event(
            run_status="COMPLETED", event_id="run-completed:art",
            event_type="run_completed", timestamp="2026-08-11T00:00:01Z",
            payload={"request_id": request.request_id},
            run_output={"text": "written",
                        "artifact_ids": [a.artifact_id for a in artifacts]},
            artifacts=artifacts,
        )
    finally:
        if view is not None:
            view.close()
        store.close()

    service = _workspace_file_service(tmp_path, workspace_root=workspace_root)
    lookup = {
        "tenant_id": "tenant-1", "user_id": "user-1",
        "session_id": "session-1", "run_id": "run-art",
    }

    async def _read(artifact_id: str, req: str):
        from agent.service.contracts import ReadArtifactRequest
        return await service.read_artifact(
            ReadArtifactRequest(
                **lookup, request_id=req, artifact_id=artifact_id
            )
        )

    async def scenario() -> None:
        # 相同字节 — 新鲜视图。
        content = await _read("artifact-art-result", "ra-1")
        assert content.result == "ok"
        assert content.text == body_a
        assert content.stale is False

        # 替换探针 — 文件在预览时换成另一版本。正文与 digest
        # 必须来自同一读取：只应读一次字节，视图不能混合版本。
        body_b = "artifact-body-B"
        target.write_text(body_b, encoding="utf-8")
        reads: list[bytes] = []
        real_read_bytes = Path.read_bytes

        def spy_read_bytes(path, *args, **kwargs):
            reads.append(real_read_bytes(path, *args, **kwargs))
            return reads[-1]

        monkeypatch.setattr(Path, "read_bytes", spy_read_bytes)
        content = await _read("artifact-art-result", "ra-2")
        assert len(reads) == 1, (
            f"artifact preview must read bytes once, got {len(reads)}"
        )
        assert content.result == "ok"
        assert content.text == body_b          # 全部是 B — 不混版本
        assert content.stale is True           # B ≠ 已提交 digest A

        # 脱敏内容 — 正文投影带 [REDACTED]，digest 仍按原始字节。
        monkeypatch.undo()
        target.write_text('api_key = "sk-SECRET000111222333"\n')
        content = await _read("artifact-art-result", "ra-3")
        assert "sk-SECRET000111222333" not in content.text
        assert content.stale is True

        # 缺失 — 独立结果，不抛未定义错误。
        target.unlink()
        content = await _read("artifact-art-result", "ra-4")
        assert content.result == "missing"
        assert content.text == "" and content.stale is False

        # 二进制 — 独立结果。
        content = await _read("artifact-art-bin", "ra-5")
        assert content.result == "unsupported"
        assert content.text == "" and content.stale is False

    import asyncio
    asyncio.run(scenario())


def test_provider_settings_non_first_model_persists_for_next_run(
    tmp_path: Path,
) -> None:
    """A13: 选择非首项模型 → update_settings → get_settings 重开
    仍返回该模型 → 下一个 Provider 实例按同一模型构造。"""
    import asyncio

    import tests.test_ha_r1_harness as harness
    from agent import llm as _llm_module
    from agent.service.contracts import (
        GetSettingsRequest,
        UpdateSettingsRequest,
    )

    async def scenario() -> None:
        service, _store = harness.service_at(tmp_path)
        router = _llm_module.llm
        original = router.ollama_model
        try:
            await service.update_settings(UpdateSettingsRequest(
                tenant_id="t", user_id="u", session_id="s",
                request_id="set-1",
                ollama_model="qwen3:14b",
            ))
            reopened = await service.get_settings(GetSettingsRequest(
                tenant_id="t", user_id="u", session_id="s",
                request_id="set-2",
            ))
            assert reopened.ollama_model == "qwen3:14b"
            # 实例缓存已失效 — 下一个 Provider 实例按新模型构造。
            provider = router._provider_ollama
            assert provider.model == "qwen3:14b"
        finally:
            router.configure(ollama_model=original)
            await service.close()

    asyncio.run(scenario())


def _a24_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any]:
    from agent.memory import long_term
    from agent.service import AgentService

    monkeypatch.setattr(long_term, "FACTS_DB_PATH", tmp_path / "facts.db")
    long_term._init_facts_db()
    store = SqliteRuntimeStore.open(tmp_path / "runtime.sqlite")
    return AgentService(runtime_store=store, launcher=None), store  # type: ignore[arg-type]


def _a24_seed_exploring(store: Any, motif_id: str = "m-1") -> None:
    store.create_task_head("t", "s", "task-1")
    store.initialize_run("t", "s", "run-1", "req-1", task_id="task-1")
    store.initialize_run("t", "s", "run-stray", "req-2", task_id="task-2")
    store.upsert_motif("t", motif_id, text="探索记忆语义")
    store.update_motif(
        "t", motif_id, status="exploring", assigned_task_id="task-1"
    )


def test_a24_motif_lifecycle_report_verdict_and_reject(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A24: create → assign → report → verdict → memory.  Existence and the
    reporting run are validated by identity before any write; rejection is
    durable and sends the motif back to exploring."""
    from agent.service.contracts import (
        AssignMotifRequest, RejectMotifRequest, ReportMotifRequest,
        ResolveMotifRequest,
    )

    service, store = _a24_service(tmp_path, monkeypatch)
    _a24_seed_exploring(store)

    def report(motif_id: str, run_id: str, req: str) -> ReportMotifRequest:
        return ReportMotifRequest(
            tenant_id="t", user_id="u", session_id="s", request_id=req,
            motif_id=motif_id, resolution_run_id=run_id,
            resolution="已理解的结论",
        )

    def resolve(motif_id: str, run_id: str, req: str) -> ResolveMotifRequest:
        return ResolveMotifRequest(
            tenant_id="t", user_id="u", session_id="s", request_id=req,
            motif_id=motif_id, resolution_run_id=run_id,
            resolution="已理解的结论",
        )

    async def scenario() -> None:
        # 汇报前直接裁决 — 拒绝。
        with pytest.raises(AgentServiceError) as premature:
            await service.resolve_motif(resolve("m-1", "run-1", "r-early"))
        assert premature.value.code == ServiceErrorCode.INVALID_REQUEST

        # 不属于该母题任务的 Run 汇报 — 拒绝，母题不变。
        with pytest.raises(AgentServiceError) as stray:
            await service.report_motif(report("m-1", "run-stray", "r-stray"))
        assert stray.value.code == ServiceErrorCode.INVALID_REQUEST
        assert store.get_motif("t", "m-1").status == "exploring"

        # 委派任务的 Run 汇报 — 记录为待裁决。
        await service.report_motif(report("m-1", "run-1", "r-report"))
        reported = store.get_motif("t", "m-1")
        assert reported.status == "reported"
        assert reported.resolution_run_id == "run-1"

        # 裁决认可 — 记忆提交后母题 resolved。
        summaries = await service.resolve_motif(resolve("m-1", "run-1", "r-verdict"))
        resolved = next(s for s in summaries if s.motif_id == "m-1")
        assert resolved.status == "resolved"
        assert resolved.resolution == "已理解的结论"
        # 证据 Run 所属会话随摘要投影 — 跨会话链接由此定位。
        assert resolved.resolution_session_id == "s"

        # 同一裁决重放 — 幂等。
        replay = await service.resolve_motif(resolve("m-1", "run-1", "r-verdict2"))
        assert next(s for s in replay if s.motif_id == "m-1").status == "resolved"
        with pytest.raises(AgentServiceError) as conflict:
            await service.resolve_motif(resolve("m-1", "run-stray", "r-again"))
        assert conflict.value.code == ServiceErrorCode.INVALID_REQUEST

        # 不认可 — 持久退回 exploring，报告清除。
        _a24_seed_exploring(store, "m-2")
        await service.report_motif(report("m-2", "run-1", "r-report2"))
        await service.reject_motif(RejectMotifRequest(
            tenant_id="t", user_id="u", session_id="s",
            request_id="r-reject", motif_id="m-2",
        ))
        bounced = store.get_motif("t", "m-2")
        assert bounced.status == "exploring"
        assert bounced.resolution == ""

        # 未委派的母题无法汇报。
        store.upsert_motif("t", "m-open", text="未委派的母题")
        with pytest.raises(AgentServiceError) as unassigned:
            await service.report_motif(report("m-open", "run-1", "r-open"))
        assert unassigned.value.code == ServiceErrorCode.INVALID_REQUEST

    try:
        _run(scenario())
    finally:
        _run(service.close())


def test_motif_summary_projects_resolution_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The resolution run's owning session rides the summary — list_motifs is
    tenant-scoped, so a caller in another session must still learn where the
    evidence run lives."""
    from agent.service.contracts import (
        ListMotifsRequest, ReportMotifRequest, ResolveMotifRequest,
    )

    service, store = _a24_service(tmp_path, monkeypatch)
    # 母题租户级共享；证据 Run 属于会话 s-a，调用方在 s-b。
    store.create_task_head("t", "s-a", "task-1")
    store.initialize_run("t", "s-a", "run-1", "req-1", task_id="task-1")
    store.upsert_motif("t", "m-1", text="跨会话母题")
    store.update_motif("t", "m-1", status="exploring", assigned_task_id="task-1")

    async def scenario() -> None:
        await service.report_motif(ReportMotifRequest(
            tenant_id="t", user_id="u", session_id="s-b",
            request_id="r-report", motif_id="m-1",
            resolution_run_id="run-1", resolution="结论",
        ))
        await service.resolve_motif(ResolveMotifRequest(
            tenant_id="t", user_id="u", session_id="s-b",
            request_id="r-verdict", motif_id="m-1",
            resolution_run_id="run-1", resolution="结论",
        ))
        listed = await service.list_motifs(ListMotifsRequest(
            tenant_id="t", user_id="u", session_id="s-b",
            request_id="r-list",
        ))
        summary = next(s for s in listed if s.motif_id == "m-1")
        assert summary.resolution_run_id == "run-1"
        assert summary.resolution_session_id == "s-a"

    try:
        _run(scenario())
    finally:
        _run(service.close())


def test_a24_motif_identity_beyond_page_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A24: validation is by identity — a motif past the 100-row list page
    and a task's run must still resolve normally."""
    from agent.service.contracts import ReportMotifRequest, ResolveMotifRequest

    service, store = _a24_service(tmp_path, monkeypatch)
    _a24_seed_exploring(store, "m-old")
    for i in range(101):
        store.upsert_motif("t", f"m-filler-{i:03d}", text=f"filler {i}")

    async def scenario() -> None:
        await service.report_motif(ReportMotifRequest(
            tenant_id="t", user_id="u", session_id="s", request_id="r-101",
            motif_id="m-old", resolution_run_id="run-1", resolution="结论",
        ))
        summaries = await service.resolve_motif(ResolveMotifRequest(
            tenant_id="t", user_id="u", session_id="s", request_id="r-102",
            motif_id="m-old", resolution_run_id="run-1", resolution="结论",
        ))
        assert next(s for s in summaries if s.motif_id == "m-old").status == "resolved"

    try:
        _run(scenario())
    finally:
        _run(service.close())


def test_a24_resolve_retry_after_memory_write_converges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A24: memory committed but motif update failed — retrying with a NEW
    request_id must converge on the same commit evidence."""
    from agent.service.contracts import ReportMotifRequest, ResolveMotifRequest

    service, store = _a24_service(tmp_path, monkeypatch)
    _a24_seed_exploring(store)

    def resolve(req: str) -> ResolveMotifRequest:
        return ResolveMotifRequest(
            tenant_id="t", user_id="u", session_id="s", request_id=req,
            motif_id="m-1", resolution_run_id="run-1", resolution="已理解的结论",
        )

    async def scenario() -> None:
        await service.report_motif(ReportMotifRequest(
            tenant_id="t", user_id="u", session_id="s", request_id="r-report",
            motif_id="m-1", resolution_run_id="run-1", resolution="已理解的结论",
        ))
        # 记忆写入与母题更新之间注入一次失败 — 事实已存在，母题仍 reported。
        real_update = store.update_motif
        calls = {"n": 0}

        def flaky_update(*args: Any, **kwargs: Any) -> Any:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("injected write failure")
            return real_update(*args, **kwargs)

        monkeypatch.setattr(store, "update_motif", flaky_update)
        with pytest.raises(RuntimeError):
            await service.resolve_motif(resolve("r-first"))
        assert store.get_motif("t", "m-1").status == "reported"

        # 新 request_id 重试 — 稳定证据去重，状态与证据收敛。
        summaries = await service.resolve_motif(resolve("r-retry"))
        assert next(s for s in summaries if s.motif_id == "m-1").status == "resolved"

    try:
        _run(scenario())
    finally:
        _run(service.close())


def test_a24_assign_motif_idempotent_delegation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A24: assign_motif delegates through A06 with a motif-derived key —
    repeated assigns with different request ids converge on one child
    Task and one Handoff."""
    from agent.service.contracts import AssignMotifRequest

    service, store = _a24_service(tmp_path, monkeypatch)
    store.initialize_run("t", "s", "run-parent", "req-parent")
    store.upsert_motif("t", "m-1", text="探索记忆语义")

    def assign(req: str) -> AssignMotifRequest:
        return AssignMotifRequest(
            tenant_id="t", user_id="u", session_id="s", request_id=req,
            motif_id="m-1", parent_run_id="run-parent",
        )

    async def scenario() -> None:
        first = await service.assign_motif(assign("r-a"))
        assert store.get_motif("t", "m-1").status == "exploring"
        assert store.get_motif("t", "m-1").assigned_task_id == first["task_id"]

        # 重复委派（不同 request id）— 同一 task/handoff，母题不重复绑定。
        second = await service.assign_motif(assign("r-b"))
        assert second["task_id"] == first["task_id"]
        assert second["handoff_id"] == first["handoff_id"]
        delegate_tasks = [
            h for h in store.list_task_heads("t", "s")
            if h.task_id == first["task_id"]
        ]
        assert len(delegate_tasks) == 1

        # 不存在的母题按身份拒绝。
        with pytest.raises(AgentServiceError) as missing:
            await service.assign_motif(AssignMotifRequest(
                tenant_id="t", user_id="u", session_id="s", request_id="r-x",
                motif_id="m-nope", parent_run_id="run-parent",
            ))
        assert missing.value.code == ServiceErrorCode.INVALID_REQUEST

    try:
        _run(scenario())
    finally:
        _run(service.close())


def test_a23_eval_verdict_contract_round_trip(tmp_path: Path) -> None:
    """A23: 人工裁决词表由单一合同约束 — 每个公开裁决经真实 dispatcher
    提交后按会话归属重读同值；非法值与执行词汇表返回可诊断的
    INVALID_REQUEST（不再是 INTERNAL_ERROR）；Saturn 的执行裁决仍可读。"""
    from agent.service.local_sidecar.dispatcher import SidecarDispatcher

    service = _workspace_file_service(tmp_path)
    dispatcher = SidecarDispatcher(service, diagnostics=sys.stderr)
    base = {"tenant_id": "tenant-1", "user_id": "user-1", "session_id": "session-1"}

    async def scenario() -> None:
        for verdict in ("keep_candidate", "keep_baseline", "inconclusive"):
            resp = await dispatcher.dispatch(LocalRpcRequest(
                f"a23-{verdict}",
                LocalTransportMethod.RECORD_EVALUATION,
                {
                    **base, "request_id": f"req-{verdict}",
                    "name": f"bio-{verdict}", "run_id": "run-1",
                    "baseline_ref": "baseline:defaults",
                    "candidate_ref": "candidate:p=1",
                    "verdict": verdict, "notes": "n",
                },
            ))
            assert isinstance(resp, LocalRpcSuccess)
            assert resp.to_dict()["result"]["verdict"] == verdict

        listed = await dispatcher.dispatch(LocalRpcRequest(
            "a23-list", LocalTransportMethod.LIST_EVALUATIONS,
            {**base, "request_id": "req-list"}))
        rows = listed.to_dict()["result"]
        verdicts = {row["verdict"] for row in rows}
        assert {"keep_candidate", "keep_baseline", "inconclusive"} <= verdicts
        names = {row["name"] for row in rows}
        assert {"bio-keep_candidate", "bio-keep_baseline", "bio-inconclusive"} <= names

        # 非法裁决 — 可诊断输入错误，不是 INTERNAL_ERROR。
        bad = await dispatcher.dispatch(LocalRpcRequest(
            "a23-bad", LocalTransportMethod.RECORD_EVALUATION,
            {**base, "request_id": "req-bad", "verdict": "bogus"}))
        assert isinstance(bad, LocalRpcFailure)
        assert bad.error.code == "INVALID_REQUEST"

        # 执行词汇表不属于人工裁决入口。
        machine = await dispatcher.dispatch(LocalRpcRequest(
            "a23-exec", LocalTransportMethod.RECORD_EVALUATION,
            {**base, "request_id": "req-exec", "verdict": "pass"}))
        assert isinstance(machine, LocalRpcFailure)
        assert machine.error.code == "INVALID_REQUEST"

        # Saturn 的执行裁决经内部路径写库 — 现有记录仍可读取。
        service._store.record_evaluation(
            "tenant-1", "session-1", "eval-sched-1",
            name="schedule:s-1", run_id="run-9",
            score=1.0, verdict="pass", notes="schedule outcome",
        )
        listed2 = await dispatcher.dispatch(LocalRpcRequest(
            "a23-list2", LocalTransportMethod.LIST_EVALUATIONS,
            {**base, "request_id": "req-list2"}))
        assert "pass" in {row["verdict"] for row in listed2.to_dict()["result"]}

    try:
        _run(scenario())
    finally:
        _run(service.close())

    # 重开 — 持久值与会话归属不变。
    service2 = _workspace_file_service(tmp_path)
    dispatcher2 = SidecarDispatcher(service2, diagnostics=sys.stderr)

    async def reopened() -> None:
        listed = await dispatcher2.dispatch(LocalRpcRequest(
            "a23-reopen", LocalTransportMethod.LIST_EVALUATIONS,
            {**base, "request_id": "req-reopen"}))
        rows = listed.to_dict()["result"]
        assert {
            "keep_candidate", "keep_baseline", "inconclusive", "pass",
        } <= {row["verdict"] for row in rows}
        # 会话范围 — 别的会话列表不含这些记录。
        other = await dispatcher2.dispatch(LocalRpcRequest(
            "a23-other", LocalTransportMethod.LIST_EVALUATIONS,
            {**base, "session_id": "session-2", "request_id": "req-other"}))
        other_names = {row["name"] for row in other.to_dict()["result"]}
        assert not {
            "bio-keep_candidate", "bio-keep_baseline", "bio-inconclusive",
            "schedule:s-1",
        } & other_names

    try:
        _run(reopened())
    finally:
        _run(service2.close())



def test_a10_forget_scoped_to_displayed_fact_and_not_reme(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A10: Forget deletes the governed Durable Fact in the displayed scope.
    Same (category, key) under another scope survives; the ReMe long-term
    layer is a separate owner — forgetting a fact never reaches it."""
    from agent.memory import long_term
    from agent.mcp import reme
    from agent.service.contracts import ForgetFactRequest

    service, _store = _a24_service(tmp_path, monkeypatch)
    user = "t:u"
    for scope in ("tenant", "session:s-1"):
        long_term._persist_fact(
            user, "preference", "theme", "dark",
            scope=scope, action="INSERT",
            evidence_id=f"ev-{scope}", source_kind="manual",
            source_ref="test",
        )
    calls: list[str] = []

    async def reme_spy(*args: Any, **kwargs: Any) -> str:
        calls.append(str(args))
        raise AssertionError("forget_fact must not call ReMe")

    monkeypatch.setattr(reme, "call_reme_tool", reme_spy)

    def req(scope: str) -> ForgetFactRequest:
        return ForgetFactRequest(
            tenant_id="t", user_id="u", session_id="s-1",
            request_id=f"req-{scope or 'all'}", category="preference",
            key="theme", scope=scope,
        )

    async def scenario() -> None:
        # 指定作用域 — 只删该作用域的记录。
        result = await service.forget_fact(req("session:s-1"))
        assert result.removed is True
        rows = long_term.list_fact_records(user)
        assert [(r["scope"]) for r in rows] == ["tenant"]

        # 未指定作用域 — 删除该 (category, key) 的其余记录（服务层合同）。
        result = await service.forget_fact(req(""))
        assert result.removed is True
        assert long_term.list_fact_records(user) == []

        # 已删除 — removed=False，不报错。
        result = await service.forget_fact(req("tenant"))
        assert result.removed is False

    try:
        _run(scenario())
    finally:
        _run(service.close())
    assert calls == []


def test_read_artifact_preview_serves_guarded_binary(
    tmp_path: Path,
) -> None:
    """read_artifact_preview returns base64 + allowlist mime for media
    artifacts, capped by size; other extensions report unsupported."""
    import base64
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
    png_bytes = b"\x89PNG\r\n\x1a\n" + bytes(range(64))
    (workspace_root / "output" / "shot.png").write_bytes(png_bytes)
    (workspace_root / "output" / "data.xyz").write_bytes(b"\x00" * 16)
    (workspace_root / "output" / "big.mp4").write_bytes(b"\x00" * 32)
    (workspace_root / "output" / "over.mp4").write_bytes(b"\x00" * 32)
    (workspace_root / "output" / "icon.svg").write_text(
        "<svg><text>sk-secret-123</text></svg>", encoding="utf-8")

    database = tmp_path / "runtime.sqlite"
    store = SqliteRuntimeStore.open(database)
    request = StartRunRequest(
        tenant_id="tenant-1",
        user_id="user-1",
        session_id="session-1",
        request_id="request-prev",
        request_text="produce media",
        run_id="run-prev",
    )
    view = None
    try:
        store.reserve_service_start(
            request.tenant_id,
            request.session_id,
            requested_run_id=request.run_id,
            request_id=request.request_id,
            request_digest=request.request_digest,
            writer_id="writer-prev",
            external_reference=encode_request_reference(
                request,
                run_id=request.run_id or "",
                workspace_root=str(workspace_root.resolve()),
            ),
        )
        view = DurableRuntimeStoreView(
            store,
            tenant_id=request.tenant_id,
            session_id=request.session_id,
            run_id=request.run_id or "",
            request_id=request.request_id,
            writer_id="writer-prev",
        )
        view.transition_run_with_event(
            run_status="RUNNING",
            event_id="run-started:prev",
            event_type="run_started",
            timestamp="2026-08-11T00:00:00Z",
            payload={"request_id": request.request_id},
            expected_status="CREATED",
        )
        artifacts = tuple(
            ArtifactCommitFact(
                artifact_id=f"artifact-{name}",
                artifact_type="file",
                reference=f"output/{name}",
                digest=hashlib.sha256(
                    (workspace_root / "output" / name).read_bytes()
                ).hexdigest(),
                producer_workflow_id="runtime-execution",
                producer_stage_id="task:prev",
                producer_task_id="prev",
            )
            for name in ("shot.png", "data.xyz", "big.mp4", "over.mp4", "icon.svg")
        )
        view.transition_run_with_event(
            run_status="COMPLETED",
            event_id="run-completed:prev",
            event_type="run_completed",
            timestamp="2026-08-11T00:00:01Z",
            payload={"request_id": request.request_id},
            run_output={
                "text": "done",
                "artifact_ids": [a.artifact_id for a in artifacts],
            },
            artifacts=artifacts,
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
        "run_id": "run-prev",
    }

    async def scenario() -> None:
        preview = await service.read_artifact_preview(
            ReadArtifactRequest(
                **lookup, request_id="req-p1", artifact_id="artifact-shot.png"
            )
        )
        assert preview.result == "ok"
        assert preview.mime == "image/png"
        assert preview.size == len(png_bytes)
        assert base64.b64decode(preview.data_b64) == png_bytes
        assert preview.stale is False

        unsupported = await service.read_artifact_preview(
            ReadArtifactRequest(
                **lookup, request_id="req-p2", artifact_id="artifact-data.xyz"
            )
        )
        assert unsupported.result == "unsupported"

        big = await service.read_artifact_preview(
            ReadArtifactRequest(
                **lookup, request_id="req-p3", artifact_id="artifact-big.mp4"
            )
        )
        assert big.result == "ok"  # 32 bytes < 8 MiB cap

        # SVG 不走字节路径 — 它必须经 read_artifact 的脱敏文本投影；
        # 白名单曾误收 .svg 导致原始字节绕开 redaction 外泄。
        svg = await service.read_artifact_preview(
            ReadArtifactRequest(
                **lookup, request_id="req-p5", artifact_id="artifact-icon.svg"
            )
        )
        assert svg.result == "unsupported"
        assert svg.data_b64 == ""

        # file grew past the cap after the commit — size check reads
        # current bytes, not the committed digest
        (workspace_root / "output" / "over.mp4").write_bytes(
            b"\x00" * (service._PREVIEW_MAX_BYTES + 1)
        )
        capped = await service.read_artifact_preview(
            ReadArtifactRequest(
                **lookup, request_id="req-p4", artifact_id="artifact-over.mp4"
            )
        )
        assert capped.result == "too_large"
        assert capped.data_b64 == ""

    try:
        _run(scenario())
    finally:
        _run(service.close())


def test_subprocess_ws02_workspace_and_delegation_chain(tmp_path: Path) -> None:
    """WS-02 真实子进程验收（离线）：python -m agent.service.local_sidecar
    通过 stdio JSONL 完成 文件写/读/列 + 父 Run → delegate_run →
    子 Run 接纳 的完整链路；后台 provider 不要求可用，断言的是
    目录、持久事实与身份合同。"""
    process = _spawn_sidecar(tmp_path, extra_env={
        # Provider 必须确定性不可达 — 本机 Ollama 活着时真实推理会拖慢
        # Run 终态并把 shutdown 拖过 wait 超时；死端口 + 零冷却立即失败。
        "OLLAMA_BASE_URL": "http://127.0.0.1:1/v1",
        "TSAGENT_OLLAMA_TIMEOUT": "0.05",
        "TSAGENT_LLM_PROVIDER": "ollama",
        "TSAGENT_LLM_PROVIDER_RETRIES": "1",
        "TSAGENT_LLM_PROVIDER_COOLDOWN": "0",
        "TSAGENT_LLM_PROVIDER_RETRY_AFTER": "0",
        "OPENAI_API_KEY": "",
        "LLM_API_KEY": "",
        "P2_SECONDARY_API_KEY": "",
    })
    base = {"tenant_id": "tenant-1", "user_id": "user-1", "session_id": "s-ws02"}
    ws_root = tmp_path / "workspace"
    try:
        _send(process, {"id": "h", "method": "health", "params": {}})
        assert _read_response(process)["ok"] is True

        # 文件写 → 读 → 列：同一会话 workspace。
        _send(process, {
            "id": "w1", "method": "write_workspace_file",
            "params": {**base, "request_id": "w1", "path": "notes.txt", "content": "ws02-body"},
        })
        wrote = _read_response(process)
        assert wrote["ok"] is True, wrote
        _send(process, {
            "id": "r1", "method": "read_workspace_file",
            "params": {**base, "request_id": "r1", "path": "notes.txt"},
        })
        read = _read_response(process)
        assert read["ok"] is True, read
        assert read["result"]["text"] == "ws02-body"
        assert read["result"]["workspace_root"].startswith(str(ws_root))
        _send(process, {
            "id": "l1", "method": "list_workspace_files",
            "params": {**base, "request_id": "l1"},
        })
        listed = _read_response(process)
        assert listed["ok"] is True, listed
        assert any(f["path"] == "notes.txt" for f in listed["result"])

        # 父 Run 接纳 — provider 不可达只影响后台终态，不影响身份。
        _send(process, {
            "id": "s1", "method": "start_run",
            "params": {**base, "request_id": "p1", "request_text": "父任务",
                       "origin_planet": "uranus"},
        })
        parent = _read_response(process)
        assert parent["ok"] is True, parent
        parent_run = parent["result"]["run_id"]

        # 显式委派 → 子 task + handoff，child session 由 taskId 派生。
        _send(process, {
            "id": "d1", "method": "delegate_run",
            "params": {**base, "request_id": "d1", "parent_run_id": parent_run,
                       "request_text": "子任务", "target_planet": "neptune"},
        })
        delegation = _read_response(process)
        assert delegation["ok"] is True, delegation
        child_task = delegation["result"]["task_id"]
        assert delegation["result"]["workspace_root"]

        child_session = f"s-ws02:subtask:{child_task}"
        _send(process, {
            "id": "s2", "method": "start_run",
            "params": {**base, "session_id": child_session, "request_id": "s2",
                       "request_text": delegation["result"]["request_text"],
                       "task_id": child_task,
                       "origin_planet": "neptune",
                       "workspace_root": delegation["result"]["workspace_root"]},
        })
        child = _read_response(process)
        assert child["ok"] is True, child
        child_run = child["result"]["run_id"]

        # 子 Run 快照携带 task_id/origin_planet — 归属是持久事实。
        _send(process, {
            "id": "g2", "method": "get_run",
            "params": {**base, "session_id": child_session, "request_id": "g2",
                       "run_id": child_run},
        })
        snap = _read_response(process)
        assert snap["ok"] is True, snap
        assert snap["result"]["task_id"] == child_task
        assert snap["result"]["origin_planet"] == "neptune"
        # handoff 持久记录子 Run 目标。
        _send(process, {
            "id": "ho", "method": "list_handoffs",
            "params": {**base, "request_id": "ho"},
        })
        handoffs = _read_response(process)
        assert handoffs["ok"] is True, handoffs
        assert any(
            h["source_run_id"] == parent_run and h["target_task_id"] == child_task
            for h in handoffs["result"]
        ), handoffs["result"]

        _send(process, {"id": "bye", "method": "shutdown", "params": {}})
        assert _read_response(process)["ok"] is True
        assert process.wait(timeout=60) == 0
    finally:
        _terminate(process)


def _wait_run_terminal(
    process: subprocess.Popen[str],
    base: dict[str, Any],
    run_id: str,
    *,
    session_id: str,
    deadline: float = 120.0,
) -> dict[str, Any]:
    """Poll get_run until the durable head reaches a terminal status."""
    started = time.monotonic()
    tick = 0
    while time.monotonic() - started < deadline:
        tick += 1
        _send(process, {
            "id": f"poll-{run_id}-{tick}", "method": "get_run",
            "params": {**base, "session_id": session_id,
                       "request_id": f"poll-{tick}", "run_id": run_id},
        })
        snap = _read_response(process)
        assert snap["ok"] is True, snap
        status = str(snap["result"]["status"])
        if status.startswith(("COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT")):
            return snap["result"]
        time.sleep(0.4)
    raise AssertionError(f"run {run_id} did not reach a terminal status")


def test_subprocess_fake_provider_agent_tool_chain(tmp_path: Path) -> None:
    """真实入口 + 假 Provider：父子 Run 经模型工具调用真实写文件。

    覆盖此前未闭合的验收面 — 不是 RPC 直写，而是 sidecar 子进程内
    agent loop 从脚本化 Ollama 端点拿到 write_file tool_call、经
    filesystem executor 落盘：断言父/子 workspace 各自出现
    agent-note.txt，且服务端确实收到了带工具目录的 chat 请求。
    """
    calls: list[dict[str, Any]] = []

    # 意图分类走 ainvoke（payload 无 tools 键）并期望 JSON 分析结果；
    # agent loop 走 achat（带 tools）— 两轮脚本必须分开。
    intent_json = json.dumps({
        "artifact_required": True,
        "requested_outcomes": ["USER_VISIBLE_OUTPUT", "FILE_MUTATION"],
        "domain": "文件",
        "action": "write",
        "target": "agent-note.txt",
        "entities": [],
        "resolved_request": "write the file agent-note.txt in the workspace",
        "conversation_relation": "new_request",
        "delivery_requirements": [{
            "id": "d1", "kind": "file", "quantity": 1,
            "target": "agent-note.txt", "acceptance_criteria": [],
        }],
        "prohibited_effects": [],
        "acceptance_criteria": [],
        "write_targets": ["."],
        "external_effects": [],
        "search_query": "",
        "search_timeliness": "any",
        "context_needs": {"memory": False, "repository": False},
        "memory_action": "none",
        "freshness_required": False,
        "source_grounding_required": False,
    })

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 — http.server API
            body = json.loads(
                self.rfile.read(int(self.headers["Content-Length"]))
            )
            calls.append(body)
            messages = body.get("messages") or []
            message: dict[str, Any]
            if "tools" not in body:
                # 意图/其他文本轮 — 意图提示词给 JSON，其余给短文本。
                system = str(messages[0].get("content", "")) if messages else ""
                is_intent = "目标语义理解" in system
                message = {
                    "role": "assistant",
                    "content": intent_json if is_intent else "done",
                }
            elif any(m.get("role") == "tool" for m in messages):
                message = {"role": "assistant", "content": "done"}
            else:
                message = {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "id": "call-0",
                        "function": {
                            "name": "write_file",
                            "arguments": {
                                "path": "agent-note.txt",
                                "content": "written-by-agent",
                            },
                        },
                    }],
                }
            if body.get("stream"):
                payload = (json.dumps({"message": message, "done": True}) + "\n").encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/x-ndjson")
            else:
                payload = json.dumps({"message": message, "done": True}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args: Any) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    process = _spawn_sidecar(tmp_path, extra_env={
        "TSAGENT_LLM_PROVIDER": "ollama",
        "OLLAMA_BASE_URL": f"http://127.0.0.1:{server.server_port}/v1",
        "OPENAI_API_KEY": "",
        "LLM_API_KEY": "",
        "P2_SECONDARY_API_KEY": "",
    })
    base = {"tenant_id": "tenant-1", "user_id": "user-1", "session_id": "s-tools"}
    try:
        _send(process, {"id": "h", "method": "health", "params": {}})
        assert _read_response(process)["ok"] is True

        # 父 Run — 请求文本携带明确写意图（意图引擎据此授予 FILE_MUTATION，
        # write_file 才进入该 Run 的工具目录）。
        _send(process, {
            "id": "s1", "method": "start_run",
            "params": {**base, "request_id": "p1",
                       "request_text": "write the file agent-note.txt in the workspace",
                       "origin_planet": "mars"},
        })
        parent = _read_response(process)
        assert parent["ok"] is True, parent
        parent_run = parent["result"]["run_id"]
        parent_head = _wait_run_terminal(
            process, base, parent_run, session_id="s-tools")
        assert parent_head["status"].startswith("COMPLETED"), parent_head

        # 委派 → 子 Run 同样经假 provider 的工具调用写文件。
        _send(process, {
            "id": "d1", "method": "delegate_run",
            "params": {**base, "request_id": "d1", "parent_run_id": parent_run,
                       "request_text": "write the file agent-note.txt in the workspace",
                       "target_planet": "mars"},
        })
        delegation = _read_response(process)
        assert delegation["ok"] is True, delegation
        child_task = delegation["result"]["task_id"]
        child_session = f"s-tools:subtask:{child_task}"
        _send(process, {
            "id": "s2", "method": "start_run",
            "params": {**base, "session_id": child_session, "request_id": "s2",
                       "request_text": delegation["result"]["request_text"],
                       "task_id": child_task,
                       "origin_planet": "mars",
                       "workspace_root": delegation["result"]["workspace_root"]},
        })
        child = _read_response(process)
        assert child["ok"] is True, child
        child_head = _wait_run_terminal(
            process, base, child["result"]["run_id"], session_id=child_session)
        assert child_head["status"].startswith("COMPLETED"), child_head

        # 真实工具执行证据 — 文件落盘于 Run workspace（委派子 Run 与父
        # 共享 workspace root，故为同一目录同一文件）。
        notes = sorted(
            p for p in tmp_path.rglob("agent-note.txt") if p.is_file()
        )
        assert len(notes) == 1, notes
        assert notes[0].read_text() == "written-by-agent"
        # 父子各自完成了 工具轮→结果轮：至少两次 chat 携带 tool 结果。
        tool_result_rounds = sum(
            1 for body in calls
            if any(m.get("role") == "tool" for m in (body.get("messages") or []))
        )
        assert tool_result_rounds >= 2, calls
        # 假 provider 确实走了带工具目录的 chat 路径。
        assert any(body.get("tools") for body in calls)

        _send(process, {"id": "bye", "method": "shutdown", "params": {}})
        assert _read_response(process)["ok"] is True
        assert process.wait(timeout=60) == 0
    finally:
        server.shutdown()
        _terminate(process)


def test_graph_execution_head_projection_and_restart_convergence(
    tmp_path: Path,
) -> None:
    """审核修正：节点状态以 Run head 为权威（存储行只是驱动检查点），
    驱动丢失的未完成节点在服务重启时收敛为 INTERRUPTED — 不再
    永久显示运行中。"""
    import asyncio

    import tests.test_ha_r1_harness as harness
    from agent.service.contracts import GraphExecutionLookupRequest

    async def scenario() -> None:
        service, store = harness.service_at(tmp_path)
        try:
            _running_parent(service, store, tmp_path)
            head = store.get_run_head("t", "run-parent", session_id="s")
            assert head is not None
            # 存储行陈旧（SKIPPED）但绑定了真实 run — 投影必须来自 head。
            store.upsert_graph_execution_node(
                "t", "gexec:g-x:r1", "n1",
                graph_id="g-x", session_id="s", position=0,
                task_id="task-1", run_id="run-parent",
                child_session_id="s", status="SKIPPED")
            summary = await service.get_graph_execution(
                GraphExecutionLookupRequest(
                    tenant_id="t", user_id="u", session_id="s",
                    request_id="req-1", execution_id="gexec:g-x:r1"))
            assert summary.steps[0].status == head.run_status
            assert summary.steps[0].status != "SKIPPED"
            # 无 Run 的未完成节点 — 驱动若中断将永不再推进。种子行的
            # updated_at 回拨出 worker 租约宽限期，模拟"驱动早已死亡"；
            # 新鲜行会被当作存活中实例而跳过（多进程共享库合同）。
            store.upsert_graph_execution_node(
                "t", "gexec:g-x:r1", "n2",
                graph_id="g-x", session_id="s", position=1, status="PENDING",
                driver_writer_id="dead-writer")
            stale = "2000-01-01T00:00:00Z"
            with store._write_transaction() as connection:
                connection.execute(
                    "UPDATE graph_execution_nodes SET updated_at = ? "
                    "WHERE tenant_id = 't' AND execution_id = 'gexec:g-x:r1'",
                    (stale,),
                )
        finally:
            await service.close()

        # 重启走 factory（启动收敛在 create_default_agent_service 内）。
        service2 = _workspace_file_service(tmp_path)
        try:
            rows = {
                r.node_id: r
                for r in service2._store.list_graph_execution_nodes(
                    "t", "gexec:g-x:r1")
            }
            assert rows["n2"].status == "INTERRUPTED"
            # n1 绑定了 Run — 收敛只处理无绑定的 open 行，存储行保持种子
            # 值；读路径仍投影 head — 存储行从来不是权威。
            assert rows["n1"].status == "SKIPPED"
            summary2 = await service2.get_graph_execution(
                GraphExecutionLookupRequest(
                    tenant_id="t", user_id="u", session_id="s",
                    request_id="req-2", execution_id="gexec:g-x:r1"))
            assert summary2.steps[0].status == head.run_status
            # n2 无 Run — INTERRUPTED 如实可见。
            assert summary2.steps[1].status == "INTERRUPTED"
        finally:
            await service2.close()

    asyncio.run(scenario())


def test_graph_convergence_preserves_live_instance_nodes(
    tmp_path: Path,
) -> None:
    """多实例共享库：实例 B 打开同库不得把存活实例 A 的 open 节点标
    INTERRUPTED — 收敛只针对有死亡证据的驱动。"""
    import asyncio

    import tests.test_ha_r1_harness as harness

    async def scenario() -> None:
        service_a, store_a = harness.service_at(tmp_path)
        try:
            store_a.upsert_graph_execution_node(
                "t", "gexec:live:r1", "n1",
                graph_id="g-x", session_id="s", position=0,
                status="PENDING", driver_writer_id="writer-a")
            # 实例 B 打开同库 — A 的行刚更新（宽限期内），且即便过期
            # 也要看 writer 租约 — 无条件收敛会误伤。
            service_b = _workspace_file_service(tmp_path)
            try:
                rows = store_a.list_graph_execution_nodes(
                    "t", "gexec:live:r1")
                assert rows[0].status == "PENDING"
            finally:
                await service_b.close()
        finally:
            await service_a.close()

    asyncio.run(scenario())


def test_graph_convergence_lazy_read_after_grace(tmp_path: Path) -> None:
    """宽限期内重启的执行不被启动收敛误杀；宽限期过后正常查询
    惰性重评同一判死谓词 — 得到终态而非永久 PENDING。"""
    import asyncio

    import tests.test_ha_r1_harness as harness
    from agent.service.contracts import GraphExecutionLookupRequest

    async def scenario() -> None:
        service_a, store_a = harness.service_at(tmp_path)
        try:
            store_a.upsert_graph_execution_node(
                "t", "gexec:stale:r1", "n1",
                graph_id="g-x", session_id="s", position=0,
                status="PENDING", driver_writer_id="dead-writer")
        finally:
            await service_a.close()

        # 驱动死后立即重启 — 行在宽限期内，启动收敛如实跳过（这正是
        # 多实例存活保护的另一半：新鲜行也可能是存活驱动刚写的）。
        service_b = _workspace_file_service(tmp_path)
        try:
            rows = service_b._store.list_graph_execution_nodes(
                "t", "gexec:stale:r1")
            assert rows[0].status == "PENDING"
            # 时间推进出宽限期 — 正常查询惰性收敛。
            stale = "2000-01-01T00:00:00Z"
            with service_b._store._write_transaction() as connection:
                connection.execute(
                    "UPDATE graph_execution_nodes SET updated_at = ? "
                    "WHERE tenant_id = 't' AND execution_id = 'gexec:stale:r1'",
                    (stale,),
                )
            summary = await service_b.get_graph_execution(
                GraphExecutionLookupRequest(
                    tenant_id="t", user_id="u", session_id="s",
                    request_id="req-l", execution_id="gexec:stale:r1"))
            assert summary.steps[0].status == "INTERRUPTED"
        finally:
            await service_b.close()

    asyncio.run(scenario())



def test_graph_execute_substitutes_upstream_artifact_references(
    monkeypatch, tmp_path: Path
) -> None:
    """{artifacts:<node_id>} 占位符在委派前替换为上游 Run 的 durable
    workspace 相对产物路径；引用未运行的节点判 FAILED 且其下游 SKIPPED。"""
    import asyncio
    import json as _json
    from types import SimpleNamespace

    import tests.test_ha_r1_harness as harness
    from agent.service.contracts import (
        ArtifactSummary,
        GraphExecutionLookupRequest,
        RunWorkflowGraphRequest,
    )
    from agent.service.service import _topo_task_order

    async def scenario() -> None:
        service = _workspace_file_service(tmp_path)
        store = service._store
        try:
            nodes = [
                {"id": "n1", "type": "task", "request_text": "write script"},
                {"id": "n2", "type": "task",
                 "request_text": "使用 {artifacts:n1} 渲染 MP4"},
                {"id": "n3", "type": "task",
                 "request_text": "see {artifacts:n9}"},
                {"id": "n4", "type": "task", "request_text": "after n3"},
            ]
            edges = [{"from": "n1", "to": "n2"}, {"from": "n3", "to": "n4"}]
            store.upsert_workflow_graph("t", "g-art", definition_json=(
                _json.dumps({"nodes": nodes, "edges": edges})))

            delegated: list[str] = []

            async def fake_delegate(req):
                delegated.append(req.request_text)
                node = req.request_id.rsplit("-", 1)[-1]
                return {
                    "task_id": f"task-{node}",
                    "handoff_id": f"h-{node}",
                    "request_text": req.request_text,
                    "workspace_root": str(tmp_path),
                    "agent_profile": "",
                }

            run_seq = iter(range(1, 10))

            async def fake_start(req):
                return SimpleNamespace(run_id=f"run-{next(run_seq)}")

            artifact = ArtifactSummary(
                artifact_id="a-1", artifact_type="file", digest="d",
                reference="output/script.py", exists=True, verified=True)

            async def fake_get_run(req):
                return SimpleNamespace(
                    status=SimpleNamespace(value="COMPLETED"),
                    artifacts=(artifact,),
                )

            service.delegate_run = fake_delegate
            service.start_run = fake_start
            service.get_run = fake_get_run
            store.update_handoff_target_run = (
                lambda *a, **k: None)

            request = RunWorkflowGraphRequest(
                tenant_id="t", user_id="u", session_id="s",
                request_id="r-1", graph_id="g-art",
                parent_run_id="run-parent")
            task_nodes = [n for n in nodes if n["type"] == "task"]
            order = _topo_task_order(task_nodes, edges)
            await service._graph_execute(
                request, "gexec:g-art:r-1", order, edges, {})

            # n1 真实产物路径注入到 n2 的任务文本；占位符不再保留。
            assert len(delegated) == 2
            assert delegated[1] == "使用 output/script.py 渲染 MP4"
            rows = {
                r.node_id: r
                for r in store.list_graph_execution_nodes(
                    "t", "gexec:g-art:r-1")
            }
            assert rows["n1"].status == "COMPLETED"
            assert rows["n2"].status == "COMPLETED"
            assert rows["n3"].status == "FAILED"
            assert "n9" in rows["n3"].detail
            assert rows["n4"].status == "SKIPPED"
            summary = await service.get_graph_execution(
                GraphExecutionLookupRequest(
                    tenant_id="t", user_id="u", session_id="s",
                    request_id="req-x", execution_id="gexec:g-art:r-1"))
            by_id = {s.node_id: s.status for s in summary.steps}
            assert by_id == {
                "n1": "COMPLETED", "n2": "COMPLETED",
                "n3": "FAILED", "n4": "SKIPPED",
            }
        finally:
            await service.close()

    asyncio.run(scenario())
