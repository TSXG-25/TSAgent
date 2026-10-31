"""Gate A propagation with scripted semantics, NOT a language accuracy eval."""
import asyncio
import hashlib

import pytest

from tests.test_ha_r1_harness import intent, run_turn, service_at, setup_provider


@pytest.mark.parametrize('utterance', [
    '帮我写个计算器', '做个简单网页', '实现一个 CSV 转换工具',
    '给我弄个 hello world 项目', '整一个能读 JSON 的脚本',
])
def test_artifact_gate_overrides_math_and_no_execution(monkeypatch, tmp_path, utterance):
    # Deliberately contradictory domain/exec flags: artifact semantics must win.
    semantic = intent(utterance, artifact=True, domain='数学', action='calculate',
        outcomes=['FILE_MUTATION'])
    assert semantic['requires_execution'] is True
    provider = setup_provider(monkeypatch, [
        (utterance, semantic),
        ('用户需求', {'tasks': [dict(id='write', verb='write',
            target='output/probe.txt', target_type='file', goal=utterance,
            inputs={'content': 'OFFLINE CONTRACT PROBE'})]}),
    ])

    async def scenario():
        service, _ = service_at(tmp_path)
        try:
            snapshot, events = await run_turn(service, utterance, 1)
            assert snapshot.status.value == 'COMPLETED', snapshot
            assert (tmp_path/'output/probe.txt').read_text() == 'OFFLINE CONTRACT PROBE'
            assert snapshot.artifacts
            assert str(tmp_path/'output/probe.txt') in snapshot.output.text
            assert any(e.event_type.value == 'task_completed' for e in events)
        finally:
            await service.close()
    asyncio.run(scenario())
    assert not provider.replies


def test_two_requested_reports_require_and_publish_two_distinct_files(
    monkeypatch,
    tmp_path,
):
    request = '做两份报告给我'
    semantics = intent(request, artifact=True, outcomes=['FILE_MUTATION'])
    semantics['delivery_requirements'] = [{
        'id': 'reports',
        'kind': 'file',
        'quantity': 2,
        'target': 'output/',
        'acceptance_criteria': [],
    }]
    provider = setup_provider(monkeypatch, [
        (request, semantics),
        ('用户需求', {'tasks': [
            dict(
                id='write-report-a',
                verb='write',
                target='output/report-a.md',
                target_type='file',
                goal='创建第一份报告',
                inputs={'content': '# 报告 A\n\n离线交付 A。\n'},
            ),
            dict(
                id='write-report-b',
                verb='write',
                target='output/report-b.md',
                target_type='file',
                goal='创建第二份报告',
                inputs={'content': '# 报告 B\n\n离线交付 B。\n'},
            ),
        ]}),
    ])

    async def scenario():
        service, _store = service_at(tmp_path)
        try:
            snapshot, events = await run_turn(service, request, 1)
            assert snapshot.status.value == 'COMPLETED', snapshot
            paths = [tmp_path / 'output/report-a.md', tmp_path / 'output/report-b.md']
            assert [path.read_text() for path in paths] == [
                '# 报告 A\n\n离线交付 A。\n',
                '# 报告 B\n\n离线交付 B。\n',
            ]
            assert len(snapshot.artifacts) == 2
            assert {item.digest for item in snapshot.artifacts} == {
                hashlib.sha256(path.read_bytes()).hexdigest() for path in paths
            }
            assert all(str(path) in snapshot.output.text for path in paths)
            completed_writes = [
                event for event in events
                if event.event_type.value == 'task_completed'
                and event.payload.get('verb') == 'write'
                and event.payload.get('verified') is True
            ]
            assert len(completed_writes) == 2
        finally:
            await service.close()

    asyncio.run(scenario())
    assert not provider.replies


def test_two_report_goal_reports_one_verified_file_when_second_write_fails(
    monkeypatch,
    tmp_path,
):
    request = '做两份报告给我'
    semantics = intent(request, artifact=True, outcomes=['FILE_MUTATION'])
    semantics['delivery_requirements'] = [{
        'id': 'reports',
        'kind': 'file',
        'quantity': 2,
        'target': 'output/',
        'acceptance_criteria': [],
    }]
    blocked_target = tmp_path / 'output/report-b.md'
    blocked_target.mkdir(parents=True)
    provider = setup_provider(monkeypatch, [
        (request, semantics),
        ('用户需求', {'tasks': [
            dict(
                id='write-report-a',
                verb='write',
                target='output/report-a.md',
                target_type='file',
                goal='创建第一份报告',
                inputs={'content': '# 报告 A\n\n离线交付 A。\n'},
            ),
            dict(
                id='write-report-b',
                verb='write',
                target='output/report-b.md',
                target_type='file',
                goal='创建第二份报告',
                inputs={'content': '# 报告 B\n\n离线交付 B。\n'},
            ),
        ]}),
    ])

    async def scenario():
        service, _store = service_at(tmp_path)
        try:
            snapshot, events = await run_turn(service, request, 1)
            report_a = tmp_path / 'output/report-a.md'
            assert snapshot.status.value != 'COMPLETED'
            assert report_a.read_text() == '# 报告 A\n\n离线交付 A。\n'
            assert blocked_target.is_dir()
            assert len(snapshot.artifacts) == 1
            assert snapshot.artifacts[0].digest == hashlib.sha256(
                report_a.read_bytes()
            ).hexdigest()
            assert '目标尚未完成' in snapshot.output.text
            assert 'deliverable:reports:2' in snapshot.output.text
            assert '已完成并保留的文件' in snapshot.output.text
            assert 'output/report-a.md' in snapshot.output.text
            assert any(
                event.event_type.value == 'task_completed'
                and event.payload.get('task_id') == 'write-report-a'
                and event.payload.get('verified') is True
                for event in events
            )
            assert not any(
                event.event_type.value == 'task_completed'
                and event.payload.get('task_id') == 'write-report-b'
                and event.payload.get('status') == 'succeeded'
                for event in events
            )
        finally:
            await service.close()

    asyncio.run(scenario())
    assert not provider.replies


def test_bad_apple_player_discloses_missing_asset_and_prohibited_execution(
    monkeypatch,
    tmp_path,
):
    request = (
        '做一个 Bad Apple 本地播放器，交付程序和使用说明；'
        '素材缺失就说明缺口，不要下载素材，也不要运行程序。'
    )
    semantics = intent(
        request,
        artifact=True,
        outcomes=['FILE_MUTATION'],
    )
    semantics['prohibited_effects'] = ['CODE_EXECUTION']
    semantics['delivery_requirements'] = [
        {
            'id': 'player',
            'kind': 'file',
            'quantity': 1,
            'target': 'output/bad_apple_player.html',
            'acceptance_criteria': [],
        },
        {
            'id': 'usage',
            'kind': 'file',
            'quantity': 1,
            'target': 'output/README.md',
            'acceptance_criteria': [],
        },
    ]
    provider = setup_provider(monkeypatch, [
        (request, semantics),
        ('用户需求', {'tasks': [
            dict(
                id='write-player',
                verb='write',
                target='output/bad_apple_player.html',
                target_type='file',
                goal='创建本地 Bad Apple 播放器',
                inputs={
                    'content': (
                        '<!doctype html><title>Bad Apple 本地播放器</title>\n'
                        '<video controls src="assets/bad_apple.mp4"></video>\n'
                    ),
                },
            ),
            dict(
                id='write-usage',
                verb='write',
                target='output/README.md',
                target_type='file',
                goal='说明素材缺失和使用方式',
                inputs={
                    'content': (
                        '# 使用说明\n\n'
                        '素材尚未提供或下载。请把有权使用的视频文件放到 '
                        '`output/assets/bad_apple.mp4`。\n'
                        '播放器已生成但未运行；播放效果未验证。\n'
                    ),
                },
            ),
        ]}),
    ])

    async def scenario():
        service, _store = service_at(tmp_path)
        try:
            snapshot, events = await run_turn(service, request, 1)
            assert snapshot.status.value == 'COMPLETED', snapshot
            player = tmp_path / 'output/bad_apple_player.html'
            usage = tmp_path / 'output/README.md'
            assert player.is_file() and usage.is_file()
            assert not (tmp_path / 'output/assets/bad_apple.mp4').exists()
            assert not any(
                event.event_type.value == 'task_completed'
                and event.payload.get('verb') == 'execute'
                and event.payload.get('status') == 'succeeded'
                for event in events
            )
            assert '素材尚未提供' in usage.read_text()
            assert '未运行' in usage.read_text()
            assert '未验证' in snapshot.output.text
        finally:
            await service.close()

    asyncio.run(scenario())
    assert not provider.replies
