"""Explicit JSONL method dispatch over the public AgentService DTO boundary."""

from __future__ import annotations

import traceback
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, TextIO

from ..local_protocol import (
    LocalProtocolError,
    LocalRpcRequest,
    LocalRpcResponse,
    LocalRpcSuccess,
    LocalTransportMethod,
    PROTOCOL_VERSION,
)


Handler = Callable[[Mapping[str, Any]], Awaitable[Any]]


class SidecarDispatcher:
    """Thin allowlisted adapter; it owns no Runtime or business decisions."""

    def __init__(self, service: Any, *, diagnostics: TextIO) -> None:
        self._service = service
        self._diagnostics = diagnostics
        self._shutdown_requested = False
        self._closed = False
        self._handlers: dict[LocalTransportMethod, Handler] = {
            LocalTransportMethod.HEALTH: self._health,
            LocalTransportMethod.START_RUN: self._start_run,
            LocalTransportMethod.GET_RUN: self._get_run,
            LocalTransportMethod.RESUME_RUN: self._resume_run,
            LocalTransportMethod.PAUSE_RUN: self._pause_run,
            LocalTransportMethod.CANCEL_RUN: self._cancel_run,
            LocalTransportMethod.LIST_ARTIFACTS: self._list_artifacts,
            LocalTransportMethod.READ_ARTIFACT: self._read_artifact,
            LocalTransportMethod.READ_ARTIFACT_PREVIEW: self._read_artifact_preview,
            LocalTransportMethod.LIST_RUNS: self._list_runs,
            LocalTransportMethod.CREATE_TASK: self._create_task,
            LocalTransportMethod.LIST_TASKS: self._list_tasks,
            LocalTransportMethod.GET_TASK: self._get_task,
            LocalTransportMethod.LIST_APPROVALS: self._list_approvals,
            LocalTransportMethod.DECIDE_APPROVAL: self._decide_approval,
            LocalTransportMethod.CREATE_SESSION: self._create_session,
            LocalTransportMethod.DELETE_SESSION: self._delete_session,
            LocalTransportMethod.LIST_SESSIONS: self._list_sessions,
            LocalTransportMethod.LIST_HANDOFFS: self._list_handoffs,
            LocalTransportMethod.LIST_HISTORY: self._list_history,
            LocalTransportMethod.CREATE_SCHEDULE: self._create_schedule,
            LocalTransportMethod.LIST_SCHEDULES: self._list_schedules,
            LocalTransportMethod.UPDATE_SCHEDULE: self._update_schedule,
            LocalTransportMethod.UPSERT_PROFILE: self._upsert_profile,
            LocalTransportMethod.LIST_PROFILES: self._list_profiles,
            LocalTransportMethod.GET_SETTINGS: self._get_settings,
            LocalTransportMethod.UPDATE_SETTINGS: self._update_settings,
            LocalTransportMethod.UPSERT_WORKSPACE: self._upsert_workspace,
            LocalTransportMethod.LIST_WORKSPACES: self._list_workspaces,
            LocalTransportMethod.LIST_CAPABILITIES: self._list_capabilities,
            LocalTransportMethod.WEB_SEARCH: self._web_search,
            LocalTransportMethod.LIST_SKILLS: self._list_skills,
            LocalTransportMethod.DELEGATE_RUN: self._delegate_run,
            LocalTransportMethod.LIST_FACTS: self._list_facts,
            LocalTransportMethod.FORGET_FACT: self._forget_fact,
            LocalTransportMethod.LIST_PARTICIPANTS: self._list_participants,
            LocalTransportMethod.SEARCH_ARTIFACTS: self._search_artifacts,
            LocalTransportMethod.LIST_WORKFLOWS: self._list_workflows,
            LocalTransportMethod.UPSERT_WORKFLOW_GRAPH: self._upsert_workflow_graph,
            LocalTransportMethod.LIST_WORKFLOW_GRAPHS: self._list_workflow_graphs,
            LocalTransportMethod.RUN_GRAPH: self._run_graph,
            LocalTransportMethod.GET_GRAPH_EXECUTION: self._get_graph_execution,
            LocalTransportMethod.LIST_GRAPH_EXECUTIONS: self._list_graph_executions,
            LocalTransportMethod.CREATE_MOTIF: self._create_motif,
            LocalTransportMethod.ASSIGN_MOTIF: self._assign_motif,
            LocalTransportMethod.RESOLVE_MOTIF: self._resolve_motif,
            LocalTransportMethod.REPORT_MOTIF: self._report_motif,
            LocalTransportMethod.REJECT_MOTIF: self._reject_motif,
            LocalTransportMethod.LIST_MOTIFS: self._list_motifs,
            LocalTransportMethod.RECORD_EVALUATION: self._record_evaluation,
            LocalTransportMethod.LIST_EVALUATIONS: self._list_evaluations,
            LocalTransportMethod.LIST_WORKSPACE_FILES: self._list_workspace_files,
            LocalTransportMethod.LIST_DIRECTORY: self._list_directory,
            LocalTransportMethod.PICK_FOLDER: self._pick_folder,
            LocalTransportMethod.PICK_FILES: self._pick_files,
            LocalTransportMethod.ATTACH_FILES: self._attach_files,
            LocalTransportMethod.REVEAL_PATH: self._reveal_path,
            LocalTransportMethod.ASSIGN_SESSION_WORKSPACE: self._assign_session_workspace,
            LocalTransportMethod.RESOLVE_SESSION_WORKSPACE: self._resolve_session_workspace,
            LocalTransportMethod.DELETE_WORKSPACE: self._delete_workspace,
            LocalTransportMethod.READ_WORKSPACE_FILE: self._read_workspace_file,
            LocalTransportMethod.WRITE_WORKSPACE_FILE: self._write_workspace_file,
            LocalTransportMethod.READ_EVENTS: self._read_events,
            LocalTransportMethod.SUBSCRIBE_EVENTS: self._subscribe_events,
            LocalTransportMethod.SHUTDOWN: self._shutdown,
        }

    @property
    def shutdown_requested(self) -> bool:
        return self._shutdown_requested

    async def dispatch(self, request: LocalRpcRequest) -> LocalRpcResponse:
        if self._closed:
            from .serializer import internal_error_response

            return internal_error_response(request.id)
        handler = self._handlers.get(request.method)
        if handler is None:  # pragma: no cover - LocalRpcRequest validates the allowlist
            from .serializer import protocol_error_response

            return protocol_error_response(
                request.id,
                LocalProtocolError(
                    "UNSUPPORTED_OPERATION",
                    f"unsupported local method: {request.method.value}",
                ),
            )
        try:
            result = await handler(request.params)
            if request.method is LocalTransportMethod.SHUTDOWN:
                self._shutdown_requested = True
            return LocalRpcSuccess(request.id, self._to_wire(result))
        except LocalProtocolError as error:
            from .serializer import protocol_error_response

            return protocol_error_response(request.id, error)
        except Exception as error:
            from ..errors import AgentServiceError
            from .serializer import internal_error_response

            if isinstance(error, AgentServiceError):
                from .serializer import service_error_response

                return service_error_response(request, error)
            traceback.print_exc(file=self._diagnostics)
            return internal_error_response(request.id)

    @staticmethod
    def _to_wire(value: Any) -> Any:
        if hasattr(value, "to_dict") and callable(value.to_dict):
            return SidecarDispatcher._to_wire(value.to_dict())
        if isinstance(value, Mapping):
            return {
                str(key): SidecarDispatcher._to_wire(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [SidecarDispatcher._to_wire(item) for item in value]
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        raise LocalProtocolError(
            "INTERNAL_MODEL_LEAK",
            "service result contains unsupported object",
        )

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._service.close()

    async def _health(self, params: Mapping[str, Any]) -> dict[str, Any]:
        del params
        return {
            "status": "ok",
            "protocol_version": PROTOCOL_VERSION,
            "service": "ready",
        }

    async def _start_run(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import StartRunRequest

        request = self._from_dto(StartRunRequest.from_dict, params)
        return await self._service.start_run(request)

    async def _get_run(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import RunLookupRequest

        request = self._from_dto(RunLookupRequest.from_dict, params)
        return await self._service.get_run(request)

    async def _resume_run(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import ResumeRunRequest

        request = self._from_dto(ResumeRunRequest.from_dict, params)
        return await self._service.resume_run(request)

    async def _cancel_run(self, params: Mapping[str, Any]) -> Any:
        from agent.interruption import CancelRunRequest

        # USER_CANCEL is the only public cancellation reason.  The desktop
        # wire request omits it intentionally; the adapter supplies the
        # frozen public default before DTO construction.
        cancel_params = dict(params)
        cancel_params.setdefault("reason", "USER_CANCEL")
        request = self._from_dto(CancelRunRequest.from_dict, cancel_params)
        return await self._service.cancel_run(request)

    async def _list_runs(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import ListRunsRequest

        request = self._from_dto(ListRunsRequest.from_dict, params)
        return await self._service.list_runs(request)

    async def _create_task(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import CreateTaskRequest

        request = self._from_dto(CreateTaskRequest.from_dict, params)
        return await self._service.create_task(request)

    async def _list_tasks(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import ListTasksRequest

        request = self._from_dto(ListTasksRequest.from_dict, params)
        return await self._service.list_tasks(request)

    async def _get_task(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import TaskLookupRequest

        request = self._from_dto(TaskLookupRequest.from_dict, params)
        return await self._service.get_task(request)

    async def _list_approvals(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import ListApprovalsRequest

        request = self._from_dto(ListApprovalsRequest.from_dict, params)
        return await self._service.list_approvals(request)

    async def _decide_approval(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import DecideApprovalRequest

        request = self._from_dto(DecideApprovalRequest.from_dict, params)
        return await self._service.decide_approval(request)

    async def _create_session(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import CreateSessionRequest

        request = self._from_dto(CreateSessionRequest.from_dict, params)
        return await self._service.create_session(request)

    async def _delete_session(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import DeleteSessionRequest

        request = self._from_dto(DeleteSessionRequest.from_dict, params)
        return await self._service.delete_session(request)

    async def _list_sessions(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import ListSessionsRequest

        request = self._from_dto(ListSessionsRequest.from_dict, params)
        return await self._service.list_sessions(request)

    async def _list_handoffs(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import ListHandoffsRequest

        request = self._from_dto(ListHandoffsRequest.from_dict, params)
        return await self._service.list_handoffs(request)

    async def _list_history(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import ListHistoryRequest

        request = self._from_dto(ListHistoryRequest.from_dict, params)
        return await self._service.list_history(request)

    async def _create_schedule(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import CreateScheduleRequest

        request = self._from_dto(CreateScheduleRequest.from_dict, params)
        return await self._service.create_schedule(request)

    async def _list_schedules(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import ListSchedulesRequest

        request = self._from_dto(ListSchedulesRequest.from_dict, params)
        return await self._service.list_schedules(request)

    async def _update_schedule(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import UpdateScheduleRequest

        request = self._from_dto(UpdateScheduleRequest.from_dict, params)
        return await self._service.update_schedule(request)

    async def _upsert_profile(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import UpsertProfileRequest

        request = self._from_dto(UpsertProfileRequest.from_dict, params)
        return await self._service.upsert_profile(request)

    async def _list_profiles(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import ListProfilesRequest

        request = self._from_dto(ListProfilesRequest.from_dict, params)
        return await self._service.list_profiles(request)

    async def _get_settings(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import GetSettingsRequest

        request = self._from_dto(GetSettingsRequest.from_dict, params)
        return await self._service.get_settings(request)

    async def _update_settings(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import UpdateSettingsRequest

        request = self._from_dto(UpdateSettingsRequest.from_dict, params)
        return await self._service.update_settings(request)

    async def _upsert_workspace(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import UpsertWorkspaceRequest

        request = self._from_dto(UpsertWorkspaceRequest.from_dict, params)
        return await self._service.upsert_workspace(request)

    async def _list_workspaces(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import ListWorkspacesRequest

        request = self._from_dto(ListWorkspacesRequest.from_dict, params)
        return await self._service.list_workspaces(request)

    async def _list_capabilities(self, params: Mapping[str, Any]) -> Any:
        """Expose the runtime capability registry to the desktop UI — scene
        'not installed' badges reflect the real registry, not UI guesses."""
        from agent.registry.capability_registry import registry

        return [
            {"capability": name, "tools": registry.get_tools_for_capability(name)}
            for name in registry.get_all_capabilities()
        ]

    async def _web_search(self, params: Mapping[str, Any]) -> Any:
        """Mercury search — real provider chain (duckduckgo -> deepseek);
        surfaces provider errors instead of fabricating results."""
        from agent.search.providers import run_search

        query = str(params.get("query", "")).strip()
        if not query:
            raise ValueError("query must be a non-empty string")
        outcome = await run_search(query, max_results=int(params.get("max_results", 8)))
        return {
            "provider": outcome.provider,
            "content": outcome.content,
            "sources": outcome.sources,
        }

    async def _pause_run(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import PauseRunRequest

        request = self._from_dto(PauseRunRequest.from_dict, params)
        return await self._service.pause_run(request)

    async def _delegate_run(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import DelegateRunRequest

        request = self._from_dto(DelegateRunRequest.from_dict, params)
        return await self._service.delegate_run(request)

    async def _list_skills(self, params: Mapping[str, Any]) -> Any:
        """Real skill index (SKILL.md frontmatter) — Uranus binds profiles to
        actual skill ids, not made-up names."""
        from agent.skills import skills_index

        return [
            {"skill_id": sid, "name": s.name, "description": s.description}
            for sid, s in skills_index().items()
        ]

    async def _list_facts(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import ListFactsRequest

        request = self._from_dto(ListFactsRequest.from_dict, params)
        return await self._service.list_facts(request)

    async def _forget_fact(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import ForgetFactRequest

        request = self._from_dto(ForgetFactRequest.from_dict, params)
        return await self._service.forget_fact(request)

    async def _list_participants(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import ListParticipantsRequest

        request = self._from_dto(ListParticipantsRequest.from_dict, params)
        return await self._service.list_participants(request)

    async def _search_artifacts(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import SearchArtifactsRequest

        request = self._from_dto(SearchArtifactsRequest.from_dict, params)
        return await self._service.search_artifacts(request)

    async def _assign_session_workspace(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import AssignSessionWorkspaceRequest

        request = self._from_dto(AssignSessionWorkspaceRequest.from_dict, params)
        return await self._service.assign_session_workspace(request)

    async def _resolve_session_workspace(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import ResolveSessionWorkspaceRequest

        request = self._from_dto(ResolveSessionWorkspaceRequest.from_dict, params)
        return await self._service.resolve_session_workspace(request)

    async def _delete_workspace(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import DeleteWorkspaceRequest

        request = self._from_dto(DeleteWorkspaceRequest.from_dict, params)
        return await self._service.delete_workspace(request)

    async def _pick_folder(self, params: Mapping[str, Any]) -> Any:
        return await self._service.pick_folder(type("Req", (), {})())

    async def _pick_files(self, params: Mapping[str, Any]) -> Any:
        return await self._service.pick_files(type("Req", (), {})())

    async def _attach_files(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import AttachFilesRequest

        request = self._from_dto(AttachFilesRequest.from_dict, params)
        return await self._service.attach_files(request)

    async def _reveal_path(self, params: Mapping[str, Any]) -> Any:
        return await self._service.reveal_path(type("Req", (), {"path": params.get("path", "")})())

    async def _list_directory(self, params: Mapping[str, Any]) -> Any:
        return await self._service.list_directory(type("Req", (), {"path": params.get("path", "")})())

    async def _list_workspace_files(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import ListWorkspaceFilesRequest

        request = self._from_dto(ListWorkspaceFilesRequest.from_dict, params)
        return await self._service.list_workspace_files(request)

    async def _read_workspace_file(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import WorkspaceFileRequest

        request = self._from_dto(WorkspaceFileRequest.from_dict, params)
        return await self._service.read_workspace_file(request)

    async def _write_workspace_file(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import WorkspaceFileRequest

        request = self._from_dto(WorkspaceFileRequest.from_dict, params)
        return await self._service.write_workspace_file(request)

    async def _record_evaluation(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import RecordEvaluationRequest

        request = self._from_dto(RecordEvaluationRequest.from_dict, params)
        return await self._service.record_evaluation(request)

    async def _list_evaluations(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import ListEvaluationsRequest

        request = self._from_dto(ListEvaluationsRequest.from_dict, params)
        return await self._service.list_evaluations(request)

    async def _list_workflows(self, params: Mapping[str, Any]) -> Any:
        """Real workflow definitions — Uranus canvas binds nodes to actual
        registered workflows, not made-up names."""
        import workflows  # importing the package registers each Workflow
        from agent.registry.workflow_registry import workflow_registry

        return [
            {
                "workflow_id": name,
                "description": getattr(wf, "description", "") or "",
                "version": getattr(wf, "version", "") or "",
            }
            for name in workflow_registry.list()
            for wf in [workflow_registry.get(name)]
        ]

    async def _upsert_workflow_graph(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import UpsertWorkflowGraphRequest

        request = self._from_dto(UpsertWorkflowGraphRequest.from_dict, params)
        return await self._service.upsert_workflow_graph(request)

    async def _create_motif(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import CreateMotifRequest

        request = self._from_dto(CreateMotifRequest.from_dict, params)
        return await self._service.create_motif(request)

    async def _assign_motif(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import AssignMotifRequest

        request = self._from_dto(AssignMotifRequest.from_dict, params)
        return await self._service.assign_motif(request)

    async def _resolve_motif(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import ResolveMotifRequest

        request = self._from_dto(ResolveMotifRequest.from_dict, params)
        return await self._service.resolve_motif(request)

    async def _report_motif(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import ReportMotifRequest

        request = self._from_dto(ReportMotifRequest.from_dict, params)
        return await self._service.report_motif(request)

    async def _reject_motif(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import RejectMotifRequest

        request = self._from_dto(RejectMotifRequest.from_dict, params)
        return await self._service.reject_motif(request)

    async def _list_motifs(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import ListMotifsRequest

        request = self._from_dto(ListMotifsRequest.from_dict, params)
        return await self._service.list_motifs(request)

    async def _run_graph(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import RunWorkflowGraphRequest

        request = self._from_dto(RunWorkflowGraphRequest.from_dict, params)
        return await self._service.run_graph(request)

    async def _get_graph_execution(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import GraphExecutionLookupRequest

        request = self._from_dto(GraphExecutionLookupRequest.from_dict, params)
        return await self._service.get_graph_execution(request)

    async def _list_graph_executions(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import ListWorkflowGraphsRequest

        request = self._from_dto(ListWorkflowGraphsRequest.from_dict, params)
        return await self._service.list_graph_executions(request)

    async def _list_workflow_graphs(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import ListWorkflowGraphsRequest

        request = self._from_dto(ListWorkflowGraphsRequest.from_dict, params)
        return await self._service.list_workflow_graphs(request)

    async def _list_artifacts(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import RunLookupRequest

        request = self._from_dto(RunLookupRequest.from_dict, params)
        return await self._service.list_artifacts(request)

    async def _read_artifact(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import ReadArtifactRequest

        request = self._from_dto(ReadArtifactRequest.from_dict, params)
        return await self._service.read_artifact(request)

    async def _read_artifact_preview(self, params: Mapping[str, Any]) -> Any:
        from ..contracts import ReadArtifactRequest

        request = self._from_dto(ReadArtifactRequest.from_dict, params)
        return await self._service.read_artifact_preview(request)

    async def _read_events(self, params: Mapping[str, Any]) -> list[Any]:
        # A poll must return promptly when a live Run has no new event.  The
        # durable event repository remains the source of truth; this timeout
        # is only transport framing.
        return await self._stream_events_bounded(
            params, first_timeout=0.05, gap_timeout=0.01
        )

    async def _subscribe_events(self, params: Mapping[str, Any]) -> list[Any]:
        """Long-poll event push — holds the request up to ~8s until new
        durable events arrive, then returns them.  Same request shape and
        semantics as read_events; the client chains calls for live runs."""
        return await self._stream_events_bounded(
            params, first_timeout=8.0, gap_timeout=0.25
        )

    async def _stream_events_bounded(
        self,
        params: Mapping[str, Any],
        *,
        first_timeout: float,
        gap_timeout: float,
    ) -> list[Any]:
        import asyncio
        from ..contracts import EventStreamRequest

        request = self._from_dto(EventStreamRequest.from_dict, params)
        stream = self._service.stream_events(request)
        iterator = stream.__aiter__()
        events: list[Any] = []
        limit = request.limit or 100
        try:
            while len(events) < limit:
                try:
                    event = await asyncio.wait_for(
                        anext(iterator),
                        timeout=first_timeout if not events else gap_timeout,
                    )
                except asyncio.TimeoutError:
                    break
                except StopAsyncIteration:
                    break
                events.append(event)
                if event.is_terminal:
                    break
        finally:
            close_stream = getattr(iterator, "aclose", None)
            if close_stream is not None:
                await close_stream()
        return events

    async def _shutdown(self, params: Mapping[str, Any]) -> dict[str, str]:
        del params
        return {"status": "shutting_down"}

    @staticmethod
    def _from_dto(factory: Callable[[Mapping[str, Any]], Any], params: Mapping[str, Any]) -> Any:
        try:
            return factory(params)
        except (TypeError, ValueError, KeyError) as error:
            raise LocalProtocolError(
                "INVALID_REQUEST",
                "request params do not match the AgentService contract",
            ) from error


__all__ = ["SidecarDispatcher"]
