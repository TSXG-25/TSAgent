"""Local-only human-acceptance regressions: real Runtime, SQLite and filesystem.

Only Provider/search responses are fixtures. No external API is called.
"""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent.cognition.cognitive_context import CognitiveContext
from agent.cognition.intent_engine import IntentEngine
from agent.runtime_store import SqliteRuntimeStore
from agent.service import AgentService, StartRunRequest, EventStreamRequest, RunLookupRequest
from agent.service.context_factory import ServiceContextFactory
from agent.service.runtime_launcher import RuntimeExecutionLauncher


CREATE = "帮我写个汇率转换计算器，写完告诉我放哪了"
MODIFY = "再给它加一个 HKD → JPY 的例子。"
CORRECT = "什么东西，你不是应该直接在本地做吗，这啥玩意"
SOURCE = 'def convert(amount, rate):\n    return amount * rate\n'
UPDATED = SOURCE + '\nprint("HKD -> JPY:", convert(100, 18.5))\n'


def intent(request, *, resolved=None, relation="new_request", artifact=False, outcomes=(), domain="开发", action="code", target="", source=False, write_targets=(), acceptance=(), deliverable_acceptance=(), external_effects=(), context_needs=None, memory_action="none", deliveries=None, search_query="", search_timeliness="any", prohibited=(), freshness=False):
    if deliveries is None:
        deliveries = ([{"id": "deliverable", "kind": "file", "quantity": 1,
                       "target": "", "acceptance_criteria": list(deliverable_acceptance)}]
                      if artifact else [])
    return dict(domain=domain, action=action, target=target, entities=[], confidence=.95,
                requires_execution=bool(outcomes or source or external_effects or artifact), summary=request,
                resolved_request=resolved or request, conversation_relation=relation,
                requested_outcomes=["USER_VISIBLE_OUTPUT", *outcomes], artifact_required=artifact,
                delivery_requirements=deliveries,
                prohibited_effects=list(prohibited), acceptance_criteria=list(acceptance),
                write_targets=list(write_targets),
                external_effects=list(external_effects),
                search_query=search_query, search_timeliness=search_timeliness,
                context_needs=context_needs or {"memory": False, "repository": False},
                memory_action=memory_action,
                freshness_required=freshness, source_grounding_required=source)


def call_tool(name, **arguments):
    """Scripted achat reply carrying one native tool call."""
    return {'tool_calls': [{'name': name, 'arguments': arguments}]}


class ScriptedProvider:
    supports_structured_output = False

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    @staticmethod
    def _text(messages) -> str:
        parts = []
        for message in messages:
            if isinstance(message, dict):
                content = str(message.get('content', ''))
                tool_calls = message.get('tool_calls') or ()
                if tool_calls:
                    content += "\n" + json.dumps(tool_calls, ensure_ascii=False)
                parts.append(content)
            else:
                parts.append(str(getattr(message, 'content', '')))
        return "\n".join(parts)

    def _next(self, text):
        self.calls.append(text)
        assert self.replies, f"Unexpected Provider call: {text[:500]}"
        required, reply = self.replies.pop(0)
        assert required in text, (required, text[:1000])
        if isinstance(reply, Exception):
            raise reply
        return reply

    async def ainvoke(self, messages, **kwargs):
        reply = self._next(self._text(messages))
        return SimpleNamespace(content=json.dumps(reply, ensure_ascii=False) if isinstance(reply, dict) else str(reply))

    async def achat(self, messages, tools=None, **kwargs):
        from agent.llm import ChatTurn, ToolCall

        reply = self._next(self._text(messages))
        if isinstance(reply, ChatTurn):
            return reply
        if isinstance(reply, dict) and 'tool_calls' in reply:
            return ChatTurn(
                content=str(reply.get('content', '')),
                tool_calls=tuple(
                    ToolCall(
                        id=str(call.get('id', f'call-{index}')),
                        name=str(call['name']),
                        arguments=dict(call.get('arguments') or {}),
                    )
                    for index, call in enumerate(reply['tool_calls'])
                ),
            )
        return ChatTurn(
            content=json.dumps(reply, ensure_ascii=False)
            if isinstance(reply, dict)
            else str(reply)
        )


def setup_provider(monkeypatch, replies):
    provider = ScriptedProvider(replies)
    monkeypatch.setattr('agent.llm.llm', provider)
    # runner binds `from agent.llm import llm` at import time, so patch the
    # name in the loop module's own namespace as well.
    monkeypatch.setattr('agent.loop.runner.llm', provider)
    engine = IntentEngine()
    engine._llm = provider
    monkeypatch.setattr('agent.orchestrator.planner.intent_engine', engine)
    monkeypatch.setattr('agent.answer_generator.llm', provider)
    return provider


async def run_turn(service, text, number, session="ha-session"):
    request = StartRunRequest(tenant_id='ha-tenant', user_id='ha-user', session_id=session,
                              run_id=f'{session}-{number}', request_id=f'request-{session}-{number}', request_text=text)
    handle = await service.start_run(request)
    events = [event async for event in service.stream_events(EventStreamRequest(
        tenant_id=request.tenant_id, user_id=request.user_id, session_id=session,
        run_id=handle.run_id, request_id=f'events-{session}-{number}'))]
    snapshot = await service.get_run(RunLookupRequest(
        tenant_id=request.tenant_id, user_id=request.user_id, session_id=session,
        run_id=handle.run_id, request_id=f'lookup-{session}-{number}'))
    return snapshot, events


def service_at(tmp_path):
    store = SqliteRuntimeStore.open(tmp_path/'runtime.sqlite')
    service = AgentService(runtime_store=store, launcher=RuntimeExecutionLauncher(),
                           context_factory=ServiceContextFactory(store, workspace_root=tmp_path))
    return service, store


def test_real_creation_then_modification_across_service_restart(monkeypatch, tmp_path):
    provider = setup_provider(monkeypatch, [
        (CREATE, intent(CREATE, artifact=True, outcomes=['FILE_MUTATION'])),
        (CREATE, call_tool('write_file', path='output/calculator.py', content=SOURCE)),
        ('output/calculator.py', '已创建 output/calculator.py'),
        (MODIFY, intent(MODIFY, resolved='修改 output/calculator.py，保留现有换算函数并加入 HKD → JPY 示例。',
                        relation='reference', artifact=True, outcomes=['FILE_READ','FILE_MUTATION'], action='modify', target='output/calculator.py',
            write_targets=['output/calculator.py'])),
        ('output/calculator.py', call_tool('read_file', path='output/calculator.py')),
        ('def convert', call_tool('write_file', path='output/calculator.py', content=UPDATED)),
        ('HKD', '已更新 output/calculator.py，加入 HKD → JPY 示例。'),
    ])

    async def scenario():
        service, store = service_at(tmp_path)
        try:
            first, events = await run_turn(service, CREATE, 1)
            assert first.status.value == 'COMPLETED', first
            assert (tmp_path/'output/calculator.py').read_text().strip() == SOURCE.strip()
            assert str(tmp_path/'output/calculator.py') in first.output.text
            assert first.artifacts and any(e.event_type.value == 'task_completed' for e in events)
        finally:
            await service.close()
        service, store = service_at(tmp_path)
        try:
            second, _ = await run_turn(service, MODIFY, 2)
            assert second.status.value == 'COMPLETED', second
            assert (tmp_path/'output/calculator.py').read_text().strip() == UPDATED.strip()
            assert second.artifacts
            intent_prompt = next(call for call in provider.calls if MODIFY in call)
            assert 'output/calculator.py' in intent_prompt
            assert '[产物]' in intent_prompt
            assert SOURCE.strip() in provider.calls[-1]
        finally:
            await service.close()
    asyncio.run(scenario())
    assert not provider.replies


def test_artifact_contract_survives_false_answer(monkeypatch, tmp_path):
    from agent.orchestrator.finalizer import Finalizer
    from agent.runtime import _build_run_evidence
    from agent.services.workspace_service import WorkspaceService
    from agent.event_bus import EventBus
    workspace = WorkspaceService.scoped(tmp_path, event_bus=EventBus(), build_index=False)
    finalizer = Finalizer(SimpleNamespace(run_context=SimpleNamespace(workspace=workspace),
                                        session_context=SimpleNamespace(memory_view=None)))
    from agent.goal import DeliveryRequirement, GoalState
    state = dict(
        plan=[],
        requested_outcomes=['USER_VISIBLE_OUTPUT','FILE_MUTATION'],
        artifact_required=True,
        goal_state=GoalState(
            objective=CREATE,
            delivery_requirements=(DeliveryRequirement('calculator', 'file', 1),),
        ).to_dict(),
    )
    answer = asyncio.run(finalizer.run(state, CREATE, 'user', '已创建 https://craft.do/s/example-exchange-rate-calculator'))
    assert 'craft.do' not in answer
    assert _build_run_evidence(state, answer, 1, CREATE)['terminal_status'] != 'COMPLETED'
    assert 'FILE_MUTATION' in state['requested_outcomes']
    workspace.close()



def test_malformed_intent_fails_before_execution():
    engine = IntentEngine()
    engine._llm = ScriptedProvider([('用户输入', {'domain':'数学','requires_execution':False})])
    result = asyncio.run(engine.analyze_async(CognitiveContext(query=CREATE)))
    assert result.failure_code == 'INTENT_CONTRACT_INVALID'


def seed_run(store, *, status='COMPLETED', answer='', goal=CREATE, tenant='ha-tenant', session='ha-session', run='seed', artifacts=(), goal_contract=None):
    from agent.runtime_store import DurableRuntimeStoreView
    store.initialize_run(tenant, session, run, run_status='RUNNING')
    view = DurableRuntimeStoreView(store, tenant_id=tenant, session_id=session, run_id=run,
                                   request_id='seed-'+run, writer_id='writer-'+run, ensure_run=False)
    view.transition_run_with_event(run_status=status, event_id='event-'+run,
        event_type='run_completed' if status == 'COMPLETED' else 'run_failed',
        timestamp='2026-09-08T00:00:00Z',
        payload={'continuation':{'request':goal,'goal':goal,'missing':[],'observations':[],
                                 'goal_contract':goal_contract or {}}},
        run_output={'text':answer,'artifact_ids':[],'evidence_ids':[]}, artifacts=artifacts)
    view.close()


def test_correction_keeps_previous_unmet_goal_and_creates_real_file(monkeypatch, tmp_path):
    from agent.goal import DeliveryRequirement, GoalState

    provider = setup_provider(monkeypatch, [
        (CORRECT, intent(CORRECT, resolved='创建本地汇率转换计算器并告诉用户路径。', relation='correction', artifact=True, outcomes=['FILE_MUTATION'])),
        ('汇率转换计算器', call_tool('write_file', path='output/recovered.py', content=SOURCE)),
        ('recovered.py', '已创建本地文件 output/recovered.py'),
    ])
    async def scenario():
        service, store = service_at(tmp_path)
        previous_contract = GoalState(
            objective=CREATE,
            original_request=CREATE,
            goal_id='goal-original',
            goal_version=4,
            requested_outcomes=('USER_VISIBLE_OUTPUT', 'FILE_MUTATION'),
            delivery_requirements=(DeliveryRequirement('calculator', 'file', 1),),
        ).to_dict()
        seed_run(store, answer='已创建 https://craft.do/s/example-exchange-rate-calculator',
                 goal_contract=previous_contract)
        try:
            result, _ = await run_turn(service, CORRECT, 2)
            assert result.status.value == 'COMPLETED', result
            assert (tmp_path/'output/recovered.py').read_text().strip() == SOURCE.strip()
            assert 'craft.do' not in result.output.text
            assert CREATE in provider.calls[0]
            assert '已创建 https://craft.do/s/example-exchange-rate-calculator' in provider.calls[0]
            snapshot = store.read_run_snapshot(
                'ha-tenant', result.run_id, session_id='ha-session'
            )
            continuation = json.loads(snapshot.terminal_event.payload_json)['continuation']
            assert continuation['goal_contract']['goal_id'] == 'goal-original'
            assert continuation['goal_contract']['goal_version'] == 5
            assert continuation['goal_contract']['original_request'] == CREATE
        finally:
            await service.close()
    asyncio.run(scenario())
    assert not provider.replies


def test_four_turn_search_summary_creation_modification(monkeypatch, tmp_path):
    import tools.web  # Register production module before replacing its external boundary.
    from agent.registry.tool_registry import registry
    search_calls = []
    evidence = ('事件甲：测试来源报道电网恢复。 https://news.example.org/event-a\n'
                '事件乙：测试来源报道港口恢复。 https://news.example.org/event-b')
    async def web_search(query: str, timeliness: str = 'any') -> str:
        """Offline source fixture."""
        search_calls.append(query)
        return evidence
    # Preserve the registry for other tests.
    monkeypatch.setattr(registry, '_tools', dict(registry._tools))
    registry.register(web_search, name='web_search')
    summary_request = '这不够大呀，你总结一下'
    search_request = '今天有什么大新闻？'
    provider = setup_provider(monkeypatch, [
        (search_request, intent(search_request, domain='网络', action='fresh_research',
            source=True, freshness=True, search_query='电网 港口 恢复 新闻')),
        (search_request, call_tool('web_search', query='电网 港口 恢复 新闻')),
        ('news.example.org/event-a', '测试来源报道电网及港口恢复。来源：https://news.example.org/event-a 和 https://news.example.org/event-b'),
        (summary_request, intent(summary_request, resolved='总结上一轮检索到的电网和港口恢复事件，说明现有来源不足以判断其全球重要性。',
                                 relation='reference', domain='知识', action='summarize')),
        ('电网', '上一轮两个事件是电网和港口恢复；现有来源不足以判定它们是全球重大新闻。'),
        (CREATE, intent(CREATE, artifact=True, outcomes=['FILE_MUTATION'])),
        (CREATE, call_tool('write_file', path='output/calculator.py', content=SOURCE)),
        ('calculator.py', '已创建 output/calculator.py'),
        (MODIFY, intent(MODIFY, resolved='修改 output/calculator.py，保留换算函数并加入 HKD → JPY 示例。', relation='reference', artifact=True, outcomes=['FILE_READ','FILE_MUTATION'], action='modify', target='output/calculator.py',
            write_targets=['output/calculator.py'])),
        ('calculator.py', call_tool('read_file', path='output/calculator.py')),
        ('def convert', call_tool('write_file', path='output/calculator.py', content=UPDATED)),
        ('HKD', '已更新 output/calculator.py，加入 HKD → JPY 示例。'),
    ])
    async def scenario():
        service, store = service_at(tmp_path)
        try:
            first, _ = await run_turn(service, '今天有什么大新闻？', 1)
            assert first.status.value == 'COMPLETED', first
            second, _ = await run_turn(service, summary_request, 2)
            assert second.status.value == 'COMPLETED', second
            assert '电网' in second.output.text
            assert 'news.example.org/event-b' in provider.calls[2]
            assert len(search_calls) == 1
            third, _ = await run_turn(service, CREATE, 3)
            assert third.status.value == 'COMPLETED', third
            assert (tmp_path/'output/calculator.py').read_text().strip() == SOURCE.strip()
            fourth, _ = await run_turn(service, MODIFY, 4)
            assert fourth.status.value == 'COMPLETED', fourth
            assert (tmp_path/'output/calculator.py').read_text().strip() == UPDATED.strip()
            assert fourth.artifacts[0].digest != third.artifacts[0].digest
            assert str(tmp_path/'output/calculator.py') in fourth.output.text
            import subprocess, sys
            executed = subprocess.run([sys.executable, str(tmp_path/'output/calculator.py')],
                                      capture_output=True, text=True, check=True)
            assert 'HKD -> JPY: 1850.0' in executed.stdout
        finally:
            await service.close()
    asyncio.run(scenario())
    assert not provider.replies


def test_fresh_research_uses_local_mcp_stdio_through_service(monkeypatch, tmp_path):
    import sys
    import tools.web
    from agent.mcp import duckduckgo
    from agent.registry.tool_registry import registry
    from mcp.client.stdio import StdioServerParameters

    request = '搜索今天的离线测试新闻'
    server_script = Path(__file__).parent / 'fixtures' / 'fake_mcp_stdio_server.py'
    stopped_file = tmp_path / 'mcp-server-stopped'
    pid_file = tmp_path / 'mcp-server-pid'
    monkeypatch.setattr(registry, '_tools', dict(registry._tools))
    monkeypatch.setattr(
        duckduckgo,
        'shutil',
        SimpleNamespace(which=lambda _command: sys.executable),
    )

    def local_server_parameters(command, env):
        return StdioServerParameters(
            command=command,
            args=[str(server_script)],
            env={
                **env,
                'TEST_MCP_PID_FILE': str(pid_file),
                'TEST_MCP_STOP_FILE': str(stopped_file),
            },
        )

    monkeypatch.setattr(duckduckgo, 'StdioServerParameters', local_server_parameters)
    provider = setup_provider(monkeypatch, [
        (request, intent(request, domain='网络', action='fresh_research',
            source=True, freshness=True, search_query='离线测试新闻')),
        (request, call_tool('web_search', query='离线测试新闻')),
        ('example.test/offline', '离线 MCP 测试搜索结果。来源：https://example.test/offline'),
    ])

    async def scenario():
        service, _store = service_at(tmp_path)
        try:
            snapshot, events = await run_turn(service, request, 1)
            assert snapshot.status.value == 'COMPLETED', snapshot
            assert 'https://example.test/offline' in snapshot.output.text, provider.calls
            assert any(
                event.event_type.value == 'task_completed'
                and event.payload.get('verb') == 'search'
                for event in events
            )
        finally:
            await service.close()

    asyncio.run(scenario())
    assert not provider.replies
    # The final answer turn sees the source-backed web_search tool result.
    assert 'https://example.test/offline' in provider.calls[-1]
    assert stopped_file.read_text() == 'stopped'


def test_fresh_research_materializes_source_backed_report_through_service(
    monkeypatch,
    tmp_path,
):
    import sys
    import tools.web
    from agent.mcp import duckduckgo
    from agent.registry.tool_registry import registry
    from mcp.client.stdio import StdioServerParameters

    request = (
        '搜索今天的离线测试新闻，并把带来源的报告保存到 '
        'output/research.md'
    )
    server_script = Path(__file__).parent / 'fixtures' / 'fake_mcp_stdio_server.py'
    stopped_file = tmp_path / 'research-mcp-server-stopped'
    pid_file = tmp_path / 'research-mcp-server-pid'
    monkeypatch.setattr(registry, '_tools', dict(registry._tools))
    monkeypatch.setattr(
        duckduckgo,
        'shutil',
        SimpleNamespace(which=lambda _command: sys.executable),
    )

    def local_server_parameters(command, env):
        return StdioServerParameters(
            command=command,
            args=[str(server_script)],
            env={
                **env,
                'TEST_MCP_PID_FILE': str(pid_file),
                'TEST_MCP_STOP_FILE': str(stopped_file),
            },
        )

    monkeypatch.setattr(duckduckgo, 'StdioServerParameters', local_server_parameters)
    provider = setup_provider(monkeypatch, [
        (request, intent(request, domain='网络', action='fresh_research',
            artifact=True, source=True, freshness=True,
            outcomes=['FILE_MUTATION'],
            write_targets=['output/research.md'],
            search_query='离线测试新闻')),
        (request, call_tool('web_search', query='离线测试新闻')),
        ('example.test/offline', call_tool(
            'write_file', path='output/research.md',
            content='# 离线测试报告\n\n来源：https://example.test/offline\nSynthetic result for local MCP integration testing only.')),
        ('research.md', '已把带来源的报告保存到 output/research.md'),
    ])

    async def scenario():
        service, _store = service_at(tmp_path)
        try:
            snapshot, events = await run_turn(service, request, 1)
            assert snapshot.status.value == 'COMPLETED', snapshot
            report = tmp_path / 'output/research.md'
            assert report.is_file()
            content = report.read_text()
            assert content.startswith('# ')
            assert 'https://example.test/offline' in content
            assert 'Synthetic result for local MCP integration testing only.' in content
            assert str(report) in snapshot.output.text
            assert any(
                event.event_type.value == 'task_completed'
                and event.payload.get('verb') == 'search'
                for event in events
            )
            assert any(
                event.event_type.value == 'task_completed'
                and event.payload.get('verb') == 'write'
                and event.payload.get('verified') is True
                for event in events
            )
            artifacts = await service.list_artifacts(RunLookupRequest(
                tenant_id='ha-tenant',
                user_id='ha-user',
                session_id='ha-session',
                run_id='ha-session-1',
                request_id='research-report-artifacts',
            ))
            assert any(
                artifact.verified
                and artifact.reference.startswith('artifact://')
                and artifact.digest
                for artifact in artifacts
            )
        finally:
            await service.close()

    asyncio.run(scenario())
    assert not provider.replies
    assert stopped_file.read_text() == 'stopped'


def test_durable_continuation_is_session_and_tenant_scoped(tmp_path):
    from agent.runtime_store import DurableRuntimeStoreView
    store = SqliteRuntimeStore.open(tmp_path/'isolation.sqlite')
    seed_run(store, answer='own result', run='own')
    seed_run(store, answer='other session secret', session='other', run='other')
    seed_run(store, answer='other tenant secret', tenant='other', run='other')
    view = DurableRuntimeStoreView(store,tenant_id='ha-tenant',session_id='ha-session',run_id='current',request_id='current',writer_id='current')
    try:
        results = view.conversation_results()
        assert [item['run_id'] for item in results] == ['own']
        assert results[0]['answer'] == 'own result'
    finally:
        view.close()
        store.close()


def test_partial_files_disclosed_if_later_work_failed(monkeypatch, tmp_path):
    from agent.orchestrator.finalizer import Finalizer
    from agent.services.workspace_service import WorkspaceService
    from agent.event_bus import EventBus
    from agent.goal import DeliveryRequirement, GoalState
    workspace = WorkspaceService.scoped(tmp_path, event_bus=EventBus(), build_index=False)
    workspace.write_text('output/partial.py', SOURCE)
    finalizer = Finalizer(SimpleNamespace(run_context=SimpleNamespace(workspace=workspace),
                                        session_context=SimpleNamespace(memory_view=None)))
    state = dict(plan=[dict(id='write',verb='write',target='output/partial.py',status='succeeded'),
                       dict(id='run',verb='execute',target='output/partial.py',status='failed',error='nonzero')],
                 artifact_required=True, requested_outcomes=['FILE_MUTATION','CODE_EXECUTION'])
    state['goal_state'] = GoalState(
        objective='创建脚本并执行',
        delivery_requirements=(DeliveryRequirement('script', 'file', 1),),
    ).to_dict()
    from agent.executor.verifier import ExecutionArtifacts, verified_file_effects
    state['plan'][0]['verified_files'] = verified_file_effects(
        ExecutionArtifacts(files_written=['output/partial.py']), workspace)
    answer = asyncio.run(finalizer.run(state, '创建脚本并执行', 'user'))
    assert state['runtime_terminal_status'] != 'COMPLETED'
    assert 'output/partial.py' in answer and '已完成并保留' in answer
    workspace.close()


def test_service_cancellation_publishes_verified_partial_file(
    monkeypatch,
    tmp_path,
):
    from agent.interruption import CancelRunRequest
    from agent.mcp import duckduckgo
    import tools.web as web

    request_text = '生成 output/partial.txt 并执行后续只读检查'
    partial_text = '已核验的部分交付。'
    semantics = intent(
        request_text,
        target='output/partial.txt',
        artifact=True,
        outcomes=['FILE_MUTATION'],
    )
    semantics['delivery_requirements'][0]['target'] = 'output/partial.txt'
    provider = setup_provider(monkeypatch, [
        (request_text, semantics),
        (request_text, call_tool('write_file', path='output/partial.txt',
                                 content=partial_text)),
        ('partial.txt', call_tool('web_search', query='wait')),
    ])
    tool_started = asyncio.Event()
    tool_cancelled = asyncio.Event()

    async def waiting_mcp_call(name: str, arguments: dict[str, object]) -> str:
        tool_started.set()
        try:
            await asyncio.Future()
        finally:
            tool_cancelled.set()
        return str(arguments.get('query', name))

    monkeypatch.setattr(duckduckgo, 'unavailable_reason', lambda: '')
    monkeypatch.setattr(web, '_call_duckduckgo_tool', waiting_mcp_call)

    async def scenario():
        from agent.service import EventStreamRequest, RunLookupRequest

        service, _store = service_at(tmp_path)
        start = StartRunRequest(
            tenant_id='ha-tenant',
            user_id='ha-user',
            session_id='cancel-partial-session',
            run_id='cancel-partial-run',
            request_id='cancel-partial-start',
            request_text=request_text,
        )
        try:
            handle = await service.start_run(start)
            await asyncio.wait_for(tool_started.wait(), timeout=5)
            cancelling = await service.cancel_run(CancelRunRequest(
                tenant_id=start.tenant_id,
                user_id=start.user_id,
                session_id=start.session_id,
                run_id=handle.run_id,
                request_id='cancel-partial-request',
                requested_by=start.user_id,
            ))
            assert cancelling.status.value == 'CANCELLING'
            await asyncio.wait_for(tool_cancelled.wait(), timeout=2)
            events = [event async for event in service.stream_events(EventStreamRequest(
                tenant_id=start.tenant_id,
                user_id=start.user_id,
                session_id=start.session_id,
                run_id=handle.run_id,
                request_id='cancel-partial-events',
            ))]
            lookup = RunLookupRequest(
                tenant_id=start.tenant_id,
                user_id=start.user_id,
                session_id=start.session_id,
                run_id=handle.run_id,
                request_id='cancel-partial-lookup',
            )
            snapshot = await service.get_run(lookup)
            artifacts = await service.list_artifacts(lookup)
            assert snapshot.status.value == 'CANCELLED'
            assert (tmp_path / 'output/partial.txt').read_text() == partial_text
            assert any(
                event.event_type.value == 'task_completed'
                and event.payload.get('verb') == 'write'
                and event.payload.get('verified') is True
                for event in events
            )
            assert not any(
                event.event_type.value == 'task_completed'
                and event.payload.get('verb') == 'search'
                for event in events
            )
            assert snapshot.output is not None
            assert 'output/partial.txt' in snapshot.output.text
            assert '尚未完成的步骤' in snapshot.output.text
            assert len(artifacts) == 1
            assert artifacts[0].reference.startswith('artifact://')
            assert artifacts[0].verified is True
        finally:
            await service.close()

    asyncio.run(scenario())
    assert not provider.replies


def test_semantic_resolution_cannot_expand_explicit_write_scope():
    from agent.cognition.effect_authorization import EffectAuthorization
    state = dict(original_request="在 output/allowed.py 写入代码", resolved_request="修改 agent/runtime.py",
                 artifact_required=True, requested_outcomes=['FILE_MUTATION'],
                 intent_write_targets=['output/allowed.py'])
    auth = EffectAuthorization.from_state(state, state['resolved_request'])
    assert auth.allows_path('output/allowed.py')
    assert not auth.allows_path('agent/runtime.py')


def test_new_artifact_default_scope_is_output_not_provider_chosen_arbitrary_path():
    from agent.cognition.effect_authorization import EffectAuthorization
    state = dict(original_request=CREATE, resolved_request="创建 agent/runtime.py",
                 artifact_required=True, requested_outcomes=['FILE_MUTATION'])
    auth = EffectAuthorization.from_state(state, state['resolved_request'])
    assert auth.allows_path('output/calculator.py')
    assert not auth.allows_path('agent/runtime.py')


def test_command_output_not_truncated_or_claimed_as_file_creation():
    from agent.orchestrator.finalizer import Finalizer
    stdout = 'X' * 600 + '\nTRAILING-OUTPUT\n'
    state = dict(plan=[dict(id='cmd',verb='execute',target='existing.py',status='succeeded',facts={'output':stdout})])
    answer = Finalizer._deterministic_completion_answer(state)
    assert answer.endswith(stdout)
    assert '已生成' not in answer


def test_identity_uses_registered_capabilities(monkeypatch):
    from agent.registry.tool_registry import registry
    from agent.product_context import product_context
    monkeypatch.setattr(registry, '_tools', {'read_file':object(),'write_file':object(),'shell':object()})
    prompt = product_context()
    assert 'TSAgent' in prompt
    assert '读取工作区文件' in prompt and '创建和修改本地文件' in prompt and '执行本地命令' in prompt


def test_pure_answer_needs_no_tools(monkeypatch, tmp_path):
    utterance = '解释一下什么是 SQLite WAL。'
    provider = setup_provider(monkeypatch, [(utterance, intent(utterance, domain='知识',action='explain')),
        ('SQLite WAL', 'SQLite WAL 将变更追加到预写日志，随后通过 checkpoint 合并到数据库文件。')])
    async def scenario():
        service, _ = service_at(tmp_path)
        try:
            result, events = await run_turn(service, utterance, 1)
            assert result.status.value == 'COMPLETED' and '预写日志' in result.output.text
            assert not any(event.event_type.value == 'task_started' for event in events)
        finally:
            await service.close()
    asyncio.run(scenario())
    assert not provider.replies


def test_runtime_preserves_real_stdout_whitespace_and_tail(monkeypatch, tmp_path):
    from agent.compiler.rules import DEFAULT_RULES
    from agent.compiler.tool_selector import Compiler
    from agent.executor.plan_executor import PlanExecutor
    from agent.services.workspace_service import WorkspaceService
    from agent.event_bus import EventBus
    from agent.task import Task
    from agent import sandbox
    monkeypatch.setenv('TSAGENT_ALLOW_LOCAL_EXECUTION', '1')
    monkeypatch.setattr(sandbox, '_check_docker', lambda: False)
    workspace = WorkspaceService.scoped(tmp_path, event_bus=EventBus(), build_index=False)
    expected = '  leading  \n' + 'X' * 600 + '\nTRAILING\n'
    import shlex
    command = 'printf %s ' + shlex.quote(expected)
    compiler = Compiler()
    for rule in DEFAULT_RULES:
        compiler.add_rule(rule)
    plan = compiler.compile(Task.from_dict(dict(id='stdout',verb='execute',target=command,target_type='text')))
    result = asyncio.run(PlanExecutor().execute(plan, workspace=workspace))
    assert not result['_error']
    assert result['_last_output'] == expected
    assert result['output'] == expected
    workspace.close()


def test_missing_execution_evidence_preserves_real_failure_reason():
    from agent.effect_truth import enforce_completion_gate
    state = dict(requested_outcomes=['COMMAND_EXECUTION'], plan=[dict(status='failed',error_code='EXECUTION_ENVIRONMENT_UNAVAILABLE')])
    enforce_completion_gate(state)
    assert state['runtime_failure_code'] == 'EXECUTION_ENVIRONMENT_UNAVAILABLE'
    assert state['runtime_terminal_status'] == 'BLOCKED'


def test_run_previous_artifact_requires_semantic_reference_resolution():
    utterance = '实际运行它'
    engine = IntentEngine()
    provider = ScriptedProvider([(utterance, intent(utterance, resolved='运行 output/calculator.py 并给出输出',
                            relation='reference', outcomes=['CODE_EXECUTION'], domain='运维', action='execute', target='output/calculator.py'))])
    engine._llm = provider
    result = asyncio.run(engine.analyze_async(CognitiveContext(query=utterance, run_results=[
        dict(run_id='previous', status='COMPLETED', goal=CREATE, answer='已创建',
             artifacts=[dict(path='output/calculator.py',digest='verified-digest')], observations=[])
    ])))
    assert result.target == 'output/calculator.py'
    assert result.conversation_relation == 'reference'
    assert not provider.replies


def test_nonzero_process_exit_never_becomes_successful_tool_output(monkeypatch, tmp_path):
    from agent.executor.plan_executor import PlanExecutor
    from agent.services.workspace_service import WorkspaceService
    from agent.task import Task, ExecutionPlan, ExecutionStep
    from agent import sandbox
    monkeypatch.setenv('TSAGENT_ALLOW_LOCAL_EXECUTION', '1')
    monkeypatch.setattr(sandbox, '_check_docker', lambda: False)
    workspace = WorkspaceService.scoped(tmp_path, build_index=False)
    plan = ExecutionPlan(task=Task(id='failing-command'), steps=[ExecutionStep(tool='shell',args={'cmd':'exit 7'},outputs=['output'])])
    result = asyncio.run(PlanExecutor().execute(plan, workspace=workspace))
    assert result['_error']
    assert result['_error_code'] == 'COMMAND_NONZERO_EXIT'
    workspace.close()
