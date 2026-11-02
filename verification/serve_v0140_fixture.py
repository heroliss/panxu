"""Owned files and isolated-profile background review, with a local model peer.

Windows inventory is read only. Two disposable helper processes let the final
executable verify process identities without controlling a user's application.
The app still uses its ordinary simulation backend. No real model or key is used.
Keep this fixture alive during native acceptance; stop with <data-dir>/stop-fixture.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import serve_v0130_fixture as previous
from diskpilot import windows
from tests.prompt_fixture import parse_host_data


fixture = previous.fixture
BACKGROUND_GOAL = '核对隔离清单中的启动项、服务和两个自建进程，提供用途、建议、原因及影响；等待用户勾选。'
_create_server = fixture.create_server
_prepare_native = fixture.prepare_native
_app = None
_helpers = []
_selected = []
_profile = None


def answer(payload):
    context = fixture.stream_peer.context(payload)
    if context.get('goal') != BACKGROUND_GOAL:
        return previous.answer(payload)
    messages = payload.get('messages', [])
    calls = next((row.get('tool_calls', []) for row in reversed(messages) if row.get('role') == 'assistant'), [])
    last = calls[-1].get('function', {}).get('name') if calls else None
    if last is None:
        return fixture.basic.tool('list_items', dict(limit=40))
    if last == 'list_items':
        seen = {}
        for message in messages:
            if message.get('role') != 'tool':
                continue
            data = parse_host_data(message['content'])
            for row in data.get('items', []):
                if row.get('kind') in ('startup', 'service', 'process'):
                    seen[row['id']] = row
        ratings = [5, 4, 4, 2, 3, None, None, None, 4, 5, 1, 4]
        advice = []
        for index, item in enumerate(_selected[:12]):
            assert item['id'] in seen, 'Only observed task targets receive fixture advice'
            score = ratings[index]
            conclusion = 'keep' if score in (1, 2) else 'skip' if index == 6 else 'no_advice' if index == 7 else 'review'
            advice.append(dict(id=item['id'], conclusion=conclusion, cleanup_rating=score, inheritance='none',
                purpose={'startup': '应用登录后自动就绪的入口，是否常用仍由你确认。',
                         'service': '为相关软件提供后台功能，需要考虑依赖和正在进行的工作。',
                         'process': '开发验收专门创建的等待进程，与用户应用分开。'}[item['kind']],
                reason='根据本次只读快照给出测试倾向；较高分不表示系统关键程度低，也不授予操作权限。',
                impact={'startup': '关闭后不再随登录自动打开，需要时仍可手动启动。',
                        'service': '停止当前服务可能中断相关功能；这不会禁用它下次开机启动。',
                        'process': '结束进程可能丢失未保存内容，本次对象是专门创建的测试进程。'}[item['kind']]))
        value = fixture.basic.tool('record_advice', dict(items=advice[:6]))
        extra = fixture.basic.tool('record_advice', dict(items=advice[6:]))['choices'][0]['message']['tool_calls'][0]
        extra['id'] += '_second'
        value['choices'][0]['message']['tool_calls'].append(extra)
        return value
    return fixture.basic.tool('present_findings', dict(
        summary='已形成12项用途与倾向建议，其他项目保留未分析状态。按类型核对勾选，确认时会分别列出关闭启动项、停止服务和结束进程的数量。',
        briefing=dict(highlights=['建议与全部项目两种视图共享勾选。'],
                      cautions=['停止服务不等于禁用其开机启动；高分不代替最终选择。'],
                      coverage=['只涵盖这个隔离任务已取得的对象，没有扩大到整台电脑。']), needs_input=False))


def capture_server(*args, **kwargs):
    global _app
    server = _create_server(*args, **kwargs)
    _app = server.app
    return server


def seed_background():
    for _ in range(2):
        _helpers.append(subprocess.Popen([sys.executable, '-c', 'import time; data=bytearray(32*1024*1024); time.sleep(1800)'],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)))
    processes, _ = windows.process_items()
    owned = [row for row in processes if row['details'].get('pid') in {helper.pid for helper in _helpers}]
    assert len(owned) == 2 and all(row['risk'] != 'protected' for row in owned)
    startups, _ = windows.startup_items()
    services, _ = windows.service_items()
    startup = [row for row in startups if row['risk'] != 'protected' and row.get('actions')][:6]
    service = [row for row in services if row['risk'] != 'protected' and row.get('actions') and row['details'].get('state') == 'Running'][:6]
    assert len(startup) >= 4 and len(service) >= 4, 'This real-Windows fixture requires optional read-only candidates'
    protected = next((row for row in services if row['risk'] == 'protected'), None)
    _selected.extend([*owned, *startup, *service, *([protected] if protected else [])])
    _app.state.publish(_selected, 'v0140-readonly-background-fixture')
    task = _app.harness.create_analysis(dict(section='background', ids=[row['id'] for row in _selected],
        depth='overview', goal=BACKGROUND_GOAL))
    _app.harness.claim(task['id'])
    try:
        task = _app.harness.investigate(task['id'])
    finally:
        _app.harness.release(task['id'])
    assert task['status'] == 'completed' and task['advice_total'] == 12, task['status']
    return dict(background_task_id=task['id'], background_targets=len(_selected),
                kind_counts=dict(Counter(row['kind'] for row in _selected)),
                owned_process_ids=[row['id'] for row in owned])


def prepare_native(source, destination, settings):
    background = seed_background()
    result = _prepare_native(source, destination, settings)
    result.update(background)
    return result


def main():
    global _profile
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--prepare-native-dir', '--prepare-native', type=Path, required=True)
    args, _ = parser.parse_known_args()
    _profile = args.data_dir.absolute()
    previous._stats_path = _profile / 'wire-fixture.json'
    fixture.create_tree = previous.previous.create_tree
    fixture.SEED_GOAL = '验证文件清单的直接说明和常规删除操作；保留所有隔离文件。'
    fixture.create_server = capture_server
    fixture.prepare_native = prepare_native
    fixture.answer = answer
    fixture.stream_peer._delta = previous._delta
    try:
        fixture.main()
    finally:
        checks = []
        for kind, collector in (('startup', windows.startup_items), ('service', windows.service_items), ('process', windows.process_items)):
            rows, _ = collector()
            current = {row['id']: row for row in rows}
            for item in _selected:
                if item['kind'] != kind:
                    continue
                after = current.get(item['id'])
                same = bool(after and after['signature'] == item['signature'])
                if kind == 'service' and same:
                    same = all(after['details'].get(field) == item['details'].get(field) for field in ('state', 'start_mode'))
                checks.append(dict(kind=kind, identity_sha256=hashlib.sha256(item['id'].encode()).hexdigest(), unchanged=same))
        report = dict(read_only_system_observations=True, external_provider_used=False, real_credentials_used=False,
            actual_reclaimed_bytes=0, owned_helpers_alive_before_cleanup=all(helper.poll() is None for helper in _helpers),
            observed_system_objects_unchanged=all(check['unchanged'] for check in checks), checks=checks)
        for helper in _helpers:
            if helper.poll() is None:
                helper.terminate()
            helper.wait(timeout=10)
        if _profile.exists():
            (_profile / 'system-fixture-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
