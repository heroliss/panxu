"""Isolated v0.10 UI peer with real disposable files and seeded assessments.

Only this peer is used as a model. It never approves a plan or controls Windows.
The temporary file tree stays alive until <data-dir>/stop-fixture or Ctrl+C.
--prepare-native-dir creates a *new* profile using SQLite backup, not WAL/file
copies or the source profile's model settings. Keep this process alive while the
native executable uses that profile: both profiles refer to the owned temp tree.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import serve_v050_fixture as basic
import serve_v090_fixture as stream_peer
from diskpilot.llm import ModelAssistant
from diskpilot.scanner import scan_directory
from diskpilot.server import create_server
from tests.prompt_fixture import parse_host_data


SEED_GOAL = '验证0.10综合评分和目录继承：先给用途和倾向，所有文件保持原样，等待审阅。'
PARENT = '应用缓存（整体5分）'
KEEP = '重要资料（保留1分）.txt'
SKIP = '稍后再看（跳过）.bin'
NO_ADVICE = '用途未明（不给建议）.bin'
UNSCORED = '已查看（尚未评分）.bin'
BARRIER = '暂不判断的目录'
HIGH = '旧安装包（倾向清理4分）.zip'
NEUTRAL = '可留可清（中立3分）.zip'
LOW = '仍需使用（倾向保留2分）.zip'
UNANALYSED = '未分析的文件.bin'


def _observed(payload):
    rows = {}
    for message in payload.get('messages', []):
        if message.get('role') == 'tool':
            try:
                result = parse_host_data(message['content'])
            except (TypeError, ValueError, KeyError):
                continue
            for row in result.get('items', []) if isinstance(result, dict) else []:
                if isinstance(row, dict) and isinstance(row.get('id'), str) and row.get('kind') in ('file', 'directory'):
                    rows[row['id']] = row
    return list(rows.values())


def _advice(row):
    name = row['name']
    definitions = {
        PARENT: ('review', 5, 'subtree', '演示用可再生成副本；目录中另有明确保留项，须分别处理。'),
        KEEP: ('keep', 1, 'none', '这份资料仍有价值，明确保留，不随父目录的清理倾向选择。'),
        SKIP: ('skip', None, 'none', '这次暂时跳过，之后需要时再判断。'),
        NO_ADVICE: ('no_advice', None, 'none', '目前不能确认具体用途，明确不给去留建议。'),
        UNSCORED: ('review', None, 'none', '已经查看，但现有线索不足以给出倾向分。'),
        BARRIER: ('no_advice', None, 'none', '这个目录的用途未确认，不能让上级倾向覆盖这里的内容。'),
        HIGH: ('review', 4, 'none', '这是可丢弃的测试安装包副本，倾向清理，仍由用户选择。'),
        NEUTRAL: ('review', 3, 'none', '继续留用与清理各有理由，已分析后保持中立。'),
        LOW: ('keep', 2, 'none', '当前仍可能需要这份副本，倾向继续保留。'),
    }
    if name not in definitions:
        return None
    conclusion, rating, inheritance, reason = definitions[name]
    return dict(id=row['id'], conclusion=conclusion, cleanup_rating=rating, inheritance=inheritance,
                purpose='隔离夹具中的测试文件或目录，用于核对建议与个人选择。', reason=reason,
                impact='本版本只记录模拟结果，实际文件保持不变。')


def answer(payload):
    definitions = {tool.get('function', {}).get('name') for tool in payload.get('tools', [])}
    if 'connection_probe' in definitions:
        return basic.answer(payload)
    context = stream_peer.context(payload)
    messages = payload.get('messages', [])
    if context.get('last_result'):
        return basic.tool('present_findings', {'summary': '已核对模拟记录，实际文件均保持原样。', 'needs_input': False})
    if len(messages) == 2:
        return basic.tool('list_items', {'limit': 40})
    previous_calls = messages[-2].get('tool_calls', []) if len(messages) > 2 else []
    previous = previous_calls[-1].get('function', {}).get('name') if previous_calls else None
    if previous == 'discover_tools':
        return basic.tool('list_items', {'limit': 40})
    rows = _observed(payload)
    if previous == 'list_items':
        if not rows:
            return basic.tool('present_findings', {'summary': '当前选择没有待分析的测试内容。', 'needs_input': True})
        named = [row for row in rows if _advice(row) is not None]
        selected = named if context.get('goal') == SEED_GOAL else rows[:20]
        return basic.tool('inspect_items', {'ids': [row['id'] for row in selected[:20]]})
    if previous == 'inspect_items':
        suggestions = [value for row in rows if (value := _advice(row)) is not None]
        if context.get('goal') != SEED_GOAL:
            suggestions = suggestions or [dict(id=row['id'], conclusion='review', cleanup_rating=4,
                inheritance='none', purpose='用户选中的隔离测试副本。', reason='根据测试目标倾向清理，最后由用户选择。',
                impact='仅记录模拟，不会实际删除。') for row in rows[:20]]
        if suggestions:
            return basic.tool('record_advice', {'items': suggestions[:30]})
    if previous == 'record_advice':
        choices = {choice['id'] for choice in context.get('user_choices', [])}
        targets = [row for row in rows if row['name'] == HIGH and row['id'] not in choices]
        if not targets and context.get('goal') != SEED_GOAL:
            targets = [row for row in rows if row['kind'] == 'file' and row['id'] not in choices
                       and row['name'] not in (KEEP, SKIP, NO_ADVICE, UNSCORED, LOW, UNANALYSED)][:2]
        if targets:
            return basic.tool('propose_plan', {'summary': '已形成用途与倾向建议。请审阅具体文件，选择后才会记录模拟。',
                'items': [dict(id=row['id'], action='delete', reason='测试副本倾向清理，由用户确认去留。',
                               impact='只模拟记录，不改变实际文件。') for row in targets]})
    return basic.tool('present_findings', {'summary': '建议已保存；跳过、不给建议和未分析的内容各自保留状态，没有执行操作。',
                                           'needs_input': False})


def create_tree(root, catalog_size):
    scope = root / '评分与目录测试'
    parent = scope / PARENT
    barrier = parent / BARRIER
    barrier.mkdir(parents=True)
    for name in (KEEP, SKIP, NO_ADVICE, UNSCORED):
        (parent / name).write_bytes((name + '\nKeep the original bytes.').encode() * 4)
    (barrier / '下级未分析.bin').write_bytes(b'Inherited advice must not cross the explicit barrier.')
    for index in range(max(70, min(catalog_size, 1000))):
        (parent / f'缓存副本-{index + 1:03d}.bin').write_bytes(b'fixture\n')
    for name in (HIGH, NEUTRAL, LOW):
        (scope / name).write_bytes((name + '\nDisposable local fixture.').encode() * 8)
    (scope / UNANALYSED).write_bytes(b'No model advice has been recorded for this file.')
    return scope, parent


def _new_profile(path):
    path = path.absolute()
    if path.exists() and (not path.is_dir() or any(path.iterdir())):
        raise ValueError('Fixture profiles must be new or empty; existing user data is never overwritten.')
    path.mkdir(parents=True, exist_ok=True)
    return path


def prepare_native(source, destination, settings):
    """Consistent per-DB snapshots before the source accepts any UI requests."""
    destination = _new_profile(destination)
    for name in ('simulation.sqlite3', 'scan_index.sqlite3', 'developer_trace.sqlite3'):
        with closing(sqlite3.connect((source / name).as_uri() + '?mode=ro', uri=True)) as origin:
            with closing(sqlite3.connect(destination / name)) as target:
                origin.backup(target)
    # Deliberately do not copy source model_settings.json, lease, WAL or SHM.
    assistant = ModelAssistant(destination)
    assistant.save_settings(settings)
    guard = assistant.public_settings()
    assert guard['provider'] == 'custom' and guard['model'] == 'local-acceptance-fixture' and not guard['has_key']
    return dict(path=str(destination), provider=guard['provider'], model=guard['model'],
                protocol=guard['protocol'], has_key=guard['has_key'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--catalog-size', type=int, default=70)
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--provider-port', type=int, default=0)
    parser.add_argument('--prepare-native-dir', '--prepare-native', type=Path)
    parser.add_argument('--hold-marker', type=Path)
    args = parser.parse_args()
    profile = _new_profile(args.data_dir)
    stats = dict(requests=0, external_provider_used=False, real_credentials_used=False,
                 automatically_approved=False, real_files_unchanged=None)
    stream_peer.HOLD_MARKER = args.hold_marker.absolute() if args.hold_marker else None
    with tempfile.TemporaryDirectory(prefix='panxu-v0100-') as tmp:
        scope, parent = create_tree(Path(tmp), args.catalog_size)
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in scope.rglob('*') if path.is_file()}

        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                if self.path not in ('/probe', '/keepalive', '/health'):
                    self.send_error(404)
                    return
                basic.write_response(self, dict(status='ok', local_fixture=True, **stats), {})

            def do_POST(self):
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                    if self.path != '/v1/chat/completions' or not 0 < length <= 192 * 1024:
                        self.send_error(400)
                        return
                    payload = json.loads(self.rfile.read(length))
                    stats['requests'] += 1
                    value, status = answer(payload), 200
                except (ValueError, KeyError, TypeError):
                    value, status = {'error': {'message': 'invalid_fixture_request'}}, 400
                stream_peer.write_response(self, value, payload if 'payload' in locals() else {}, status)

        provider = ThreadingHTTPServer(('127.0.0.1', args.provider_port), Provider)
        threading.Thread(target=provider.serve_forever, daemon=True).start()
        server = None
        stopping = threading.Event()
        try:
            server = create_server(profile, port=args.port)
            app = server.app
            report = scan_directory(str(scope))
            if report['summary']['status'] != 'completed':
                raise RuntimeError('The owned fixture tree must be fully observable.')
            app.state.publish(report['items'], 'v0100-owned-files')
            settings = dict(provider='custom', protocol='chat_completions',
                base_url=f'http://127.0.0.1:{provider.server_port}/v1', model='local-acceptance-fixture',
                send_paths=False, stream=True)
            app.assistant.save_settings(settings)
            app.trace.update(enabled=True)
            task = app.harness.create_analysis(dict(section='files', scope=str(scope), depth='overview', goal=SEED_GOAL))
            app.harness.claim(task['id'])
            try:
                task = app.harness.investigate(task['id'])
            finally:
                app.harness.release(task['id'])
            if task['status'] != 'awaiting_review' or not task['plan'] or task['last_result']:
                raise RuntimeError('Seed advice must stop at an unapproved reviewable plan: ' + task['status'])
            page = app.catalog.page(limit=100)
            counts = Counter(row['assessment']['state'] for row in page['items'])
            parent_id = next(row['id'] for row in page['items'] if row['path'] == str(parent))
            if counts['inherited'] < 70 or not all(counts[state] for state in ('explicit', 'skipped', 'no_advice', 'unanalysed')):
                raise RuntimeError('Expected assessment states were not seeded: ' + str(dict(counts)))
            info = dict(app_url=f'http://127.0.0.1:{server.server_port}',
                provider_url=f'http://127.0.0.1:{provider.server_port}/v1',
                probe_url=f'http://127.0.0.1:{provider.server_port}/probe', scope=str(scope),
                parent_id=parent_id, task_id=task['id'], status=task['status'],
                item_count=page['total'], assessment_counts=dict(counts),
                file_ids={row['name']: row['id'] for row in page['items'] if row['name'] in
                          (PARENT, KEEP, SKIP, NO_ADVICE, UNSCORED, BARRIER, HIGH, NEUTRAL, LOW, UNANALYSED)})
            if args.prepare_native_dir:
                info['native_profile'] = prepare_native(profile, args.prepare_native_dir, settings)
            (profile / 'fixture-info.json').write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')

            def watch():
                while not stopping.wait(.2):
                    if (profile / 'stop-fixture').exists():
                        server.shutdown()
                        return
            threading.Thread(target=watch, daemon=True).start()
            print(json.dumps(info, ensure_ascii=False), flush=True)
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            stopping.set()
            if server is not None:
                server.server_close()
                server.app.close()
            provider.shutdown()
            provider.server_close()
            stats['real_files_unchanged'] = all(path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest
                                              for path, digest in before.items())
            (profile / 'fixture-report.json').write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
