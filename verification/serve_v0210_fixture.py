"""Owned profiles and an offline SSE peer for the 0.21 home/harness flows.

The peer is a deterministic protocol fixture, not a model-quality evaluation.
It never requests file collection or launches a system program. Windows system
configuration, if explicitly requested, uses the app's existing read-only tool.
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
from diskpilot.windows import _item
from tests.prompt_fixture import parse_host_data
from serve_v090_fixture import write_response


def call(name, arguments):
    return {'choices': [{'message': {'role': 'assistant', 'content': '正在核对隔离样本；所有操作保持模拟。',
        'tool_calls': [{'id': 'owned-' + name, 'type': 'function',
                        'function': {'name': name, 'arguments': json.dumps(arguments, ensure_ascii=False)}}]},
        'finish_reason': 'tool_calls'}]}


def answer(payload):
    context = parse_host_data(payload['messages'][1]['content'])
    results = [message for message in payload['messages'] if message['role'] == 'tool']
    tools = {entry['function']['name'] for entry in payload['tools']}
    if not results:
        return call('collect_system_configuration', {}) if '系统盘' in context.get('goal', '') else call('list_items', {'limit': 20})
    last = results[-1]
    previous = next(entry['function']['name'] for message in payload['messages'] if message['role'] == 'assistant'
                    for entry in message.get('tool_calls', []) if entry['id'] == last['tool_call_id'])
    if previous == 'list_items' and 'record_advice' in tools:
        data = parse_host_data(last['content'])
        rows = [row for row in data.get('items', []) if row.get('kind') == 'startup'][:3]
        if rows:
            return call('record_advice', {'items': [dict(id=row['id'], conclusion='review', cleanup_rating=4,
                impact_rating=2, purpose='隔离测试中的开机启动样本。', reason='样本只用于验证建议与工具结果的交接，可按需启动。',
                impact='实际程序不受影响；本轮仅记录测试建议。') for row in rows]})
    return call('present_findings', {'summary': '隔离验收已取得有限观察并保存结果；系统工具可以独立选择，尚未扫描的文件仍未覆盖。',
        'needs_input': False, 'briefing': {'coverage': ['仅核对隔离元数据或固定只读系统配置，没有执行真实清理。']}})


def new_profile(path):
    path = path.resolve()
    if not path.is_relative_to(ROOT / 'verification') or path.exists():
        raise ValueError('Use a new profile inside verification/. Existing profiles are never overwritten.')
    path.mkdir(parents=True)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--native-profile', type=Path, required=True)
    parser.add_argument('--port', type=int, default=3566)
    args = parser.parse_args()
    profile = new_profile(args.data_dir)
    native = new_profile(args.native_profile)
    scope = Path(tempfile.mkdtemp(prefix='panxu-owned-v0210-'))
    for name in ('旧安装样本.zip', '需要保留的资料.txt'):
        (scope / name).write_bytes(('Owned unchanged fixture ' + name).encode('utf-8'))
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in scope.iterdir()}
    stats = dict(requests=0, external_provider_used=False, real_credentials_used=False)

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            length = int(self.headers.get('Content-Length', '0'))
            if self.path != '/v1/chat/completions' or not 0 < length <= 192 * 1024:
                self.send_error(400)
                return
            payload = json.loads(self.rfile.read(length))
            stats['requests'] += 1
            write_response(self, answer(payload), payload)

    provider = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    threading.Thread(target=provider.serve_forever, daemon=True).start()
    server = create_server(profile, port=args.port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        app = server.app
        app.state.publish(scan_directory(str(scope))['items'], 'v0210-owned-files')
        rows = [_item('startup', 'panxu-owned-' + str(index), '隔离启动样本 ' + str(index), 'Owned fixture',
                     '开机启动', 'disable_startup', {'fixture': True, 'enabled': True},
                     reason='Only disposable metadata, not a Windows registration.') for index in range(3)]
        app.state.publish(rows, 'v0210-owned-apps')
        settings = dict(provider='custom', protocol='chat_completions', model='offline-harness-fixture',
            base_url=f'http://127.0.0.1:{provider.server_port}/v1', send_paths=False, stream=True)
        app.assistant.save_settings(settings)
        app.trace.update(enabled=True)
        task = app.harness.create_analysis(dict(section='background', ids=[row['id'] for row in rows], depth='auto'))
        app.harness.claim(task['id'])
        try:
            task = app.harness.investigate(task['id'])
        finally:
            app.harness.release(task['id'])
        if task['status'] != 'completed' or task.get('advice_total') != 3:
            raise RuntimeError('The offline peer must produce three grounded fixture assessments.')
        for name in ('simulation.sqlite3', 'scan_index.sqlite3', 'developer_trace.sqlite3'):
            with closing(sqlite3.connect((profile / name).as_uri() + '?mode=ro', uri=True)) as origin:
                with closing(sqlite3.connect(native / name)) as target:
                    origin.backup(target)
        from diskpilot.llm import ModelAssistant
        ModelAssistant(native).save_settings(settings)
        info = dict(app_url=f'http://127.0.0.1:{server.server_port}', provider_url=settings['base_url'],
            task_id=task['id'], scope=str(scope), startup_ids=[row['id'] for row in rows],
            fixture_hashes=before, native_profile=str(native), **stats)
        (profile.parent / 'fixture-meta.json').write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(info, ensure_ascii=False), flush=True)
        while not (profile / 'stop-fixture').exists():
            threading.Event().wait(.3)
        assert before == {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in scope.iterdir()}
    finally:
        server.shutdown()
        server.server_close()
        server.app.close()
        provider.shutdown()
        provider.server_close()


if __name__ == '__main__':
    main()
