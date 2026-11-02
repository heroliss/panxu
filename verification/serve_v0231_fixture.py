"""Owned task-hub profiles and a deterministic loopback SSE peer for 0.23.1.

Only tiny disposable samples are observed. One separate sample is simulated to
seed an honest operation receipt; no real file, program or system state changes.
Native demonstration states are separate from the two HTTP continuation cases.
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
from diskpilot.tasks import now
from diskpilot.llm import ModelAssistant
from tests.prompt_fixture import parse_host_data
from serve_v090_fixture import write_response

NAMES = ('old-cache.tmp', 'keep-notes.txt', 'direct-only.tmp')


def hashes(scope):
    return {name: hashlib.sha256((scope / name).read_bytes()).hexdigest() for name in NAMES}


def new_profile(path):
    path = path.resolve()
    if not path.is_relative_to(ROOT / 'verification') or path.exists():
        raise ValueError('Use a new profile under verification; existing profiles are never overwritten.')
    path.mkdir(parents=True)
    return path


def response(payload):
    context = parse_host_data(payload['messages'][1]['content'])
    if not isinstance(context, dict) or '测试' not in context.get('goal', ''):
        raise ValueError('Only owned task-hub fixture goals are accepted.')
    arguments = dict(summary='已保留两项测试文件的建议；你可以查看清单并决定如何处理。',
        needs_input=False, briefing={'highlights': ['旧临时文件和个人笔记分别给出了建议。'],
        'coverage': ['只检查隔离样本，没有执行真实清理。']})
    return {'choices': [{'message': {'role': 'assistant', 'content': '正在根据你的选择整理已有建议。',
        'tool_calls': [{'id': 'owned-findings', 'type': 'function', 'function': {
            'name': 'present_findings', 'arguments': json.dumps(arguments, ensure_ascii=False)}}]},
        'finish_reason': 'tool_calls'}]}


def seed(app, scope):
    h, stop = app.harness, threading.Event()
    app.state.publish(scan_directory(str(scope))['items'], 'v0231-owned')
    direct_file = next(row for row in app.state.snapshot()['items'] if row['name'] == 'direct-only.tmp')
    prepared = h.prepare_assessment_plan([direct_file['id']])
    direct = prepared['task']['id']
    h.validate_approval(direct, prepared['plan']['version'], prepared['picked_ids'])
    h.claim(direct, execute=True)
    result = h.investigate(direct, execute=True)
    if result['last_result']['succeeded'] != 1:
        raise AssertionError('Owned direct-operation seed failed.')
    item_ids = {}
    for row in app.state.snapshot()['items']:
        if row['kind'] == 'file' and row['name'] in NAMES[:2]:
            item_ids[row['name']] = row['id']
    provenance = dict(source='llm', provider='custom', model='offline-v0231-peer',
        protocol='chat_completions', generated_at=now())

    def create(label, state, *, question=False, advice=True, specialist=False):
        task = h.create('整理测试目录中的旧文件（' + label + '）', str(scope))
        key = task['id']
        observation = h.tool(key, 'survey_scope', {'scope_id': 'root'}, cancel=stop)
        observation['fact_id'] = h._record_fact(key, 'survey_scope', observation)
        value = h._get(key)
        value['observations'].append(dict(tool='survey_scope', at=now(), result=observation))
        h._save(value)
        if advice:
            h.tool(key, 'record_advice', {'items': [
                dict(id=item_ids['old-cache.tmp'], conclusion='review', cleanup_rating=4,
                    purpose='隔离测试中的临时文件。', reason='已结束的样本工作留下的小文件，可由用户决定。',
                    impact='以后需要时可能重建；本次只是测试建议。', impact_rating=2),
                dict(id=item_ids['keep-notes.txt'], conclusion='keep', cleanup_rating=1,
                    purpose='隔离测试中的个人笔记。', reason='用户创建的内容，建议保留。',
                    impact='删除可能失去笔记，请保留需要的资料。', impact_rating=4)]}, cancel=stop, provenance=provenance)
        if specialist:
            h.tool(key, 'record_specialist_suggestions', {'items': [dict(id='owned-recycle',
                title='查看系统回收站', explanation='可以先了解回收站占用，再决定是否清理。',
                impact='进入工具只检查状态，操作仍需另行确认。', options=[
                    dict(id='open', label='查看回收站工具', kind='tool', tool_id='recycle_bin',
                         parameters={'volume': scope.anchor.rstrip('\\/')}),
                    dict(id='skip', label='暂不清理', kind='answer')])]}, cancel=stop, provenance=provenance)
        if question:
            h.tool(key, 'ask_user', {'question': '测试目录中的临时文件以后还需要保留吗？', 'options': [
                dict(id='review', label='看看清理建议', description='保留已有结果，再查看可选的处理清单。'),
                dict(id='keep', label='先保留', description='继续保留文件；回答不表示同意删除。')]}, cancel=stop)
        value = h._get(key)
        descriptions = {'paused': '分析已暂停，已有两项建议会保留。',
            'error': '上次分析连接中断，已有建议会保留，可以重试。',
            'needs_model': '需要连接模型才能继续，已有建议仍可查看。',
            'budget_exhausted': '本轮分析额度已用完，已有建议会保留。',
            'completed': '本轮分析已完成，可以查看建议并决定下一步。'}
        value.update(status='needs_input' if question else state,
            summary=value['summary'] if question else descriptions.get(state, '准备继续分析。'))
        if state != 'completed' and not question:
            h._checkpoint(value, state)
        h._save(value)
        return key

    checker = dict(paused=create('接口继续检查', 'paused'),
                   question=create('接口回答检查', 'needs_input', question=True))
    tasks = dict(direct_completed=direct,
        completed=create('已有建议', 'completed'),
        needs_model=create('连接模型', 'needs_model'),
        error=create('连接中断', 'error'),
        budget=create('额度用完', 'budget_exhausted'),
        tools_only=create('专项工具建议', 'completed', advice=False, specialist=True),
        question=create('等待回答', 'needs_input', question=True),
        paused=create('分析暂停', 'paused'))
    return tasks, checker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--native-profile', type=Path, required=True)
    parser.add_argument('--port', type=int, default=14771)
    args = parser.parse_args()
    profile, native = new_profile(args.data_dir), new_profile(args.native_profile)
    if profile.is_relative_to(native) or native.is_relative_to(profile):
        raise ValueError('Source and native profiles must be separate.')
    scope = Path(tempfile.mkdtemp(prefix='panxu-owned-v0231-'))
    for name in NAMES:
        (scope / name).write_bytes(('Owned unchanged task-hub sample: ' + name).encode())
    before = hashes(scope)
    stats, lock = dict(requests=0, errors=[], path_leaks=0), threading.Lock()

    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for item in value.values():
                yield from strings(item)
        elif isinstance(value, list):
            for item in value:
                yield from strings(item)

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
                if any(str(scope).casefold() in text.casefold() for text in strings(payload)):
                    stats['path_leaks'] += 1
            try:
                answer = response(payload)
            except Exception as exc:
                with lock:
                    stats['errors'].append(type(exc).__name__)
                self.send_error(400); return
            write_response(self, answer, payload)

    provider = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    threading.Thread(target=provider.serve_forever, daemon=True).start()
    server = create_server(profile, port=args.port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        settings = dict(provider='custom', protocol='chat_completions', model='offline-v0231-peer',
            base_url=f'http://127.0.0.1:{provider.server_port}/v1', send_paths=False, stream=True)
        app = server.app
        app.assistant.save_settings(settings); app.trace.update(enabled=True)
        tasks, checker = seed(app, scope)
        for name in ('simulation.sqlite3', 'scan_index.sqlite3', 'developer_trace.sqlite3'):
            with closing(sqlite3.connect((profile / name).as_uri() + '?mode=ro', uri=True)) as origin:
                with closing(sqlite3.connect(native / name)) as target:
                    origin.backup(target)
        ModelAssistant(native).save_settings(settings)
        metadata = dict(app_url=f'http://127.0.0.1:{server.server_port}', provider_url=settings['base_url'],
            provider_stats=f'http://127.0.0.1:{provider.server_port}/stats', tasks=tasks, checker_tasks=checker,
            scope=str(scope), fixture_hashes=before, native_profile=str(native),
            external_provider_used=False, real_credentials_used=False)
        destination = profile.parent / 'fixture-meta.json'
        destination.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(dict(metadata=str(destination), **metadata), ensure_ascii=False), flush=True)
        while not (profile / 'stop-fixture').exists():
            threading.Event().wait(.3)
        if hashes(scope) != before:
            raise AssertionError('Owned sample bytes changed.')
    finally:
        server.shutdown(); server.server_close(); server.app.close()
        provider.shutdown(); provider.server_close()


if __name__ == '__main__':
    main()
