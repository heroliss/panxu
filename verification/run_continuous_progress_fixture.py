"""Owned source/native profiles for continuous analysis and explicit state advice.

The deterministic loopback SSE peer requires one list call, twelve separate
record_advice responses and final findings: fourteen model calls from one start.
The source collector is replaced by artificial records; the copied native
profile works with an unmodified packaged host. No startup/service registration,
real provider, credentials, file deletion or system action is used.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import copy
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
from diskpilot.llm import ModelAssistant
from diskpilot.server import create_server
from diskpilot.windows import _item
from tests.prompt_fixture import parse_host_data
from serve_v0231_fixture import new_profile
from serve_v0260_fixture import Peer as ProgressivePeer, hashes

MODEL = 'offline-v0270-peer'
GOAL_PREFIX = '[盘序验收0.27] '


def seed(app, scope):
    startup, services, entries = [], [], {}
    startup_cases = [('keep_disabled', False, 1), ('enable', False, 2),
                     ('keep_current', True, 3), ('keep_enabled', True, 1)]
    startup_cases.extend(('disable', True, (4, 5)[index % 2]) for index in range(8))
    for index, (recommendation, enabled, score) in enumerate(startup_cases, 1):
        executable = scope / f'fixture-startup-{index:02}.exe'
        executable.write_bytes(b'Inert owned continuous-analysis startup sample; never execute.\n')
        row = _item('startup', f'PanXuOwnedV0270-Startup-{index:02}',
            f'连续验收启动项 {index:02}', 'Owned artificial publisher', 'startup', 'disable_startup',
            dict(state='Enabled' if enabled else 'Disabled', enabled=enabled,
                 fixture=MODEL, usage_evidence={'status': 'unknown'}), path=str(executable),
            reason='人工离线验收信息，不是真实启动注册项。')
        startup.append(row)
        entries[row['id']] = dict(recommendation=recommendation, scope='login', score=score)
    service_cases = [
        ('已停止且禁用启动', 'Stopped', 'Disabled', 'keep_disabled', 'service_startup', 1),
        ('已停止建议启动', 'Stopped', 'Manual', 'enable', 'runtime', 2),
        ('运行中建议停止', 'Running', 'Auto', 'disable', 'runtime', 5),
        ('运行状态未知', 'Unknown', 'Manual', 'investigate', 'runtime', None),
    ]
    for index, (name, state, mode, recommendation, state_scope, score) in enumerate(service_cases, 1):
        executable = scope / f'fixture-service-{index:02}.exe'
        executable.write_bytes(b'Inert owned state-advice service sample; never execute.\n')
        row = _item('service', f'PanXuOwnedV0270-Service-{index:02}', '状态验收服务 ' + name,
            'Owned artificial publisher', 'service', 'stop_service',
            dict(service_name=f'PanXuOwnedV0270-Service-{index:02}', state=state, start_mode=mode,
                 process_id=123456 if state == 'Running' else 0, fixture=MODEL),
            path=str(executable), reason='人工离线服务信息，未注册Windows服务。')
        services.append(row)
        entries[row['id']] = dict(recommendation=recommendation, scope=state_scope, score=score)
    app.state.publish(startup + services, 'owned-v0270-selected-objects')
    service_task = app.harness.create_analysis(dict(section='background', kind='service',
        ids=[row['id'] for row in services], reanalyze=True,
        goal=GOAL_PREFIX + '四个服务状态建议；区分运行与启动方式，只分析不执行。'))
    startup_task = app.harness.create_analysis(dict(section='background', kind='startup',
        ids=[row['id'] for row in startup], reanalyze=True,
        goal=GOAL_PREFIX + '12个启动项逐项分析，跨检查点自动继续；只分析不执行。'))
    return dict(tasks=dict(startup=startup_task['id'], services=service_task['id']),
        startup_ids=[row['id'] for row in startup], service_ids=[row['id'] for row in services],
        approved={row['id']: row for row in startup + services}, entries=entries,
        startup=startup, services=services)


class Peer(ProgressivePeer):
    def __init__(self, step_delay):
        super().__init__(step_delay)
        self.entries = {}
        self.stats['by_profile'] = {}

    def answer(self, profile, payload):
        definitions = {row['function']['name']: row['function'] for row in payload.get('tools', [])}
        if 'connection_probe' in definitions:
            nonce = definitions['connection_probe']['parameters']['properties']['nonce']['enum'][0]
            return self.tool('connection_probe', {'nonce': nonce}, '离线连接检查。')
        context = parse_host_data(payload['messages'][1]['content'])
        if (not isinstance(context, dict) or not isinstance(context.get('analysis'), dict)
                or not context.get('goal', '').startswith(GOAL_PREFIX)
                or context['analysis'].get('section') != 'background'):
            raise ValueError('Only owned frozen background analyses are accepted.')
        kind = 'service' if '四个服务状态' in context['goal'] else 'startup'
        expected = {ident for ident, row in self.approved.items() if row['kind'] == kind}
        if context['analysis'].get('target_count') != len(expected) or not expected:
            raise ValueError('The selected fixture scope must be exact.')
        key = (profile, kind, context['goal'])
        state = self.states.setdefault(key, dict(order=[], scored=set()))
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
            if call['name'] == 'list_items':
                ids = [row['id'] for row in result.get('items', [])]
                if set(ids) != expected:
                    raise ValueError('list_items must return only the exact fixture objects.')
                state['order'] = ids
            elif call['name'] == 'record_advice' and result.get('recorded_count') == len(arguments['items']):
                ids = {row['id'] for row in arguments['items']}
                if not ids <= expected:
                    raise ValueError('Unexpected advice identity.')
                state['scored'].update(ids)
        advised = context['analysis'].get('advised_count', 0)
        if type(advised) is int and 0 < advised <= len(state['order']):
            state['scored'].update(state['order'][:advised])
        if not state['order']:
            result = self.tool('list_items', {'limit': 30}, '正在读取人工验收清单，不采集真实系统。')
        else:
            pending = [ident for ident in state['order'] if ident not in state['scored']]
            if pending:
                ident = pending[0]
                row, case = self.approved[ident], self.entries[ident]
                score = case['score']
                advice = dict(id=ident,
                    conclusion='keep' if score in (1, 2) else 'recommend' if score in (4, 5) else 'review',
                    cleanup_rating=score, state_recommendation=case['recommendation'], state_scope=case['scope'],
                    purpose='人工验收对象：' + row['name'],
                    reason='固定离线建议验证连续分析、状态方向和勾选语义；不代表真实系统清理判断。',
                    impact='仅分析人工对象，不修改开机启动、服务运行状态或启动方式。', impact_rating=2)
                result = self.tool('record_advice', {'items': [advice]},
                    f'已保存 {len(state["scored"])} 项建议，正在分析第 {len(state["scored"]) + 1} 项。')
            else:
                result = self.tool('present_findings', dict(
                    summary=f'人工验收：{len(expected)}项建议已保存；未执行真实操作。', needs_input=False),
                    '本轮人工验收已分析完毕，可查看状态建议和评分。')
        name = result['choices'][0]['message']['tool_calls'][0]['function']['name']
        if name not in definitions:
            raise ValueError('The fixture requires a declared ordinary tool: ' + name)
        bucket = self.stats['by_profile'].setdefault(profile, {}).setdefault(kind,
            dict(requests=0, actions=[]))
        bucket['requests'] += 1
        bucket['actions'].append(name)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--native-profile', type=Path, required=True)
    parser.add_argument('--port', type=int, default=14787)
    parser.add_argument('--provider-port', type=int, default=10252)
    parser.add_argument('--step-delay', type=float, default=2)
    parser.add_argument('--serve', action='store_true')
    args = parser.parse_args()
    if not 0 <= args.step_delay <= 10:
        raise ValueError('Step delay must be0..10seconds.')
    profile, native = new_profile(args.data_dir), new_profile(args.native_profile)
    scope = Path(tempfile.mkdtemp(prefix='panxu-owned-v0270-'))
    peer = Peer(args.step_delay)

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            if self.path != '/stats':
                self.send_error(404)
                return
            with peer.lock:
                raw = json.dumps(peer.stats, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            if self.path not in ('/v1/source/chat/completions', '/v1/native/chat/completions'):
                self.send_error(404)
                return
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 2 * 1024**2:
                self.send_error(400)
                return
            try:
                payload = json.loads(self.rfile.read(length))
                with peer.lock:
                    peer.stats['requests'] += 1
                    value = peer.answer('native' if '/native/' in self.path else 'source', payload)
                    peer.stats['actions'].append(value['choices'][0]['message']['tool_calls'][0]['function']['name'])
                peer.write(self, value, payload)
            except Exception as problem:
                with peer.lock:
                    peer.stats['errors'].append(type(problem).__name__ + ': ' + str(problem))
                self.send_error(400)

    provider = ThreadingHTTPServer(('127.0.0.1', args.provider_port), Provider)
    threading.Thread(target=provider.serve_forever, daemon=True).start()
    server = create_server(profile, port=args.port)
    patchers = []
    metadata = None
    try:
        seeded = seed(server.app, scope)
        peer.approved, peer.entries = seeded['approved'], seeded['entries']

        def inventory(section):
            return dict(items=copy.deepcopy(seeded['startup'] + seeded['services']) if section == 'background' else [],
                        warnings=['人工离线库存；没有读取真实启动项或服务。'])

        for target in ('diskpilot.tasks.inventory', 'diskpilot.server.inventory'):
            patcher = patch(target, side_effect=inventory)
            patcher.start()
            patchers.append(patcher)
        settings = dict(provider='custom', protocol='chat_completions', model=MODEL,
            base_url=f'http://127.0.0.1:{provider.server_port}/v1/source', send_paths=False, stream=True)
        server.app.assistant.save_settings(settings)
        server.app.trace.update(enabled=True)
        for name in ('simulation.sqlite3', 'scan_index.sqlite3', 'developer_trace.sqlite3'):
            with closing(sqlite3.connect((profile / name).as_uri() + '?mode=ro', uri=True)) as origin:
                with closing(sqlite3.connect(native / name)) as target:
                    origin.backup(target)
        ModelAssistant(native).save_settings(dict(settings,
            base_url=f'http://127.0.0.1:{provider.server_port}/v1/native'))
        metadata = dict(app_url=f'http://127.0.0.1:{server.server_port}', source_profile=str(profile),
            native_profile=str(native), provider_stats=f'http://127.0.0.1:{provider.server_port}/stats',
            scope=str(scope), fixture_hashes=hashes(scope), model=MODEL, step_delay=args.step_delay,
            **{key: seeded[key] for key in ('tasks', 'startup_ids', 'service_ids')},
            expected_state_advice=seeded['entries'], minimum_continuous_requests=14,
            external_provider_used=False, real_credentials_used=False,
            limits=['Artificial startup/service metadata and inert files only; noWindows registrations.',
                    'Source inventory replaced; native EXE is unmodified with externally copied fixture data.',
                    'Peer only accepts frozen fixture tasks and IDs; no real provider or keys.',
                    'No collector, cleanup execution, plan approval or system state change is needed.'])
        destination = profile / 'fixture-meta.json'
        destination.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
        threading.Thread(target=server.serve_forever, daemon=True).start()
        print(json.dumps(dict(metadata=str(destination), app_url=metadata['app_url'],
            provider_stats=metadata['provider_stats'], native_profile=str(native), tasks=metadata['tasks']),
            ensure_ascii=False), flush=True)
        while args.serve and not (profile / 'stop-fixture').exists():
            threading.Event().wait(.3)
        if hashes(scope) != metadata['fixture_hashes']:
            raise AssertionError('Owned fixture sentinel files changed.')
    finally:
        if metadata:
            (profile / 'peer-stats.json').write_text(json.dumps(peer.stats, ensure_ascii=False, indent=2), encoding='utf-8')
        server.shutdown()
        server.server_close()
        server.app.close()
        provider.shutdown()
        provider.server_close()
        for patcher in reversed(patchers):
            patcher.stop()


if __name__ == '__main__':
    main()
