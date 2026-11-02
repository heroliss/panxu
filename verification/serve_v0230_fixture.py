"""Owned profiles and a loopback SSE peer for 0.23 orchestration acceptance.

This deterministic peer tests the harness protocol, not model intelligence.
It requests only owned file metadata/hashes and fixed read-only diagnostics.
It never requests a shell, program launch, cloud service, or live operation.
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from diskpilot.server import create_server
from diskpilot.scanner import scan_directory
from tests.prompt_fixture import parse_host_data
from serve_v090_fixture import write_response

NAMES = ('duplicate-a.bin', 'duplicate-b.bin', 'old-cache.tmp', 'keep.txt')


def call(name, arguments):
    return {'choices': [{'message': {'role': 'assistant', 'content': '正在核对隔离样本，当前仅记录模拟操作。',
        'tool_calls': [{'id': 'owned-' + name, 'type': 'function',
                        'function': {'name': name, 'arguments': json.dumps(arguments, ensure_ascii=False)}}]},
        'finish_reason': 'tool_calls'}]}


def result_messages(payload):
    names = {entry['id']: entry['function']['name'] for message in payload['messages'] if message['role'] == 'assistant'
             for entry in message.get('tool_calls', [])}
    result = []
    for message in payload['messages']:
        if message['role'] == 'tool':
            result.append((names.get(message['tool_call_id']), parse_host_data(message['content'])))
    return result


def findings():
    return call('present_findings', dict(summary='隔离范围调查完成；建议和操作记录已保存，未扫描范围仍未知。',
        needs_input=False, briefing={'coverage': ['仅检查指定测试目录及固定只读系统配置，没有执行真实清理。']}))


def answer(payload):
    context = parse_host_data(payload['messages'][1]['content'])
    goal = context.get('goal', '')
    results = result_messages(payload)
    last, value = results[-1] if results else (None, {})
    if isinstance(value, dict) and value.get('error'):
        raise ValueError('Owned peer received a tool error: ' + str(last))
    # A continued completed task uses persisted context and does not recollect.
    if 'fixture-resume' in goal or 'fixture-duplicates' in goal and context.get('advice_progress', {}).get('recorded', 0):
        return findings()
    if 'fixture-system' in goal:
        if last is None:
            return call('describe_cleanup_tool', {'tool_id': 'unity_cache'})
        if last == 'describe_cleanup_tool':
            return call('inspect_cleanup_tool', {'tool_id': 'unity_cache', 'parameters': {'project_path': {'scope_id': 'root'}}})
        if last == 'inspect_cleanup_tool':
            return call('record_specialist_suggestions', {'items': [dict(id='owned-system', title='检查项目缓存',
                explanation='只检查隔离目录的项目缓存，适用性未知时不把未知当作零。', impact='选择只进入工具，确认之前不会操作。',
                options=[dict(id='open', label='查看工具', kind='tool', tool_id='unity_cache', parameters={'project_path': {'scope_id': 'root'}}),
                         dict(id='skip', label='暂不清理', kind='answer')])]})
        return findings()
    if 'fixture-custom' in goal:
        if last is None:
            return call('draft_user_tool', {'type': 'files', 'title': '隔离临时文件',
                'description': '仅保存用户选定目录中的临时文件条件。', 'scope_ids': ['root'],
                'rules': {'includes': ['*.tmp'], 'recursive': True, 'min_age_days': 0}})
        if last == 'draft_user_tool':
            return call('record_specialist_suggestions', {'items': [dict(id='owned-custom', title='保存日常清理规则',
                explanation='先检查草案条件，再由用户保存和选择本次文件。', impact='不会自动批准或执行。',
                options=[dict(id='open', label='查看草案', kind='user_tool', draft_id=value['draft_id']),
                         dict(id='skip', label='暂不保存', kind='answer')])]})
        return findings()
    if 'fixture-duplicates' in goal:
        if last is None:
            return call('start_duplicate_scan', {'scope_ids': ['root'], 'max_entries': 100, 'max_bytes': 100000})
        if last == 'start_duplicate_scan':
            return call('read_duplicate_groups', {'job_id': value['id'], 'limit': 1})
        if last == 'read_duplicate_groups':
            return call('read_duplicate_members', {'job_id': value['job_id'], 'group_id': value['groups'][0]['group_id'], 'limit': 10})
        if last == 'read_duplicate_members':
            return call('record_advice', {'items': [dict(id=row['id'], conclusion='review', cleanup_rating=4,
                purpose='隔离测试的内容相同副本。', reason='SHA256检查确认相同内容，仍由用户选定保留位置。',
                impact='模拟操作不会改变真实文件。', impact_rating=2) for row in value['items']]})
        return findings()
    return findings()


def new_profile(path):
    path = path.resolve()
    if not path.is_relative_to(ROOT / 'verification') or path.exists():
        raise ValueError('Use new profiles under verification; existing profiles are never overwritten.')
    path.mkdir(parents=True)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--native-profile', type=Path, required=True)
    parser.add_argument('--port', type=int, default=3572)
    args = parser.parse_args()
    profile, native = new_profile(args.data_dir), new_profile(args.native_profile)
    scope = Path(tempfile.mkdtemp(prefix='panxu-owned-v0230-'))
    for name in NAMES:
        content = b'Owned identical duplicate fixture' if name.endswith('.bin') else ('Owned ' + name).encode()
        (scope / name).write_bytes(content)
    hashes = {name: hashlib.sha256((scope / name).read_bytes()).hexdigest() for name in NAMES}
    stats = {'requests': 0, 'errors': [], 'path_leaks': 0}
    lock = threading.Lock()

    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for entry in value.values():
                yield from strings(entry)
        elif isinstance(value, list):
            for entry in value:
                yield from strings(entry)

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            if self.path != '/stats':
                self.send_error(404); return
            with lock:
                raw = json.dumps(stats).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)

        def do_POST(self):
            length = int(self.headers.get('Content-Length', '0'))
            if self.path != '/v1/chat/completions' or not 0 < length <= 256 * 1024:
                self.send_error(400); return
            payload = json.loads(self.rfile.read(length))
            with lock:
                stats['requests'] += 1
                # Inspect the parsed projection, not JSON slash escaping.
                if any(str(scope).casefold() in value.casefold() for value in strings(payload)):
                    stats['path_leaks'] += 1
            try:
                response = answer(payload)
            except Exception as exc:
                with lock:
                    stats['errors'].append(type(exc).__name__ + ':' + str(exc))
                response = findings()
            write_response(self, response, payload)

    provider = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    threading.Thread(target=provider.serve_forever, daemon=True).start()
    server = create_server(profile, port=args.port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        app = server.app
        app.state.publish(scan_directory(str(scope))['items'], 'v0230-owned')
        settings = dict(provider='custom', protocol='chat_completions', model='offline-v0230-peer',
            base_url=f'http://127.0.0.1:{provider.server_port}/v1', send_paths=False, stream=True)
        app.assistant.save_settings(settings); app.trace.update(enabled=True)
        app.agent_budget.save({'model_requests': 10, 'tool_calls': 24})
        tasks = {}
        for kind in ('system', 'custom', 'duplicates'):
            task = app.harness.create('fixture-' + kind + ' 核对隔离测试目录', str(scope))
            if kind == 'system':
                value = app.harness._get(task['id']); value['preset'] = 'system_cleanup'; app.harness._save(value)
            tasks[kind] = task['id']
        for name in ('simulation.sqlite3', 'scan_index.sqlite3', 'developer_trace.sqlite3'):
            with closing(sqlite3.connect((profile / name).as_uri() + '?mode=ro', uri=True)) as origin:
                with closing(sqlite3.connect(native / name)) as target:
                    origin.backup(target)
        from diskpilot.llm import ModelAssistant
        ModelAssistant(native).save_settings(settings)
        metadata = dict(app_url=f'http://127.0.0.1:{server.server_port}', provider_url=settings['base_url'],
            provider_stats=f'http://127.0.0.1:{provider.server_port}/stats', tasks=tasks, scope=str(scope),
            fixture_hashes=hashes, native_profile=str(native), external_provider_used=False, real_credentials_used=False)
        destination = profile.parent / 'fixture-meta.json'
        destination.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(dict(metadata= str(destination), **metadata), ensure_ascii=False), flush=True)
        while not (profile / 'stop-fixture').exists():
            threading.Event().wait(.3)
        if hashes != {name: hashlib.sha256((scope / name).read_bytes()).hexdigest() for name in NAMES}:
            raise AssertionError('Owned fixture bytes changed')
    finally:
        server.shutdown(); server.server_close(); server.app.close()
        provider.shutdown(); provider.server_close()


if __name__ == '__main__':
    main()
