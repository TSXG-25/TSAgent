"""run_workflow — the loop's Workflow tool.

Moves the durable activation logic out of the Planner: input-file checks,
the durable Run index, and RunResumeCoordinator.execute_or_resume are
unchanged; the only difference is the model now selects the workflow and
its bindings directly instead of a selector LLM call.
"""
from __future__ import annotations

from typing import Any, Mapping

from agent.state import AgentState
from agent.workflow.projection import WorkflowDefinitionProjection


def workflow_projections() -> list[dict[str, Any]]:
    """Available workflows for the loop toolset."""
    from agent.bootstrap import load_all_workflows
    from agent.registry.workflow_registry import workflow_registry
    from agent.tool_identity import registry_tool_name
    from agent.registry.tool_registry import registry as tool_registry

    load_all_workflows()
    projections = []
    for name in workflow_registry.list():
        workflow = workflow_registry.get(name)
        if workflow is None:
            continue
        projection = WorkflowDefinitionProjection.from_workflow(workflow)
        if not all(
            tool_registry.get(registry_tool_name(capability)) is not None
            for capability in projection.required_capabilities
        ):
            continue
        projections.append({
            "id": projection.id,
            "description": projection.description,
            "required_bindings": list(projection.required_bindings),
            "required_input_paths": list(projection.required_input_paths),
        })
    return projections


def missing_required_input_files(
    projection: WorkflowDefinitionProjection,
    bindings: Mapping[str, Any],
    workspace: Any,
) -> list[str]:
    """Return ``name=path`` entries for declared input files that do not exist."""
    missing: list[str] = []
    if workspace is None:
        return missing
    for name in projection.required_input_paths:
        bound = str(bindings[name])
        resolved = None
        try:
            resolved = workspace.resolve_path(bound)
        except (OSError, ValueError):
            resolved = None
        if resolved is None or not resolved.is_file():
            missing.append(f"{name}={bound}")
    return missing


def record_workflow_execution_evidence(
    state: AgentState,
    *,
    workflow: Any,
    projection: WorkflowDefinitionProjection,
    bindings: Mapping[str, Any],
    workspace: Any,
) -> None:
    """Project a completed Workflow's verified file effects into Run evidence."""
    if workspace is None:
        return
    from agent.executor.verifier import snapshot_outputs

    producers = {
        str(argument.artifact): stage.id
        for stage in getattr(workflow, "stages", ()) or ()
        for argument in getattr(stage, "arguments", ()) or ()
        if getattr(argument, "artifact", None)
    }
    input_names = set(projection.required_input_paths)
    evidence = [
        dict(item)
        for item in state.get("execution_evidence") or ()
        if isinstance(item, Mapping)
    ]
    file_evidence = [
        dict(item)
        for item in state.get("file_artifact_evidence") or ()
        if isinstance(item, Mapping)
    ]
    recorded = {
        (str(item.get("outcome", "")), str(item.get("target", "")))
        for item in evidence
    }
    recorded_paths = {str(item.get("path", "")) for item in file_evidence}
    for name, bound in bindings.items():
        try:
            resolved = workspace.resolve_path(str(bound))
        except (OSError, ValueError):
            continue
        if not resolved.is_file():
            continue
        relative = workspace.relative_path(resolved)
        outcome = (
            "FILE_READ" if name in input_names else "FILE_MUTATION"
        )
        source = (
            "WorkflowExecutor:filesystem.read"
            if name in input_names
            else "WorkflowExecutor:filesystem.write"
        )
        identity = (outcome, relative)
        if identity not in recorded:
            evidence.append({
                "outcome": outcome,
                "status": "VERIFIED",
                "source": source,
                "target": relative,
            })
            recorded.add(identity)
        if outcome == "FILE_MUTATION" and relative not in recorded_paths:
            for path, digest in snapshot_outputs([relative], workspace).items():
                file_evidence.append({
                    "path": path,
                    "digest": digest,
                    "producer_task_id": producers.get(
                        name, getattr(workflow, "id", "")
                    ),
                })
                recorded_paths.add(path)
    state["execution_evidence"] = evidence
    state["file_artifact_evidence"] = file_evidence


async def run_workflow_tool(
    state: AgentState,
    *,
    run_context,
    user_id: str,
    user_input: str,
    workflow_id: str,
    bindings: Mapping[str, Any],
) -> tuple[bool, str]:
    """Activate/resume one Workflow through the durable coordinator.

    Returns (ok, tool_message).  Every failure mode is a tool error the
    model can react to; nothing here raises into the Runtime.
    """
    from agent.bootstrap import load_all_workflows
    from agent.checkpoint import InMemoryCheckpointStore
    from agent.registry.workflow_registry import workflow_registry
    from agent.run_resume import (
        InMemoryRunResumeStore,
        RunResumeCoordinator,
        RunResumeRequest,
    )
    from agent.run_resume.execution_context import (
        ephemeral_run_id,
        new_single_workflow_index,
        workflow_execution_context,
    )
    from agent.workflow import Workflow

    load_all_workflows()
    wf_obj = workflow_registry.get(str(workflow_id))
    if wf_obj is None:
        return False, (
            f"WORKFLOW_UNKNOWN: '{workflow_id}' 不存在。可用: "
            + ", ".join(workflow_registry.list())
        )
    if not isinstance(wf_obj, Workflow):
        return False, f"WORKFLOW_UNKNOWN: '{workflow_id}' 不是 canonical Workflow"
    if state.get("workflow"):
        return False, (
            f"WORKFLOW_ALREADY_ACTIVE: 本 Run 已执行 Workflow "
            f"'{state['workflow']}'，一次只能运行一个"
        )
    try:
        projection = WorkflowDefinitionProjection.from_workflow(wf_obj)
    except ValueError as exc:
        return False, f"WORKFLOW_CONFIG_INVALID: {exc}"

    merged = dict(projection.defaults)
    merged.update(dict(bindings))
    missing_bindings = [
        name for name in projection.required_bindings
        if not str(merged.get(name, "") or "").strip()
    ]
    if missing_bindings:
        return False, (
            "WORKFLOW_BINDINGS_REQUIRED: 缺少必需参数 "
            + ", ".join(missing_bindings)
            + "；请从用户请求中提取，或向用户询问"
        )
    workspace = getattr(run_context, "workspace", None)
    missing_inputs = missing_required_input_files(
        projection, merged, workspace
    )
    if missing_inputs:
        return False, (
            "WORKFLOW_INPUT_MISSING: 输入文件在工作区不存在: "
            + ", ".join(missing_inputs)
        )

    run_id = (
        run_context.run_id if run_context is not None else ephemeral_run_id()
    )
    session_id = (
        run_context.session_id if run_context is not None else "default-session"
    )
    user_scope = (
        run_context.session.learning_namespace
        if run_context is not None
        else user_id
    )
    durable_view = (
        run_context.durable_store_view if run_context is not None else None
    )
    proposed_index = new_single_workflow_index(
        wf_obj,
        run_id=run_id,
        session_id=session_id,
        conversation_id=session_id,
        user_scope=user_scope,
        target_summary=user_input,
        input_bindings=merged,
    )
    if durable_view is not None:
        index = durable_view.get_run_index()
        if index is None:
            index = durable_view.bootstrap_run_index(proposed_index)
    else:
        run_store = InMemoryRunResumeStore()
        run_store.save(proposed_index)
        index = proposed_index
    if index.workflow_sequence != (wf_obj.id,):
        return False, "WORKFLOW_ALREADY_ACTIVE: 持久化 Run 记录了不同的 Workflow"
    summary = index.workflow(wf_obj.id)
    if summary is None:
        return False, "WORKFLOW_ALREADY_ACTIVE: 持久化 Run 缺少该 Workflow"
    if summary.workflow_version != wf_obj.version:
        return False, (
            "WORKFLOW_VERSION_INCOMPATIBLE: 当前 Workflow 版本与持久化 Run 不一致"
        )

    workflows = {wf_obj.id: wf_obj}
    if durable_view is not None:
        coordinator = RunResumeCoordinator(
            runtime_store_view=durable_view,
            workflows=workflows,
        )
    else:
        coordinator = RunResumeCoordinator(
            run_store=run_store,
            checkpoint_store=InMemoryCheckpointStore(),
            workflows=workflows,
        )
    resume_request = RunResumeRequest(
        requested_run_id=run_id,
        candidate_run_ids=(run_id,),
        current_workflow_versions=((wf_obj.id, wf_obj.version),),
    )
    attempt_id = (
        run_context.request_id if run_context is not None else ephemeral_run_id()
    )
    try:
        execution = await coordinator.execute_or_resume(
            run_id,
            lambda selected: workflow_execution_context(
                selected,
                index.workflow(selected.id),
                run_context=run_context,
            ),
            attempt_id=attempt_id,
            request=resume_request,
        )
    except Exception as exc:  # coordinator boundary: report, don't crash the Run
        return False, f"WORKFLOW_EXECUTION_FAILED: {type(exc).__name__}: {exc}"

    state["workflow"] = wf_obj.id
    result = execution.execution_result
    if (
        execution.decision.disposition.value == "ALLOW"
        and result is not None
        and result.success
        and result.outputs.get("_summary")
        and bool((result.metadata or {}).get("terminal_outputs_verified", False))
    ):
        record_workflow_execution_evidence(
            state,
            workflow=wf_obj,
            projection=projection,
            bindings=merged,
            workspace=workspace,
        )
        return True, str(result.outputs["_summary"])
    reason = (
        execution.decision.reason_code.value
        if result is None
        else (result.error or "Workflow terminal outputs 未通过校验")
    )
    return False, f"WORKFLOW_EXECUTION_INCOMPLETE: {str(reason)[:240]}"
