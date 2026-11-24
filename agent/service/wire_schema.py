"""Single source of the Desktop wire contract.

Every ``LocalTransportMethod`` declares its params and result type here.
The sidecar dispatcher routes plain methods through this table, and
``scripts/gen_wire_types.py`` generates the Desktop TypeScript wire types
(``apps/desktop/src/service/wire.generated.ts``) from it.  Dataclass
contracts describe themselves; dict-shaped payloads are declared as
TypedDicts below so they have exactly one declaration as well.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, NotRequired, TypedDict

from agent.interruption import CancelRunRequest

from . import contracts as c
from .local_protocol import LocalTransportMethod as M


class EmptyParams(TypedDict):
    pass


class PathParams(TypedDict):
    path: NotRequired[str]


class WebSearchParams(TypedDict):
    query: str
    max_results: NotRequired[int]


class HealthResult(TypedDict):
    status: str
    protocol_version: str
    service: str


class ShutdownResult(TypedDict):
    status: str


class CapabilityEntry(TypedDict):
    capability: str
    tools: list[str]


class WebSearchResult(TypedDict):
    provider: str
    content: str
    sources: list[dict[str, Any]]


class SkillEntry(TypedDict):
    skill_id: str
    name: str
    description: str


class WorkflowEntry(TypedDict):
    workflow_id: str
    description: str
    version: str


class DeleteSessionResult(TypedDict):
    removed: bool
    session_id: str


class RemovedResult(TypedDict):
    removed: bool


class PickFolderResult(TypedDict):
    path: str | None


class PickFilesResult(TypedDict):
    paths: list[str]


class AttachFilesResult(TypedDict):
    context_refs: list[str]


class SessionWorkspaceBinding(TypedDict):
    session_id: str
    workspace_id: str


class ResolvedSessionWorkspace(TypedDict):
    session_id: str
    workspace_id: str
    path: str
    bound: bool


class RevealResult(TypedDict):
    revealed: bool


class DirectoryListing(TypedDict):
    path: str
    parent: str
    dirs: list[str]
    home: NotRequired[str]
    error: NotRequired[str]


class GraphExecutionRef(TypedDict):
    execution_id: str
    graph_id: str
    updated_at: str


class DelegationAgentProfile(TypedDict):
    profile_id: NotRequired[str]
    model_ref: NotRequired[str]
    skill_refs: NotRequired[list[str]]
    permission_policy: NotRequired[dict[str, Any]]


class DelegationResult(TypedDict):
    task_id: str
    handoff_id: str
    session_id: str
    target_planet: str
    workspace_root: str
    agent_profile: DelegationAgentProfile
    request_text: str


class WindowSummaryDict(TypedDict):
    view_id: str
    title: str
    url: str
    loading: bool
    error: bool
    can_back: bool
    can_forward: bool
    controller: str
    run_id: str
    owner: str
    doc_generation: int


class ClosedResult(TypedDict):
    closed: bool


class DisplayTicketResult(TypedDict):
    port: int
    token: str


@dataclass(frozen=True)
class WireMethod:
    """``service`` names the AgentService method a plain method forwards
    to with ``params.from_dict(...)``; empty means the dispatcher owns a
    dedicated handler."""

    params: Any
    result: Any
    service: str = ""


def _plain(params: Any, result: Any, service: str) -> WireMethod:
    return WireMethod(params=params, result=result, service=service)


WIRE_METHODS: dict[M, WireMethod] = {
    M.HEALTH: WireMethod(EmptyParams, HealthResult),
    M.START_RUN: _plain(c.StartRunRequest, c.RunHandle, "start_run"),
    M.GET_RUN: _plain(c.RunLookupRequest, c.RunSnapshot, "get_run"),
    M.RESUME_RUN: _plain(c.ResumeRunRequest, c.RunHandle, "resume_run"),
    M.PAUSE_RUN: _plain(c.PauseRunRequest, c.RunSnapshot, "pause_run"),
    M.CANCEL_RUN: WireMethod(CancelRunRequest, c.RunSnapshot),
    M.LIST_ARTIFACTS: _plain(c.RunLookupRequest, list[c.ArtifactSummary], "list_artifacts"),
    M.READ_ARTIFACT: _plain(c.ReadArtifactRequest, c.ArtifactContent, "read_artifact"),
    M.READ_ARTIFACT_PREVIEW: _plain(c.ReadArtifactRequest, c.ArtifactPreview, "read_artifact_preview"),
    M.LIST_RUNS: _plain(c.ListRunsRequest, list[c.RunSnapshot], "list_runs"),
    M.CREATE_TASK: _plain(c.CreateTaskRequest, c.TaskHandle, "create_task"),
    M.LIST_TASKS: _plain(c.ListTasksRequest, list[c.TaskSnapshot], "list_tasks"),
    M.GET_TASK: _plain(c.TaskLookupRequest, c.TaskSnapshot, "get_task"),
    M.LIST_APPROVALS: _plain(c.ListApprovalsRequest, list[c.ApprovalSummary], "list_approvals"),
    M.DECIDE_APPROVAL: _plain(c.DecideApprovalRequest, c.ApprovalSummary, "decide_approval"),
    M.CREATE_SESSION: _plain(c.CreateSessionRequest, c.SessionSummary, "create_session"),
    M.DELETE_SESSION: _plain(c.DeleteSessionRequest, DeleteSessionResult, "delete_session"),
    M.LIST_SESSIONS: _plain(c.ListSessionsRequest, list[c.SessionSummary], "list_sessions"),
    M.LIST_HANDOFFS: _plain(c.ListHandoffsRequest, list[c.HandoffSummary], "list_handoffs"),
    M.LIST_HISTORY: _plain(c.ListHistoryRequest, list[c.HistoryEntry], "list_history"),
    M.CREATE_SCHEDULE: _plain(c.CreateScheduleRequest, c.ScheduleSummary, "create_schedule"),
    M.LIST_SCHEDULES: _plain(c.ListSchedulesRequest, list[c.ScheduleSummary], "list_schedules"),
    M.UPDATE_SCHEDULE: _plain(c.UpdateScheduleRequest, c.ScheduleSummary, "update_schedule"),
    M.UPSERT_PROFILE: _plain(c.UpsertProfileRequest, c.ProfileSummary, "upsert_profile"),
    M.LIST_PROFILES: _plain(c.ListProfilesRequest, list[c.ProfileSummary], "list_profiles"),
    M.GET_SETTINGS: _plain(c.GetSettingsRequest, c.SettingsSummary, "get_settings"),
    M.UPDATE_SETTINGS: _plain(c.UpdateSettingsRequest, c.SettingsSummary, "update_settings"),
    M.UPSERT_WORKSPACE: _plain(c.UpsertWorkspaceRequest, c.WorkspaceSummary, "upsert_workspace"),
    M.LIST_WORKSPACES: _plain(c.ListWorkspacesRequest, list[c.WorkspaceSummary], "list_workspaces"),
    M.LIST_CAPABILITIES: WireMethod(EmptyParams, list[CapabilityEntry]),
    M.WEB_SEARCH: WireMethod(WebSearchParams, WebSearchResult),
    M.LIST_SKILLS: WireMethod(EmptyParams, list[SkillEntry]),
    M.DELEGATE_RUN: _plain(c.DelegateRunRequest, DelegationResult, "delegate_run"),
    M.SUBSCRIBE_EVENTS: WireMethod(c.EventStreamRequest, list[c.RunEvent]),
    M.LIST_FACTS: _plain(c.ListFactsRequest, list[c.FactRecordSummary], "list_facts"),
    M.FORGET_FACT: _plain(c.ForgetFactRequest, c.FactForgetResult, "forget_fact"),
    M.LIST_PARTICIPANTS: _plain(c.ListParticipantsRequest, list[c.ParticipantSummary], "list_participants"),
    M.SEARCH_ARTIFACTS: _plain(c.SearchArtifactsRequest, list[c.JupiterArtifactSummary], "search_artifacts"),
    M.LIST_WORKFLOWS: WireMethod(EmptyParams, list[WorkflowEntry]),
    M.UPSERT_WORKFLOW_GRAPH: _plain(c.UpsertWorkflowGraphRequest, c.WorkflowGraphSummary, "upsert_workflow_graph"),
    M.LIST_WORKFLOW_GRAPHS: _plain(c.ListWorkflowGraphsRequest, list[c.WorkflowGraphSummary], "list_workflow_graphs"),
    M.RUN_GRAPH: _plain(c.RunWorkflowGraphRequest, list[c.GraphRunStepSummary], "run_graph"),
    M.GET_GRAPH_EXECUTION: _plain(c.GraphExecutionLookupRequest, c.GraphExecutionSummary, "get_graph_execution"),
    M.LIST_GRAPH_EXECUTIONS: _plain(c.ListWorkflowGraphsRequest, list[GraphExecutionRef], "list_graph_executions"),
    M.CREATE_MOTIF: _plain(c.CreateMotifRequest, c.MotifSummary, "create_motif"),
    M.ASSIGN_MOTIF: _plain(c.AssignMotifRequest, DelegationResult, "assign_motif"),
    M.RESOLVE_MOTIF: _plain(c.ResolveMotifRequest, list[c.MotifSummary], "resolve_motif"),
    M.REPORT_MOTIF: _plain(c.ReportMotifRequest, list[c.MotifSummary], "report_motif"),
    M.REJECT_MOTIF: _plain(c.RejectMotifRequest, list[c.MotifSummary], "reject_motif"),
    M.LIST_MOTIFS: _plain(c.ListMotifsRequest, list[c.MotifSummary], "list_motifs"),
    M.RECORD_EVALUATION: _plain(c.RecordEvaluationRequest, c.EvaluationSummary, "record_evaluation"),
    M.LIST_EVALUATIONS: _plain(c.ListEvaluationsRequest, list[c.EvaluationSummary], "list_evaluations"),
    M.LIST_WORKSPACE_FILES: _plain(c.ListWorkspaceFilesRequest, list[c.WorkspaceFileSummary], "list_workspace_files"),
    M.LIST_DIRECTORY: WireMethod(PathParams, DirectoryListing),
    M.PICK_FOLDER: WireMethod(EmptyParams, PickFolderResult),
    M.PICK_FILES: WireMethod(EmptyParams, PickFilesResult),
    M.ATTACH_FILES: _plain(c.AttachFilesRequest, AttachFilesResult, "attach_files"),
    M.REVEAL_PATH: WireMethod(PathParams, RevealResult),
    M.ASSIGN_SESSION_WORKSPACE: _plain(c.AssignSessionWorkspaceRequest, SessionWorkspaceBinding, "assign_session_workspace"),
    M.RESOLVE_SESSION_WORKSPACE: _plain(c.ResolveSessionWorkspaceRequest, ResolvedSessionWorkspace, "resolve_session_workspace"),
    M.DELETE_WORKSPACE: _plain(c.DeleteWorkspaceRequest, RemovedResult, "delete_workspace"),
    M.READ_WORKSPACE_FILE: _plain(c.WorkspaceFileRequest, c.WorkspaceFileContent, "read_workspace_file"),
    M.WRITE_WORKSPACE_FILE: _plain(c.WorkspaceFileRequest, c.WorkspaceFileContent, "write_workspace_file"),
    M.READ_EVENTS: WireMethod(c.EventStreamRequest, list[c.RunEvent]),
    M.LIST_WINDOWS: _plain(c.ListWindowsRequest, list[WindowSummaryDict], "list_windows"),
    M.OPEN_WINDOW: _plain(c.OpenWindowRequest, WindowSummaryDict, "open_window"),
    M.CLOSE_WINDOW: _plain(c.CloseWindowRequest, ClosedResult, "close_window"),
    M.NAVIGATE_WINDOW: _plain(c.NavigateWindowRequest, WindowSummaryDict, "navigate_window"),
    M.DISPLAY_TICKET: WireMethod(c.DisplayTicketRequest, DisplayTicketResult),
    M.SHUTDOWN: WireMethod(EmptyParams, ShutdownResult),
}


__all__ = ["WIRE_METHODS", "WireMethod"]
