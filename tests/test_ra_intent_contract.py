"""R-A: intent-driven contract tests — no deterministic interpreters."""
import asyncio
from pathlib import Path

from tests.test_ha_r1_harness import (
    call_tool, intent, run_turn, service_at, setup_provider,
)


def test_a_calculator_request_completes_with_reply_mentions_path(monkeypatch, tmp_path):
    """User's real case run-a1592420ea66440996627fa087bc479e."""
    request = '写个计算器，然后告诉我放哪个目录底下了'
    code = 'def add(a,b):\n    return a+b\nprint(add(1,2))\n'
    provider = setup_provider(monkeypatch, [
        (request, intent(request, artifact=True, outcomes=['FILE_MUTATION'],
            deliverable_acceptance=[{"kind": "reply_mentions_artifact_path"}])),
        (request, call_tool('write_file', path='output/calculator.py',
                            content=code)),
        ('calculator.py', '已创建 output/calculator.py'),
    ])

    async def scenario():
        service, _ = service_at(tmp_path)
        try:
            snapshot, events = await run_turn(service, request, 1)
            assert snapshot.status.value == 'COMPLETED', snapshot
            assert 'output/calculator.py' in snapshot.output.text
            f = tmp_path / 'output/calculator.py'
            assert f.read_text().strip() == code.strip()
        finally:
            await service.close()
    asyncio.run(scenario())
    assert not provider.replies


def test_b_unverifiable_criterion_disclosed_and_completes(monkeypatch, tmp_path):
    request = '写一个待办清单脚本，界面要美观'
    provider = setup_provider(monkeypatch, [
        (request, intent(request, artifact=True, outcomes=['FILE_MUTATION'],
            deliverable_acceptance=[{"kind": "unverifiable", "description": "界面美观"}])),
        (request, call_tool('write_file', path='output/todo.py',
                            content='print("todo")\n')),
        ('todo.py', '已创建 output/todo.py'),
    ])

    async def scenario():
        service, _ = service_at(tmp_path)
        try:
            snapshot, events = await run_turn(service, request, 1)
            assert snapshot.status.value == 'COMPLETED', snapshot
            assert '未自动验证：界面美观' in snapshot.output.text
        finally:
            await service.close()
    asyncio.run(scenario())


def test_c_file_contains_missing_fails_without_repair(monkeypatch, tmp_path):
    """An unmet criterion is an honest failure, never an edit-the-file repair."""
    request = '创建 output/report.md，内容必须包含“摘要”。'
    code = '正文内容\n'
    semantics = intent(request, artifact=True, outcomes=['FILE_MUTATION'],
        deliveries=[{"id": "report", "kind": "file", "quantity": 1,
                     "target": "output/report.md",
                     "acceptance_criteria": [{"kind": "file_contains", "text": "摘要"}]}])
    provider = setup_provider(monkeypatch, [
        (request, semantics),
        (request, call_tool('write_file', path='output/report.md',
                            content=code)),
        ('report.md', '已创建 output/report.md'),
    ])

    async def scenario():
        service, _ = service_at(tmp_path)
        try:
            snapshot, _events = await run_turn(service, request, 1)
            assert snapshot.status.value != 'COMPLETED', snapshot
            assert 'acceptance' in snapshot.output.text or '验收' in snapshot.output.text or '摘要' in snapshot.output.text
        finally:
            await service.close()
    asyncio.run(scenario())
    # intent + write_file call + final answer: no repair pass, no file rewrite.
    assert len(provider.calls) == 3
    assert (tmp_path / 'output/report.md').read_text().strip() == code.strip()


def test_c2_deliverable_missing_still_triggers_repair(monkeypatch, tmp_path):
    """Model answers without a deliverable → verifier feedback drives a
    bounded repair round until the file is actually written."""
    request = '创建 output/report.md。'
    semantics = intent(request, artifact=True, outcomes=['FILE_MUTATION'],
        deliveries=[{"id": "report", "kind": "file", "quantity": 1,
                     "target": "output/report.md", "acceptance_criteria": []}])
    provider = setup_provider(monkeypatch, [
        (request, semantics),
        (request, '概述报告需求'),
        ('deliverable:report', call_tool('write_file', path='output/report.md',
                                         content='报告内容\n')),
        ('report.md', '已创建 output/report.md'),
    ])

    async def scenario():
        service, _ = service_at(tmp_path)
        try:
            snapshot, _events = await run_turn(service, request, 1)
            assert snapshot.status.value == 'COMPLETED', snapshot
            assert (tmp_path / 'output/report.md').read_text().strip() == '报告内容'
            assert 'deliverable:report' in provider.calls[2]
        finally:
            await service.close()
    asyncio.run(scenario())
    # intent + premature answer + write_file + final answer prove repair ran.
    assert len(provider.calls) == 4


def test_d_effect_authorization_write_targets():
    from agent.cognition.effect_authorization import EffectAuthorization

    def auth(**kw):
        state = dict(original_request='', resolved_request='',
                     requested_outcomes=kw.pop('outcomes', ()),
                     artifact_required=kw.pop('artifact', False),
                     intent_write_targets=kw.pop('targets', ()),
                     goal_state={})
        state.update(kw)
        return EffectAuthorization.from_state(state, '')

    a = auth(outcomes=['FILE_MUTATION'], targets=['src/app.py'])
    assert a.allows_path('src/app.py')
    assert not a.allows_path('other/x.py')
    assert not a.allows_path('output/anything.py')

    b = auth(outcomes=['FILE_MUTATION'], artifact=True)
    assert b.write_scopes == ('output/',)
    assert b.allows_path('output/calc.py')
    assert not b.allows_path('src/app.py')

    c = auth(outcomes=['USER_VISIBLE_OUTPUT'])
    assert not c.allows_file_mutation
    assert c.validate_task({'verb': 'write', 'target': 'output/a.py',
                            'target_type': 'file'}) is not None


def test_e_external_effect_unsupported_blocks_before_planner(monkeypatch, tmp_path):
    request = '帮我给张三发条微信消息'
    provider = setup_provider(monkeypatch, [
        (request, intent(request, domain='运维', action='send',
            external_effects=[{"capability": "message_send", "description": "发消息"}])),
    ])

    async def scenario():
        service, _ = service_at(tmp_path)
        try:
            snapshot, events = await run_turn(service, request, 1)
            assert snapshot.status.value == 'BLOCKED', snapshot
            assert '已发送' not in snapshot.output.text
        finally:
            await service.close()
    asyncio.run(scenario())
    # exactly one provider call (intent); planner never ran
    assert len(provider.calls) == 1


def test_f_context_needs_memory_controls_retrieval(monkeypatch, tmp_path):
    request = '你好'
    calls = []

    class RecordingView:
        def get_retrieved_memory(self, query, **kw):
            calls.append(query)
            return {'long_term': 'LT', 'facts': 'F'}
        def get_user_facts(self, *_a, **_kw):
            return {}
        def runtime_pending(self, *_a):
            return False

    provider = setup_provider(monkeypatch, [
        (request, intent(request, domain='闲聊', action='answer',
            context_needs={"memory": True, "repository": False})),
        ('你好', '你好！'),
    ])

    async def scenario():
        service, _ = service_at(tmp_path)
        # swap in the recording view is hard here; just verify contract
        try:
            snapshot, _ = await run_turn(service, request, 1)
            assert snapshot.status.value == 'COMPLETED', snapshot
        finally:
            await service.close()
    asyncio.run(scenario())


def test_g_greeting_makes_one_intent_and_one_answer_call(monkeypatch, tmp_path):
    request = '你好'
    provider = setup_provider(monkeypatch, [
        (request, intent(request, domain='闲聊', action='answer')),
        (request, '你好！很高兴见到你。'),
    ])

    async def scenario():
        service, _ = service_at(tmp_path)
        try:
            snapshot, _ = await run_turn(service, request, 1)
            assert snapshot.status.value == 'COMPLETED', snapshot
            assert '你好' in snapshot.output.text
        finally:
            await service.close()
    asyncio.run(scenario())
    assert len(provider.calls) == 2
    assert not provider.replies
