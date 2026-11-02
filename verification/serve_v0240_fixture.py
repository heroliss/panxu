"""Owned history/workflow profiles with an offline peer; no real cleanup.

History samples use the normal simulation stores and explicit test-only fault
hooks. Fake background registrations are never installed into Windows, so the
unmodified final EXE must reject them when its read-only identity check runs.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from diskpilot.actions import ActionRuntime
from diskpilot.llm import ModelAssistant
from diskpilot.scanner import scan_directory
from diskpilot.server import create_server
from diskpilot.tasks import now
from diskpilot.windows import _item
from serve_v0231_fixture import new_profile, response
from serve_v090_fixture import write_response

NAMES = ('retained-a.tmp', 'restored-b.tmp', 'unverified.tmp', 'failed.tmp',
         'cancelled.tmp', 'checker-unverified.tmp', 'native-clean.tmp',
         'native-notes.txt', 'checker-clean.tmp', 'checker-notes.txt',
         'fake-background.exe')


def hashes(scope, names=NAMES):
    return {name: hashlib.sha256((scope / name).read_bytes()).hexdigest() for name in names}


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from strings(child)


def seed(app, scope, bulk_history=0):
    h, stop = app.harness, threading.Event()
    app.state.publish(scan_directory(str(scope))['items'], 'v0240-owned-files')
    files = {row['name']: row for row in app.state.snapshot()['items'] if row['kind'] == 'file'}
    bulk = dict(count=bulk_history)
    if bulk_history:
        rows = [files[f'bulk-{index:03d}.tmp'] for index in range(bulk_history)]
        prepared = h.prepare_assessment_plan([row['id'] for row in rows])
        key, version = prepared['task']['id'], prepared['plan']['version']
        h.validate_approval(key, version, prepared['picked_ids'])
        h.claim(key, execute=True)
        completed = h.investigate(key, execute=True)
        result = completed['last_result']
        if result['succeeded'] != bulk_history or result['actual_reclaimed_bytes'] != 0:
            raise AssertionError('Bulk owned simulation seed did not finish.')
        receipts = {row['id']: row['batch_id'] for row in result['items']}
        bulk.update(task_id=key, plan_version=version,
            items=[dict(name=row['name'], item_id=row['id'], batch_id=receipts[row['id']]) for row in rows])
    prepared = app.state.prepare([files[name]['id'] for name in NAMES[:2]])
    mixed = app.state.execute(prepared['id'])['batch']
    restored = next(row for row in app.recovery.list(status='retained')['items']
                    if row['name'] == 'restored-b.tmp')
    recovery_plan = app.recovery.prepare([restored['id']], 'restore')
    app.recovery.execute(recovery_plan['id'], recovery_plan['confirmation'])

    attempts = {}
    for label, name, phase in (('unverified', 'unverified.tmp', 'after'),
                               ('checker_unverified', 'checker-unverified.tmp', 'after'),
                               ('failed', 'failed.tmp', 'before'),
                               ('cancelled', 'cancelled.tmp', None)):
        runtime = ActionRuntime(app.state, fault=lambda item, stage, wanted=phase: stage == wanted)
        cancel = threading.Event()
        if label == 'cancelled':
            cancel.set()
        task_id = 'owned-v0240-' + label
        result = runtime.run(task_id, 1, [dict(files[name], action='delete')], cancel=cancel)
        attempts[label] = dict(task_id=task_id, item_id=files[name]['id'],
            status=result['items'][0]['status'], batch_id=result['items'][0].get('batch_id'))

    provenance = dict(source='llm', provider='custom', model='offline-v0240-peer',
                      protocol='chat_completions', generated_at=now())

    def file_analysis(label, clean_name, notes_name):
        rows = [files[clean_name], files[notes_name]]
        task = h.create_analysis(dict(section='files', ids=[r['id'] for r in rows],
            reanalyze=True, goal='测试文件清单与操作记录流程（' + label + '）'))
        h.tool(task['id'], 'record_advice', {'items': [dict(id=row['id'],
            conclusion='keep' if row['name'].endswith('.txt') else 'review',
            cleanup_rating=1 if row['name'].endswith('.txt') else 5,
            purpose='个人笔记样本。' if row['name'].endswith('.txt') else '结束工作的临时文件样本。',
            reason='用户创建内容应保留。' if row['name'].endswith('.txt') else '仅用于隔离模拟验收，可由用户选择。',
            impact='删除会丢失笔记。' if row['name'].endswith('.txt') else '需要时可重新生成；本次不会改动实际文件。',
            impact_rating=4 if row['name'].endswith('.txt') else 1) for row in rows]},
            cancel=stop, provenance=provenance)
        h.tool(task['id'], 'propose_plan', dict(summary='测试建议：仅清理所选临时文件，保留笔记。',
            items=[dict(id=rows[0]['id'], action='delete', reason='仅用于隔离测试。',
                        impact='本构建只记录模拟结果。', impact_rating=1)]), cancel=stop, provenance=provenance)
        return task['id']

    tasks = dict(native_files=file_analysis('原生窗口', 'native-clean.tmp', 'native-notes.txt'),
                 checker_files=file_analysis('接口检查', 'checker-clean.tmp', 'checker-notes.txt'))
    background = [
        _item('startup', 'owned-v0240-startup', '测试启动助手', '隔离夹具', '开机启动',
            'disable_startup', dict(synthetic=False, state='已注册', registry_location='owned-fixture-only'),
            path=str(scope / 'fake-background.exe')),
        _item('service', 'owned-v0240-service', '测试后台服务', '隔离夹具', '后台服务',
            'stop_service', dict(synthetic=False, state='Running', start_mode='Manual'),
            path=str(scope / 'fake-background.exe'))]
    task = h.create(preset='background', goal='测试后台建议与失败核验，不安装或改变真实服务。')
    with patch('diskpilot.tasks.inventory', return_value=dict(items=background, warnings=[], summary={'read_only': True})):
        h.tool(task['id'], 'collect_apps', {'section': 'background'}, cancel=stop)
    h.tool(task['id'], 'record_advice', {'items': [dict(id=row['id'], conclusion='review', cleanup_rating=5,
        purpose='仅存在于隔离库存的后台样本。', reason='测试用户选择和运行结果。',
        impact='真实系统中不存在此注册，最终身份核验应拒绝执行。', impact_rating=2) for row in background]},
        cancel=stop, provenance=provenance)
    h.tool(task['id'], 'propose_plan', dict(summary='测试后台清单；不存在的系统身份应核验失败。',
        items=[dict(id=row['id'], action=row['actions'][0], reason='隔离样本，不是真实注册。',
                    impact='最终EXE只读核对后应拒绝模拟提交。', impact_rating=2) for row in background]),
        cancel=stop, provenance=provenance)
    tasks['native_background'] = task['id']

    tools = {}
    # The completed result must genuinely occur after the cancelled event;
    # restart reconciliation follows event time, not callback arrival time.
    for label, tool_id, status in (('cancelled', 'uv_cache', 'cancelled'),
                                    ('completed', 'thumbnail_cache', 'completed'),
                                    ('awaiting', 'recycle_bin', 'awaiting_review')):
        parameters = {'volume': scope.anchor.rstrip('\\/')} if tool_id == 'recycle_bin' else {}
        plan = app.post('/api/cleanup-tools/prepare', dict(tool_id=tool_id,
            parameters=parameters, source={'task_id': tasks['native_files']}))
        if status == 'completed':
            app.post('/api/cleanup-tools/execute', dict(plan_id=plan['id'], version=plan['version']))
        elif status == 'cancelled':
            app.post('/api/cleanup-tools/cancel', dict(plan_id=plan['id'], version=plan['version']))
        tools[label] = plan['id']
    return dict(tasks=tasks, tool_plans=tools, attempts=attempts, bulk_history=bulk, mixed_batch_id=mixed['id'],
                restored_entry_id=restored['id'], file_ids={name: row['id'] for name, row in files.items()},
                background_ids=[row['id'] for row in background])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--native-profile', type=Path, required=True)
    parser.add_argument('--port', type=int, default=14774)
    parser.add_argument('--bulk-history', type=int, default=0,
                        help='Seed 0..100 owned files in one confirmed task/version for grouped history.')
    args = parser.parse_args()
    if not 0 <= args.bulk_history <= 100:
        parser.error('--bulk-history must be between 0 and 100.')
    profile, native = new_profile(args.data_dir), new_profile(args.native_profile)
    if profile.is_relative_to(native) or native.is_relative_to(profile):
        raise ValueError('Use separate new source and native profiles.')
    scope = Path(tempfile.mkdtemp(prefix='panxu-owned-v0240-'))
    owned_names = (*NAMES, *(f'bulk-{index:03d}.tmp' for index in range(args.bulk_history)))
    for name in owned_names:
        (scope / name).write_bytes(('Owned unchanged workflow sample: ' + name).encode())
    before = hashes(scope, owned_names)
    stats, lock = dict(requests=0, errors=[], path_leaks=0), threading.Lock()

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            if self.path != '/stats':
                self.send_error(404); return
            with lock:
                body = json.dumps(stats).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get('Content-Length', '0'))
            if self.path != '/v1/chat/completions' or not 0 < length <= 256 * 1024:
                self.send_error(400); return
            try:
                payload = json.loads(self.rfile.read(length))
                with lock:
                    stats['requests'] += 1
                    if any(str(scope).casefold() in value.casefold() for value in strings(payload)):
                        stats['path_leaks'] += 1
                answer = response(payload)
            except Exception as exc:
                with lock:
                    stats['errors'].append(type(exc).__name__)
                self.send_error(400); return
            write_response(self, answer, payload)

    provider = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    threading.Thread(target=provider.serve_forever, daemon=True).start()
    server = create_server(profile, port=args.port)
    serving = False
    try:
        settings = dict(provider='custom', protocol='chat_completions', model='offline-v0240-peer',
            base_url=f'http://127.0.0.1:{provider.server_port}/v1', send_paths=False, stream=True)
        app = server.app
        app.assistant.save_settings(settings)
        app.trace.update(enabled=True)
        seeded = seed(app, scope, args.bulk_history)
        for name in ('simulation.sqlite3', 'scan_index.sqlite3', 'developer_trace.sqlite3'):
            with closing(sqlite3.connect((profile / name).as_uri() + '?mode=ro', uri=True)) as origin:
                with closing(sqlite3.connect(native / name)) as target:
                    origin.backup(target)
        ModelAssistant(native).save_settings(settings)
        metadata = dict(app_url=f'http://127.0.0.1:{server.server_port}', provider_url=settings['base_url'],
            provider_stats=f'http://127.0.0.1:{provider.server_port}/stats', scope=str(scope),
            fixture_hashes=before, native_profile=str(native), **seeded,
            external_provider_used=False, real_credentials_used=False,
            limits=['Seeded model-origin advice uses an explicit offline fixture provenance, not a cloud inference.',
                    'Fake background objects are expected to fail final system identity validation.'])
        destination = profile.parent / 'fixture-meta.json'
        destination.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
        threading.Thread(target=server.serve_forever, daemon=True).start()
        serving = True
        print(json.dumps(dict(metadata=str(destination), **metadata), ensure_ascii=False), flush=True)
        while not (profile / 'stop-fixture').exists():
            threading.Event().wait(.3)
        if hashes(scope, owned_names) != before:
            raise AssertionError('Owned sample bytes changed.')
        print(json.dumps(dict(stopped=True, real_fixture_files_unchanged=True)), flush=True)
    finally:
        if serving:
            server.shutdown()
        server.server_close(); server.app.close()
        provider.shutdown(); provider.server_close()


if __name__ == '__main__':
    main()
