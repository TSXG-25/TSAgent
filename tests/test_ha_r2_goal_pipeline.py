"""HA-R2: scripted semantics; real Service, Compiler, effects and outputs.

These tests verify the cross-module contract, not Provider language accuracy.
"""
import asyncio

import pytest

import tools.python  # noqa: F401 — registers run_python/run_python_file for the loop catalog
from tests.test_ha_r1_harness import call_tool, intent, run_turn, service_at, setup_provider
from agent.cognition.cognitive_context import CognitiveContext
from agent.cognition.effect_authorization import EffectAuthorization
from agent.cognition.intent_engine import IntentEngine
from agent.execution_errors import ProviderError



def test_create_and_execute_keeps_semantic_mutation_requirement(monkeypatch):
    from tests.test_ha_r1_harness import ScriptedProvider
    request = '写一个 Python 文件 fibonacci.py，实现 fibonacci(n)，然后实际运行几个例子验证它。'
    engine = IntentEngine()
    engine._llm = ScriptedProvider([(request, intent(request, artifact=True,
        outcomes=['FILE_MUTATION', 'CODE_EXECUTION'], target='fibonacci.py',
        write_targets=['fibonacci.py']))])
    result = asyncio.run(engine.analyze_async(CognitiveContext(query=request)))
    assert not engine._llm.replies
    assert result.artifact_required
    auth = EffectAuthorization.from_state(dict(original_request=request,
        resolved_request=result.resolved_request, intent_target=result.target,
        artifact_required=result.artifact_required,
        intent_write_targets=list(result.write_targets),
        requested_outcomes=[x.value for x in result.requested_outcomes]), request)
    assert auth.allows_path('fibonacci.py')
    assert not auth.allows_path('unrequested.py')
    assert auth.code_execution_allowed and auth.allows_file_mutation


def test_semantic_target_cannot_expand_original_path_scope():
    request = '给 hello.py 加一个 main 函数'
    state = dict(original_request=request, resolved_request='修改 agent/runtime.py',
                 intent_target='agent/runtime.py', requested_outcomes=['FILE_MUTATION'],
                 intent_write_targets=['hello.py'],
                 artifact_required=True)
    auth = EffectAuthorization.from_state(state, request)
    assert not auth.allows_path('agent/runtime.py')


def test_named_python_creation_and_real_execution_through_service(monkeypatch, tmp_path):
    from agent import sandbox
    monkeypatch.setenv('TSAGENT_ALLOW_LOCAL_EXECUTION', '1')
    monkeypatch.setattr(sandbox, '_check_docker', lambda: False)
    request = '写一个 Python 文件 fibonacci.py，实现 fibonacci(n)，然后实际运行几个例子验证它。'
    source = ('def fibonacci(n):\n'
              '    a, b = 0, 1\n'
              '    for _ in range(n):\n'
              '        a, b = b, a + b\n'
              '    return a\n'
              'assert fibonacci(0) == 0\nassert fibonacci(5) == 5\n'
              'print("FIBONACCI-VERIFIED", fibonacci(0), fibonacci(5))\n')
    provider = setup_provider(monkeypatch, [
        (request, intent(request, target='fibonacci.py', artifact=True,
                         outcomes=['FILE_MUTATION', 'CODE_EXECUTION'],
                         write_targets=['fibonacci.py'])),
        (request, call_tool('write_file', path='fibonacci.py', content=source)),
        ('fibonacci.py', call_tool('run_python_file', path='fibonacci.py')),
        ('FIBONACCI-VERIFIED', 'fibonacci.py 已创建并实际运行验证，输出 FIBONACCI-VERIFIED 0 5。'),
    ])
    async def scenario():
        service, _ = service_at(tmp_path)
        try:
            snapshot, events = await run_turn(service, request, 1)
            assert snapshot.status.value == 'COMPLETED', snapshot
            assert (tmp_path/'fibonacci.py').read_text() == source
            assert 'FIBONACCI-VERIFIED 0 5' in snapshot.output.text
            assert str(tmp_path/'fibonacci.py') in snapshot.output.text
            assert snapshot.artifacts
            assert sum(e.event_type.value == 'task_completed' for e in events) == 2
        finally:
            await service.close()
    asyncio.run(scenario())
    assert not provider.replies


def test_service_does_not_repair_a_failed_acceptance_criterion(
    monkeypatch,
    tmp_path,
):
    request = '创建 output/report.md，内容必须包含“摘要”。'
    semantics = intent(
        request,
        target='output/report.md',
        artifact=True,
        outcomes=['FILE_MUTATION'],
    )
    semantics['delivery_requirements'][0]['target'] = 'output/report.md'
    semantics['delivery_requirements'][0]['acceptance_criteria'] = [
        {"kind": "file_contains", "text": "摘要"}
    ]
    provider = setup_provider(monkeypatch, [
        (request, semantics),
        (request, call_tool('write_file', path='output/report.md',
                            content='本报告仅包含正文内容。')),
        ('report.md', '已创建 output/report.md'),
    ])

    async def scenario():
        service, _store = service_at(tmp_path)
        try:
            snapshot, events = await run_turn(service, request, 1)
            report = tmp_path / 'output/report.md'
            # An unmet acceptance criterion is an honest failure; repair
            # replan must never edit the delivered file to satisfy the text.
            assert snapshot.status.value != 'COMPLETED', snapshot
            assert '本报告仅包含正文内容。' in report.read_text(encoding='utf-8')
            assert '摘要' not in report.read_text(encoding='utf-8')
            assert not any(
                event.event_type.value == 'stage_started'
                and event.payload.get('phase') == 'replan'
                for event in events
            )
        finally:
            await service.close()

    asyncio.run(scenario())
    assert not provider.replies


def test_service_correction_versions_goal_and_preserves_new_prohibition(
    monkeypatch,
    tmp_path,
):
    first_request = '创建 output/tool.py'
    correction = '纠正：不要运行 output/tool.py，只保留脚本并补充用途说明。'
    first_intent = intent(
        first_request,
        target='output/tool.py',
        artifact=True,
        outcomes=['FILE_MUTATION'],
    )
    first_intent['delivery_requirements'][0]['target'] = 'output/tool.py'
    correction_intent = intent(
        correction,
        target='output/tool.py',
        relation='correction',
        artifact=True,
        outcomes=['FILE_READ', 'FILE_MUTATION'],
    )
    correction_intent['delivery_requirements'][0]['target'] = 'output/tool.py'
    correction_intent['prohibited_effects'] = [
        'CODE_EXECUTION', 'COMMAND_EXECUTION'
    ]
    updated_source = 'print("v1")\n# 用途：演示工具脚本。'
    provider = setup_provider(monkeypatch, [
        (first_request, first_intent),
        (first_request, call_tool('write_file', path='output/tool.py',
                                  content='print("v1")')),
        ('tool.py', '已创建 output/tool.py'),
        (correction, correction_intent),
        (correction, call_tool('read_file', path='output/tool.py')),
        ('print("v1")', call_tool('write_file', path='output/tool.py',
                                 content=updated_source)),
        ('用途', '已保留脚本并补充用途说明。'),
    ])

    async def scenario():
        service, _store = service_at(tmp_path)
        try:
            first, _ = await run_turn(service, first_request, 1)
            assert first.status.value == 'COMPLETED', first
            corrected, events = await run_turn(service, correction, 2)
            assert corrected.status.value == 'COMPLETED', corrected
            assert (tmp_path / 'output/tool.py').read_text() == updated_source
            terminal = next(
                event for event in reversed(events)
                if event.event_type.value == 'run_completed'
            )
            contract = terminal.payload['continuation']['goal_contract']
            assert contract['goal_version'] == 2
            assert tuple(contract['prohibited_effects']) == (
                'CODE_EXECUTION', 'COMMAND_EXECUTION'
            )
            assert all(
                not (
                    event.event_type.value == 'task_completed'
                    and event.payload.get('verb') == 'execute'
                )
                for event in events
            )
        finally:
            await service.close()

    asyncio.run(scenario())
    assert not provider.replies


def test_append_survives_preservation_clause_and_retains_original_bytes(monkeypatch, tmp_path):
    request = '把 test_note.txt 第二行追加“second line”，但不要覆盖原来的内容。'
    resolved = '先读取 test_note.txt，然后在文件末尾追加一行“second line”，不得覆盖或删除原有内容。'
    path = tmp_path / 'test_note.txt'
    path.write_text('TSAgent human test\n')
    provider = setup_provider(monkeypatch, [
        (request, intent(request, resolved=resolved, target='test_note.txt', action='append',
        write_targets=['test_note.txt'],
                         artifact=True, outcomes=['FILE_READ', 'FILE_MUTATION'])),
        (resolved, call_tool('read_file', path='test_note.txt')),
        ('TSAgent human test', call_tool('write_file', path='test_note.txt',
            content='second line\n', mode='append')),
        ('second line', '已在 test_note.txt 末尾追加“second line”，原有内容已保留。'),
    ])
    async def scenario():
        service, _ = service_at(tmp_path)
        try:
            snapshot, events = await run_turn(service, request, 1)
            assert snapshot.status.value == 'COMPLETED', snapshot
            assert path.read_text() == 'TSAgent human test\nsecond line\n'
            assert str(path) in snapshot.output.text
            assert snapshot.artifacts
        finally:
            await service.close()
    asyncio.run(scenario())
    assert not provider.replies


def test_write_read_explain_delivers_current_content_and_path(monkeypatch, tmp_path):
    from tests.test_ha_r1_harness import seed_run
    request = '在当前 workspace 创建 test_note.txt，写入“TSAgent human test”，然后读取它并告诉我内容。'
    provider = setup_provider(monkeypatch, [
        (request, intent(request, target='test_note.txt', artifact=True,
        write_targets=['test_note.txt'],
                         outcomes=['FILE_MUTATION', 'FILE_READ'])),
        (request, call_tool('write_file', path='test_note.txt',
                            content='TSAgent human test')),
        ('test_note.txt', call_tool('read_file', path='test_note.txt')),
        ('TSAgent human test', '文件内容：TSAgent human test'),
    ])
    async def scenario():
        service, store = service_at(tmp_path)
        seed_run(store, answer='UNRELATED HISTORY', run='unrelated')
        try:
            snapshot, _ = await run_turn(service, request, 1)
            assert snapshot.status.value == 'COMPLETED', snapshot
            assert (tmp_path/'test_note.txt').read_text() == 'TSAgent human test'
            assert 'TSAgent human test' in snapshot.output.text
            assert str(tmp_path/'test_note.txt') in snapshot.output.text
            assert 'UNRELATED HISTORY' not in snapshot.output.text
            assert '"content": "TSAgent human test"' in provider.calls[-1]
            assert provider.calls[-1].find('UNRELATED HISTORY') < provider.calls[-1].rfind('"content": "TSAgent human test"')
        finally:
            await service.close()
    asyncio.run(scenario())
    assert not provider.replies


def test_provider_timeout_after_search_preserves_category_and_partial_work(monkeypatch, tmp_path):
    import tools.web
    from agent.registry.tool_registry import registry
    async def web_search(query: str, timeliness: str = 'any') -> str:
        """Offline search boundary."""
        return '测试新闻事件来源与日期，仅用于离线契约验证。' * 4 + ' https://example.org/news'
    monkeypatch.setattr(registry, '_tools', dict(registry._tools))
    registry.register(web_search, name='web_search')
    request_text = '搜索今天的新闻并总结'
    provider = setup_provider(monkeypatch, [
        (request_text, intent(request_text, domain='网络', action='fresh_research',
            source=True, freshness=True, search_query='今天的新闻')),
        (request_text, call_tool('web_search', query='今天的新闻')),
        ('example.org/news', ProviderError('scripted provider timeout', TimeoutError())),
    ])
    async def scenario():
        service, _ = service_at(tmp_path)
        try:
            snapshot, events = await run_turn(service, request_text, 1)
            assert snapshot.status.value == 'FAILED_TERMINAL', snapshot
            assert 'PROVIDER_TIMEOUT' in snapshot.output.text
            assert '已完成的步骤' in snapshot.output.text
            assert 'search' in snapshot.output.text
            terminal = events[-1]
            assert terminal.payload['failure_code'] == 'PROVIDER_TIMEOUT'
            assert terminal.payload['failure_class'] == 'provider'
        finally:
            await service.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('verb', ['写个', '做个', '实现个', '搞个', '帮我弄个'])
@pytest.mark.parametrize('noun', ['网页', '脚本', '程序', '文件', '项目', 'calculator', 'API'])
def test_deliverable_family_contract_rejects_text_only_completion(verb, noun):
    # The semantic decision is supplied, never claimed as model eval evidence.
    from agent.goal import GoalVerifier
    request = verb + noun
    engine = IntentEngine()
    from tests.test_ha_r1_harness import ScriptedProvider
    engine._llm = ScriptedProvider([(request, intent(request, artifact=True, outcomes=['FILE_MUTATION']))])
    semantic = asyncio.run(engine.analyze_async(CognitiveContext(query=request)))
    assert semantic.artifact_required
    state = dict(artifact_required=semantic.artifact_required,
                 requested_outcomes=[x.value for x in semantic.requested_outcomes], plan=[])
    assert not GoalVerifier.verify(state, '已完成，下载链接 https://example.org/fake').can_complete
