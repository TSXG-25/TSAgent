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
