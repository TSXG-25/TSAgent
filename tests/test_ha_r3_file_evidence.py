"""Actual tool effects are the only source for file artifact publication."""
import asyncio
import hashlib
import shlex
import sys
import pytest
from types import SimpleNamespace

from agent.event_bus import EventBus
from agent.services.workspace_service import WorkspaceService
from agent.task import Task, ExecutionPlan, ExecutionStep
from agent.workflow import ExecutionContext
from agent.executor.executors.tool import ToolExecutor
from agent.orchestrator.executor import ExecutionStage
from agent.orchestrator.finalizer import Finalizer
from agent.effect_truth import record_execution_evidence
from agent.runtime import _verified_file_artifacts
from agent.goal import DeliveryRequirement, GoalState


def execute(tmp_path, monkeypatch, task, steps):
    from agent import sandbox
    monkeypatch.setenv('TSAGENT_ALLOW_LOCAL_EXECUTION', '1')
    monkeypatch.setattr(sandbox, '_check_docker', lambda: False)
    ws = WorkspaceService.scoped(tmp_path, event_bus=EventBus(), build_index=False)
    plan = ExecutionPlan(task=task, steps=steps)
    context = ExecutionContext(user_input=task.goal)
    context.set_var('workspace', ws)
    context.set_var('execution_plan', plan)
    result = asyncio.run(ToolExecutor().execute(task, context))
    return ws, plan, result


def shell(source):
    command = shlex.quote(sys.executable) + ' -c ' + shlex.quote(source)
    return ExecutionStep(tool='shell', args={'cmd': command}, outputs=['output'])


def finalize(ws, plan, result):
    task = plan.task.to_dict()
    ExecutionStage._apply_result(task, plan, result)
    state = dict(plan=[task], artifact_required=True,
                 requested_outcomes=['FILE_MUTATION', 'USER_VISIBLE_OUTPUT'],
                 goal_state=GoalState(
                     objective='交付文件',
                     delivery_requirements=(DeliveryRequirement(
                         'deliverables', 'file',
                         len(task.get('output_paths') or ()) or 1,
                     ),),
                 ).to_dict())
    record_execution_evidence(state, plan, result)
    if not result.success:
        state['runtime_terminal_status'] = 'FAILED_TERMINAL'
        state['runtime_failure_code'] = result.metadata['error_code']
    finalizer = Finalizer(SimpleNamespace(run_context=SimpleNamespace(workspace=ws),
        session_context=SimpleNamespace(memory_view=None),
        _timings={}))
    answer = asyncio.run(finalizer.run(state, '交付文件', 'audit'))
    return state, answer


def test_actual_write_path_not_task_target_is_delivered(tmp_path, monkeypatch):
    task = Task(id='write', verb='write', target='logical-name.txt', target_type='file')
    ws, plan, result = execute(tmp_path, monkeypatch, task, [ExecutionStep(
        tool='filesystem.write', args={'path': str(tmp_path/'actual.txt'), 'content':'ACTUAL', 'exact': True}, outputs=['output'])])
    try:
        assert result.success
        assert result.metadata['verified_files'] == [{'path': 'actual.txt', 'digest': hashlib.sha256(b'ACTUAL').hexdigest()}]
        state, answer = finalize(ws, plan, result)
        assert str(tmp_path/'actual.txt') in answer
        assert 'logical-name.txt' not in answer
        assert _verified_file_artifacts(state)[0]['reference'] == 'actual.txt'
    finally:
        ws.close()


def test_real_command_directory_outputs_become_individual_artifacts(tmp_path, monkeypatch):
    task = Task(id='generate', verb='execute', target='generator', target_type='text', output_paths=['dist/'])
    ws, plan, result = execute(tmp_path, monkeypatch, task, [shell(
        "from pathlib import Path; Path('dist').mkdir(); Path('dist/a.csv').write_text('a,b\\n'); Path('dist/b.csv').write_text('c,d\\n'); print('BUILT')")])
    try:
        assert result.success, result.error
        state, answer = finalize(ws, plan, result)
        assert {a['reference'] for a in _verified_file_artifacts(state)} == {'dist/a.csv', 'dist/b.csv'}
        assert str(tmp_path/'dist/a.csv') in answer and str(tmp_path/'dist/b.csv') in answer
        assert 'BUILT' in answer
    finally:
        ws.close()


def test_stale_existing_file_is_not_a_command_effect(tmp_path, monkeypatch):
    (tmp_path/'existing.csv').write_text('existing')
    task = Task(id='noop', verb='execute', target='noop', target_type='text', output_paths=['existing.csv'])
    ws, plan, result = execute(tmp_path, monkeypatch, task, [shell("print('no file changes')")])
    try:
        assert not result.success
        assert result.metadata['error_code'] == 'ARTIFACT_OUTPUT_UNVERIFIED'
        assert not result.metadata['verified_files']
    finally:
        ws.close()


def test_nonzero_command_keeps_verified_partial_files(tmp_path, monkeypatch):
    task = Task(id='partial', verb='execute', target='partial generator', target_type='text', output_paths=['first.csv','missing.csv'])
    ws, plan, result = execute(tmp_path, monkeypatch, task, [shell(
        "from pathlib import Path; Path('first.csv').write_text('real partial'); raise SystemExit(2)")])
    try:
        assert not result.success
        state, answer = finalize(ws, plan, result)
        assert '已完成并保留' in answer and 'first.csv' in answer
        assert [a['reference'] for a in _verified_file_artifacts(state)] == ['first.csv']
    finally:
        ws.close()


def test_failed_multistep_task_keeps_earlier_write_receipt(tmp_path, monkeypatch):
    task = Task(id='multi', verb='write', target='first.txt', target_type='file')
    ws, plan, result = execute(tmp_path, monkeypatch, task, [
        ExecutionStep(tool='filesystem.write', args={'path': str(tmp_path/'first.txt'), 'content':'FIRST', 'exact':True}, outputs=['output']),
        shell('raise SystemExit(3)'),
    ])
    try:
        assert not result.success
        state, answer = finalize(ws, plan, result)
        assert state['plan'][0]['status'] == 'failed'
        assert 'first.txt' in answer and '已完成并保留' in answer
        assert len(_verified_file_artifacts(state)) == 1
    finally:
        ws.close()


def test_changed_file_does_not_reuse_old_verification(tmp_path, monkeypatch):
    task = Task(id='write', verb='write', target='a.txt', target_type='file')
    ws, plan, result = execute(tmp_path, monkeypatch, task, [ExecutionStep(tool='filesystem.write',
        args={'path':str(tmp_path/'a.txt'), 'content':'original', 'exact':True}, outputs=['output'])])
    try:
        (tmp_path/'a.txt').write_text('changed externally')
        state, answer = finalize(ws, plan, result)
        assert state['runtime_failure_code'] == 'ARTIFACT_CHANGED_AFTER_VERIFICATION'
        assert not _verified_file_artifacts(state)
        assert '已生成/更新' not in answer
    finally:
        ws.close()


def test_artifact_declaration_does_not_authorize_a_new_write_scope():
    from agent.cognition.effect_authorization import EffectAuthorization
    from agent.cognition.execution_need import RequestedOutcome
    auth = EffectAuthorization((RequestedOutcome.FILE_MUTATION, RequestedOutcome.COMMAND_EXECUTION), ('output/',))
    assert auth.validate_task(Task(id='bad', verb='execute', target='cmd', output_paths=['agent/runtime.py']).to_dict())
    assert not auth.validate_task(Task(id='good', verb='execute', target='cmd', output_paths=['output/report.csv']).to_dict())


@pytest.mark.parametrize('tamper', [False, True])
def test_service_publishes_command_artifacts_with_matching_digest(monkeypatch, tmp_path, tamper):
    from tests.test_ha_r1_harness import call_tool, setup_provider, service_at, run_turn, intent
    import tools.shell  # noqa: F401 — registers the shell tool for the loop catalog
    from agent import sandbox
    monkeypatch.setenv('TSAGENT_ALLOW_LOCAL_EXECUTION', '1')
    monkeypatch.setattr(sandbox, '_check_docker', lambda: False)
    if tamper:
        from agent.service.runtime_launcher import RuntimeExecutionLauncher
        original = RuntimeExecutionLauncher._artifact_commit_facts
        def changed_before_commit(runtime):
            (tmp_path/'output/a.csv').write_text('tampered at publication')
            return original(runtime)
        monkeypatch.setattr(RuntimeExecutionLauncher, '_artifact_commit_facts', staticmethod(changed_before_commit))
    request = '生成两份 CSV 报告并保存在工作区'
    command = shell("from pathlib import Path; Path('output').mkdir(exist_ok=True); Path('output/a.csv').write_text('A'); Path('output/b.csv').write_text('B'); print('TWO-REPORTS')").args['cmd']
    provider = setup_provider(monkeypatch, [
        (request, intent(request, artifact=True, outcomes=['FILE_MUTATION','COMMAND_EXECUTION'])),
        (request, call_tool('shell', cmd=command,
            output_paths=['output/a.csv','output/b.csv'])),
        ('TWO-REPORTS', '已生成两份 CSV 报告'),
    ])
    async def scenario():
        service, _ = service_at(tmp_path)
        try:
            snapshot, _ = await run_turn(service, request, 1)
            if tamper:
                assert snapshot.status.value == 'FAILED_TERMINAL'
                assert 'ARTIFACT_CHANGED_AFTER_VERIFICATION' in snapshot.output.text
                assert len(snapshot.artifacts) == 1
                assert snapshot.artifacts[0].digest == hashlib.sha256(b'B').hexdigest()
                return
            assert snapshot.status.value == 'COMPLETED', snapshot
            assert len(snapshot.artifacts) == 2
            assert 'TWO-REPORTS' in snapshot.output.text
            paths = [tmp_path/'output/a.csv', tmp_path/'output/b.csv']
            assert {item.digest for item in snapshot.artifacts} == {hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
            for path in paths:
                assert str(path) in snapshot.output.text
        finally:
            await service.close()
    asyncio.run(scenario())
    assert not provider.replies


def test_copy_and_move_publish_actual_destinations(tmp_path, monkeypatch):
    for verb in ('copy', 'move'):
        (tmp_path/'source.txt').write_text('SOURCE')
        task = Task(id=verb, verb=verb, target='logical-target.txt', target_type='file')
        destination = f'{verb}.txt'
        ws, plan, result = execute(tmp_path, monkeypatch, task, [ExecutionStep(
            tool=f'filesystem.{verb}', args={'source':str(tmp_path/'source.txt'),
                'destination':str(tmp_path/destination), 'exact':True}, outputs=['output'])])
        try:
            assert result.success, result.error
            state, answer = finalize(ws, plan, result)
            assert [a['reference'] for a in _verified_file_artifacts(state)] == [destination]
            assert str(tmp_path/destination) in answer
        finally:
            ws.close()


def test_write_then_move_retires_old_artifact_receipt(tmp_path, monkeypatch):
    write = Task(id='write', verb='write', target='original.txt', target_type='file')
    ws, write_plan, written = execute(tmp_path, monkeypatch, write, [ExecutionStep(
        tool='filesystem.write', args={'path':str(tmp_path/'original.txt'), 'content':'CONTENTS', 'exact':True}, outputs=['output'])])
    ws.close()
    move = Task(id='move', verb='move', target='final.txt', target_type='file', dependencies=['write'])
    ws, move_plan, moved = execute(tmp_path, monkeypatch, move, [ExecutionStep(
        tool='filesystem.move', args={'source':str(tmp_path/'original.txt'), 'destination':str(tmp_path/'final.txt'), 'exact':True}, outputs=['output'])])
    try:
        tasks = [write.to_dict(), move.to_dict()]
        state = dict(
            plan=tasks,
            artifact_required=True,
            requested_outcomes=['FILE_MUTATION'],
            goal_state=GoalState(
                objective='生成文件并移动到最终路径',
                delivery_requirements=(DeliveryRequirement('deliverable', 'file', 1),),
            ).to_dict(),
        )
        for task, plan, result in zip(tasks, [write_plan, move_plan], [written, moved]):
            assert result.success, result.error
            ExecutionStage._apply_result(task, plan, result)
            record_execution_evidence(state, plan, result)
        finalizer = Finalizer(SimpleNamespace(run_context=SimpleNamespace(workspace=ws),
            session_context=SimpleNamespace(memory_view=None),
            _timings={}))
        answer = asyncio.run(finalizer.run(state, '生成文件并移动到最终路径', 'audit'))
        assert 'ARTIFACT_CHANGED' not in answer
        assert str(tmp_path/'final.txt') in answer
        assert [a['reference'] for a in _verified_file_artifacts(state)] == ['final.txt']
    finally:
        ws.close()
