"""Owned progressive-list profiles with a deterministic loopback SSE provider.

The peer only reads preselected artificial task objects. It emits three scores
per response, using the ordinary transport and six-request/twelve-tool budget.
The source server replaces inventory with artificial rows; the native profile
works with the unmodified EXE and preserves exactly selected task identities.
No real model, credentials, file cleanup or system change is used here.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from diskpilot.llm import ModelAssistant
from diskpilot.scanner import scan_directory
from diskpilot.server import create_server
from diskpilot.windows import _item
from tests.prompt_fixture import parse_host_data
from serve_v0231_fixture import new_profile
from serve_v0250_fixture import cache_result
from run_tool_http_acceptance import Client


def hashes(scope):
    return {p.relative_to(scope).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in scope.rglob('*') if p.is_file()}


def seed(app, scope):
    for name, content in (('test-cache.tmp', b'Owned progressive cache sample.\n'),
                           ('keep-notes.txt', b'Owned artificial personal notes.\n')):
        (scope / name).write_bytes(content)
    rows = scan_directory(str(scope))['items']
    startup = []
    for index in range(12):
        executable = scope / f'fixture-startup-{index + 1:02}.exe'
        executable.write_bytes(b'Inert owned startup placeholder; never execute.\n')
        row = _item('startup', f'PanXuOwnedV0260-Startup-{index + 1:02}',
            f'验收启动项 {index + 1:02}', 'Owned artificial publisher', 'startup', 'disable_startup',
            dict(state='Enabled', enabled=True, synthetic=False,
                 fixture='offline-v0260-startup', usage_evidence={'status': 'unknown'}),
            path=str(executable), reason='人工离线验收数据，不是真实启动注册项。')
        row['evidence'] = ['离线人工验收库存，不代表本机真实软件或使用频率。']
        startup.append(row)
    app.state.publish(rows + startup, 'owned-v0260-selected-objects')
    h = app.harness
    files = [item for item in app.state.snapshot()['items']
             if item['kind'] == 'file' and item['name'] in ('test-cache.tmp', 'keep-notes.txt')]
    file_task = h.create_analysis(dict(section='files', ids=[row['id'] for row in files],
        reanalyze=True, goal='[盘序验收] 文件列表逐批评分；全部为人工样本，只分析不执行。'))
    h.tool_assessments.ttl = 900  # Source-only latency fixture; not a production default.
    with patch.object(app.cleanup_tools, 'inspect', side_effect=lambda tool_id, parameters=None:
            dict(cache_result(scope / ('artificial-cache-' + tool_id), 128 * 1024**2, 4),
                 tool_id=tool_id, parameters=parameters or {})):
        tools = [h.tool_assessments.inspect(key) for key in ('gpu_cache', 'thumbnail_cache', 'steam_cache')]
    tool_task = h.create_tool_analysis(dict(items=[dict(row['tool_ref'], inspection_id=row['inspection_id']) for row in tools],
        reanalyze=True, goal='[盘序验收] 工具列表逐批评分；只分析已保存人工诊断，不执行。'))
    startup_task = h.create_analysis(dict(section='background', ids=[row['id'] for row in startup],
        kind='startup', reanalyze=True,
        goal='[盘序验收] 12个启动项逐批评分；全部为人工注册信息，只分析不执行。'))
    approved = {row['id']: row for row in [*startup, *files]}
    approved.update({row['id']: dict(id=row['id'], kind='cleanup_tool', name=row['title']) for row in tools})
    return dict(tasks=dict(startup=startup_task['id'], files=file_task['id'], tools=tool_task['id']),
        startup_ids=[row['id'] for row in startup], file_ids=[row['id'] for row in files],
        tool_ids=[row['id'] for row in tools], approved=approved, startup=startup)


class Peer:
    def __init__(self, step_delay):
        self.delay = step_delay
        self.lock = threading.RLock()
        self.approved = {}
        self.states = {}
        self.stats = dict(requests=0, errors=[], external_requests=0, nonfixture_rejections=0, actions=[])

    def check_objects(self, value):
        if isinstance(value, list):
            for child in value:
                self.check_objects(child)
        elif isinstance(value, dict):
            if value.get('kind') in ('startup', 'file', 'directory', 'cleanup_tool', 'service', 'software', 'process') and value.get('id'):
                if value['id'] not in self.approved:
                    self.stats['nonfixture_rejections'] += 1
                    raise ValueError('Nonfixture task object rejected.')
            for child in value.values():
                self.check_objects(child)

    def answer(self, profile, payload):
        definitions = {row['function']['name']: row['function'] for row in payload.get('tools', [])}
        if 'connection_probe' in definitions:
            nonce = definitions['connection_probe']['parameters']['properties']['nonce']['enum'][0]
            return self.tool('connection_probe', {'nonce': nonce}, '离线连接检查。')
        context = parse_host_data(payload['messages'][1]['content'])
        if not isinstance(context, dict) or not context.get('analysis'):
            raise ValueError('Only frozen owned analysis tasks are accepted.')
        # The compact wire deliberately omits constant simulation/scope fields.
        # The frozen analysis section remains part of its useful contract.
        section = context['analysis'].get('section')
        kind = 'startup' if section == 'background' else 'cleanup_tool' if section == 'cleanup_tools' else 'file'
        if section not in ('background', 'cleanup_tools', 'files'):
            raise ValueError('Unknown analysis scope.')
        expected = {ident for ident, row in self.approved.items() if row['kind'] == kind}
        if not 0 < context['analysis'].get('target_count', -1) <= len(expected):
            raise ValueError('Unexpected selected object count.')
        state_key = (profile, kind, context.get('goal', ''))
        state = self.states.setdefault(state_key, dict(order=[], scored=set(), inspected=set()))
        if len(payload['messages']) == 2 and not context.get('advice_progress', {}).get('recorded'):
            state.update(order=[], scored=set(), inspected=set())
        calls = {}
        for message in payload['messages']:
            for call in message.get('tool_calls') or []:
                calls[call['id']] = call['function']
            if message.get('role') != 'tool':
                continue
            try:
                result = parse_host_data(message['content'])
            except (ValueError, TypeError):
                continue
            self.check_objects(result)
            call = calls.get(message.get('tool_call_id'))
            if not call or not isinstance(result, dict) or result.get('error'):
                continue
            arguments = json.loads(call['arguments'])
            if call['name'] in ('list_items', 'inspect_items'):
                ids = [row['id'] for row in result.get('items', []) if row.get('id') in expected]
                state['order'] = list(dict.fromkeys(state['order'] + ids))
                if call['name'] == 'inspect_items':
                    state['inspected'].update(ids)
            elif call['name'] == 'record_advice' and result.get('recorded_count', 0) == len(arguments.get('items', [])):
                ids = {row['id'] for row in arguments['items']}
                if not ids <= expected:
                    raise ValueError('Nonfixture advice identity rejected.')
                state['scored'].update(ids)
        # A pause can occur immediately after a score was committed, before its
        # next transport response. Durable host coverage restores that prefix.
        advised = context.get('analysis', {}).get('advised_count', 0)
        if type(advised) is int and 0 < advised <= len(state['order']):
            state['scored'].update(state['order'][:advised])
        if not state['order']:
            return self.tool('list_items', {'limit': 20}, '正在读取本次人工验收清单，不采集真实系统。')
        if kind == 'cleanup_tool' and set(state['order']) - state['inspected']:
            return self.tool('inspect_items', {'ids': state['order']}, '正在读取已保存的人工工具诊断。')
        pending = [ident for ident in state['order'] if ident not in state['scored']]
        if pending and 'record_advice' in definitions:
            chosen = pending[:3]
            entries = []
            for ident in chosen:
                row = self.approved[ident]
                index = state['order'].index(ident)
                score = 1 if row.get('name', '').endswith('.txt') else (5, 4, 3, 2, 1)[index % 5]
                entries.append(dict(id=ident, conclusion='keep' if score <= 2 else 'recommend' if score >= 4 else 'review',
                    cleanup_rating=score, purpose='离线人工验收对象：' + row['name'],
                    reason='固定离线评分用于验证逐批更新、筛选与续接，不代表真实清理判断。',
                    impact='停用只改变随登录自动就绪；本次不执行。' if kind == 'startup' else '只用于建议展示，不执行真实文件或系统操作。',
                    impact_rating=2 if score >= 4 else 4 if score <= 2 else 3))
            return self.tool('record_advice', {'items': entries}, f'已保留前 {len(state["scored"])} 项结果，正在整理下一批 {len(chosen)} 项建议。')
        return self.tool('present_findings', dict(summary=f'离线人工验收：已保存 {len(state["scored"])} 项建议，未执行文件或系统操作。', needs_input=False),
                         '本次人工验收建议已保存，可以按评分筛选与勾选。')

    @staticmethod
    def tool(name, arguments, content):
        return {'choices': [{'message': dict(role='assistant', content=content,
            tool_calls=[dict(id='fixture-' + name + '-' + hashlib.sha256(json.dumps(arguments, sort_keys=True).encode()).hexdigest()[:12],
                type='function', function=dict(name=name, arguments=json.dumps(arguments, ensure_ascii=False)))])}]}

    def write(self, handler, value, payload):
        message = value['choices'][0]['message']
        if not payload.get('stream'):
            raw = json.dumps(value, ensure_ascii=False).encode()
            handler.send_response(200); handler.send_header('Content-Type', 'application/json'); handler.send_header('Content-Length', str(len(raw))); handler.end_headers(); handler.wfile.write(raw)
            return
        handler.send_response(200); handler.send_header('Content-Type', 'text/event-stream; charset=utf-8'); handler.send_header('Cache-Control', 'no-store'); handler.send_header('Connection', 'close'); handler.end_headers(); handler.close_connection = True
        def send(delta):
            handler.wfile.write(('data: ' + json.dumps({'choices': [{'index': 0, 'delta': delta}]}, ensure_ascii=False) + '\n\n').encode()); handler.wfile.flush()
        try:
            send({'role': 'assistant', 'content': message['content']})
            call = message['tool_calls'][0]
            if call['function']['name'] == 'record_advice':
                # A pending tool request is not an applied score. The delay is
                # only an owned transport fixture, so Pause can be observed.
                for _ in range(max(1, round(self.delay * 4))):
                    time.sleep(self.delay / max(1, round(self.delay * 4)))
                    send({'content': ''})
            send({'tool_calls': [{'index': 0, 'id': call['id'], 'type': 'function',
                'function': {'name': call['function']['name'], 'arguments': call['function']['arguments']}}]})
            handler.wfile.write(b'data: {"choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}\n\n')
            handler.wfile.write(b'data: {"choices":[],"usage":{"prompt_tokens":64,"completion_tokens":64}}\n\n')
            handler.wfile.write(b'data: [DONE]\n\n'); handler.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
            pass


def self_check(url, metadata, report_path):
    client = Client(url)
    def wait(key, predicate=lambda task: task['status'] not in ('ready', 'investigating'), timeout=70):
        end, seen = time.monotonic() + timeout, []
        while time.monotonic() < end:
            value = client.request('/api/tasks/' + key)['task']
            saved = value.get('analysis_progress', {}).get('saved_count', value.get('advice_total', 0))
            if not seen or seen[-1] != saved:
                seen.append(saved)
            if predicate(value):
                return value, seen
            threading.Event().wait(.08)
        raise TimeoutError('Owned fixture task did not reach the expected stage.')
    complete = client.request('/api/tasks/analyze', dict(section='background', ids=metadata['startup_ids'],
        reanalyze=True, goal='[盘序验收] HTTP完整12项逐批分析。'))['task']
    final, samples = wait(complete['id'])
    checks = dict(complete_12=final['status'] == 'completed' and final['analysis_progress']['saved_count'] == 12,
        progressive_batches=len([n for n in samples if 0 < n < 12]) >= 2,
        default_budget=final['budget']['per_turn'] == dict(model_requests=6, tool_calls=12),
        no_tool_failures=final['usage']['tool_failures'] == 0)
    if not all(checks.values()):
        report_path.write_text(json.dumps(dict(passed=False, checks=checks, task=final, samples=samples), ensure_ascii=False, indent=2), encoding='utf-8')
        raise AssertionError('The initial owned progressive analysis did not finish correctly.')
    paused = client.request('/api/tasks/analyze', dict(section='background', ids=metadata['startup_ids'],
        reanalyze=True, goal='[盘序验收] HTTP暂停续接12项。'))['task']
    partial, pause_samples = wait(paused['id'], lambda task: task['status'] not in ('ready', 'investigating') or 0 < task['analysis_progress']['saved_count'] < 12)
    client.request('/api/jobs/' + partial['job_id'] + '/cancel', {})
    stopped, _ = wait(paused['id'])
    checks['pause_preserves_partial'] = stopped['status'] == 'paused' and 0 < stopped['analysis_progress']['saved_count'] < 12
    client.request('/api/tasks/' + paused['id'] + '/continue', {})
    resumed, resume_samples = wait(paused['id'])
    checks['resume_finishes_remaining'] = resumed['status'] == 'completed' and resumed['analysis_progress']['saved_count'] == 12 and resumed['usage']['tool_failures'] == 0
    other = {}
    for kind, total in (('files', 2), ('tools', 3)):
        key = metadata['tasks'][kind]
        client.request('/api/tasks/' + key + '/continue', {})
        result, stages = wait(key)
        checks[kind + '_transport'] = result['status'] == 'completed' and result['analysis_progress']['saved_count'] == total and result['usage']['tool_failures'] == 0
        other[kind] = dict(status=result['status'], samples=stages, usage=result['usage'])
    checks['no_simulated_or_real_execution'] = client.request('/api/history')['history'] == [] and client.request('/api/cleanup-tools/history')['history'] == []
    checks['fixture_unchanged'] = hashes(Path(metadata['scope'])) == metadata['fixture_hashes']
    result = dict(passed=all(checks.values()), checks=checks, completed_samples=samples,
        pause_samples=pause_samples, resume_samples=resume_samples,
        usage=final['usage'], pause_usage=resumed['usage'], actual_reclaimed_bytes=0, real_model_requests=0,
        other_transport=other,
        external_provider_used=False, real_credentials_used=False)
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    if not result['passed']:
        raise AssertionError('Owned progressive-list self-check failed; inspect its report.')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--native-profile', type=Path, required=True)
    parser.add_argument('--port', type=int, default=14786)
    parser.add_argument('--provider-port', type=int, default=0)
    parser.add_argument('--step-delay', type=float, default=4)
    parser.add_argument('--self-check', action='store_true')
    parser.add_argument('--serve', action='store_true')
    args = parser.parse_args()
    if not 0 <= args.step_delay <= 20:
        raise ValueError('Step delay must be between0and20seconds.')
    profile, native = new_profile(args.data_dir), new_profile(args.native_profile)
    scope = Path(tempfile.mkdtemp(prefix='panxu-owned-v0260-'))
    peer = Peer(args.step_delay)
    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_): pass
        def do_GET(self):
            if self.path != '/stats': self.send_error(404); return
            with peer.lock: raw = json.dumps(peer.stats, ensure_ascii=False).encode()
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)
        def do_POST(self):
            if self.path not in ('/v1/source/chat/completions', '/v1/native/chat/completions'): self.send_error(404); return
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 256 * 1024: self.send_error(400); return
            try:
                payload = json.loads(self.rfile.read(length))
                with peer.lock:
                    peer.stats['requests'] += 1
                    value = peer.answer('native' if '/native/' in self.path else 'source', payload)
                    peer.stats['actions'].append(value['choices'][0]['message']['tool_calls'][0]['function']['name'])
                peer.write(self, value, payload)
            except Exception as problem:
                with peer.lock: peer.stats['errors'].append(type(problem).__name__)
                self.send_error(400)
    provider = ThreadingHTTPServer(('127.0.0.1', args.provider_port), Provider)
    threading.Thread(target=provider.serve_forever, daemon=True).start()
    server = create_server(profile, port=args.port)
    patchers = []
    try:
        seeded = seed(server.app, scope)
        peer.approved = seeded['approved']
        def inventory(section):
            return dict(items=copy.deepcopy(seeded['startup']) if section == 'background' else [], warnings=['离线人工库存；没有采集真实应用。'])
        for target in ('diskpilot.tasks.inventory', 'diskpilot.server.inventory'):
            patcher = patch(target, side_effect=inventory); patcher.start(); patchers.append(patcher)
        settings = dict(provider='custom', protocol='chat_completions', model='offline-v0260-peer',
            base_url=f'http://127.0.0.1:{provider.server_port}/v1/source', send_paths=False, stream=True)
        server.app.assistant.save_settings(settings); server.app.trace.update(enabled=True)
        for name in ('simulation.sqlite3', 'scan_index.sqlite3', 'developer_trace.sqlite3'):
            with closing(sqlite3.connect((profile / name).as_uri() + '?mode=ro', uri=True)) as origin:
                with closing(sqlite3.connect(native / name)) as target: origin.backup(target)
        ModelAssistant(native).save_settings(dict(settings, base_url=f'http://127.0.0.1:{provider.server_port}/v1/native'))
        metadata = dict(app_url=f'http://127.0.0.1:{server.server_port}', source_profile=str(profile), native_profile=str(native),
            provider_stats=f'http://127.0.0.1:{provider.server_port}/stats', scope=str(scope), fixture_hashes=hashes(scope),
            **{key: seeded[key] for key in ('tasks', 'startup_ids', 'file_ids', 'tool_ids')},
            step_delay=args.step_delay, model='offline-v0260-peer', external_provider_used=False, real_credentials_used=False,
            limits=['Allstartup registrations, tool sizes and advice are artificial.',
                'Sourceinventory is replaced; finalEXE is unmodified and only the selected fixture task IDs are returned to the peer.',
                'Broad home-preset collection and real system action quality are outside this fixture.',
                'Native frozen tool history may need fresh diagnosis for current execution eligibility; no tool execution is tested.'])
        destination = profile / 'fixture-meta.json'; destination.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
        threading.Thread(target=server.serve_forever, daemon=True).start()
        print(json.dumps(dict(metadata=str(destination), app_url=metadata['app_url'], provider_stats=metadata['provider_stats'], native_profile=str(native), tasks=metadata['tasks']), ensure_ascii=False), flush=True)
        if args.self_check:
            result = self_check(metadata['app_url'], metadata, profile / 'progressive-self-check.json')
            print(json.dumps(dict(self_check_passed=result['passed'], samples=result['completed_samples']), ensure_ascii=False), flush=True)
        while args.serve and not (profile / 'stop-fixture').exists(): threading.Event().wait(.3)
        if hashes(scope) != metadata['fixture_hashes']: raise AssertionError('Owned fixture files changed.')
        (profile / 'peer-stats.json').write_text(json.dumps(peer.stats, ensure_ascii=False, indent=2), encoding='utf-8')
    finally:
        server.shutdown(); server.server_close(); server.app.close()
        provider.shutdown(); provider.server_close()
        for patcher in reversed(patchers): patcher.stop()


if __name__ == '__main__':
    main()
