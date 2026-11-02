"""Disposable end-to-end fixture for list analysis, tools and connection testing.

No external model or credentials. Every original file is checked on shutdown.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import argparse
import hashlib
import json
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from diskpilot.server import create_server
from diskpilot.scanner import scan_directory
from tests.prompt_fixture import parse_host_data


def tool(name, args):
    return {'choices': [{'message': {'role': 'assistant', 'content': None, 'tool_calls': [
        {'id': 'fixture_' + name, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args, ensure_ascii=False)}}]}}]}


def answer(payload):
    messages = payload['messages']
    definitions = {t['function']['name']: t['function'] for t in payload.get('tools', [])}
    if 'connection_probe' in definitions:
        if messages[-1]['role'] == 'tool':
            nonce = parse_host_data(messages[-1]['content'])['nonce']
            return {'choices': [{'message': {'role': 'assistant', 'content': '工具往返成功 ' + nonce}}]}
        nonce = definitions['connection_probe']['parameters']['properties']['nonce']['enum'][0]
        return tool('connection_probe', {'nonce': nonce})
    context = parse_host_data(messages[1]['content'])
    if context.get('last_result'):
        n = context['last_result']['succeeded']
        return tool('present_findings', {'summary': f'已核对本轮 {n} 项预演结果，实际文件未改变。', 'needs_input': False})
    if len(messages) == 2:
        return tool('discover_tools', {}) if 'discover_tools' in definitions else tool('list_items', {})
    previous = messages[-2]['tool_calls'][0]['function']['name']
    if previous == 'discover_tools':
        return tool('list_items', {'limit': 30})
    if previous == 'list_items':
        result = parse_host_data(messages[-1]['content'])
        rows = result.get('items', [])
        if not rows and context.get('scopes'):
            return tool('survey_scope', {'scope_id': context['scopes'][0]['id']})
        if rows:
            return tool('inspect_items', {'ids': [r['id'] for r in rows[:20]]})
        return tool('present_findings', {'summary': '当前范围没有待分析内容。', 'needs_input': False})
    if previous == 'survey_scope':
        return tool('list_items', {'limit': 30})
    observed = []
    for message in messages:
        if message['role'] == 'tool':
            value = parse_host_data(message['content'])
            observed += value.get('items', []) if isinstance(value, dict) else []
    rows = {r['id']: r for r in observed if isinstance(r, dict) and 'kind' in r and 'id' in r}
    skipped = {c['id'] for c in context.get('user_choices', [])}
    candidates = [r for r in rows.values() if r['id'] not in skipped and r.get('risk') != 'protected'
                  and not r.get('user_kept') and r.get('kind') == 'file' and r['name'].endswith('.zip')]
    if previous == 'inspect_items' and 'record_advice' in definitions:
        return tool('record_advice', {'items': [dict(id=r['id'], conclusion='review' if r in candidates else 'keep',
            purpose='可丢弃的测试下载副本。' if r in candidates else '应保留的测试资料。',
            reason='演示如何先说明用途，再由用户决定。', impact='测试保护下只记录结果，实际文件继续保留。')
            for r in list(rows.values())[:30]]})
    if candidates:
        return tool('propose_plan', {'summary': '已分析这两份测试安装包。可以选择不需要的副本，个人文件不会默认勾选。',
            'items': [dict(id=r['id'], action='delete', reason='测试安装包副本，最终去留由你决定。',
                           impact='预演只记录选择，不改变实际文件。') for r in candidates]})
    return tool('present_findings', {'summary': '已查看当前结果，本轮建议保留。尚未调查的内容可继续分析。', 'needs_input': False})


def write_response(handler, value, payload, status=200):
    """Default JSON peer; newer fixtures may replace only this wire writer."""
    raw = json.dumps(value, ensure_ascii=False).encode()
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json')
    handler.send_header('Content-Length', str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--catalog-size', type=int, default=3000)
    args = parser.parse_args()
    stats = {'requests': 0, 'external_provider_used': False, 'real_credentials_used': False}
    with tempfile.TemporaryDirectory(prefix='panxu-v050-') as tmp:
        root = Path(tmp)
        scope = root / '下载测试'
        scope.mkdir()
        for name in ('安装包副本A.zip', '安装包副本B.zip'):
            (scope / name).write_bytes(b'PanXu disposable unchanged fixture.')
        (scope / '个人资料.txt').write_text('Keep this fixture.', encoding='utf-8')
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in scope.iterdir()}

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
                try:
                    value = answer(payload)
                    status = 200
                except Exception as exc:
                    value, status = {'error': {'message': type(exc).__name__}}, 500
                write_response(self, value, payload, status)

        provider = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
        threading.Thread(target=provider.serve_forever, daemon=True).start()
        server = create_server(args.data_dir)
        app = server.app
        app.state.publish(scan_directory(str(scope))['items'], 'test-scope')
        large = root / '分页测试'
        large.mkdir()
        for index in range(min(max(args.catalog_size, 0), 10000)):
            group = large / f'批次-{index // 500:02d}'
            group.mkdir(exist_ok=True)
            (group / f'测试下载-{index:05d}.bin').write_bytes(b'x' * (index % 30 + 1))
        for index, group in enumerate(large.iterdir()):
            app.state.publish(scan_directory(str(group))['items'], f'pagination-{index}')
        app.assistant.save_settings({'provider': 'custom', 'protocol': 'chat_completions',
            'base_url': f'http://127.0.0.1:{provider.server_port}/v1', 'model': 'local-acceptance-fixture'})

        def watch():
            while not (args.data_dir / 'stop-fixture').exists():
                time.sleep(.2)
            server.shutdown()
        threading.Thread(target=watch, daemon=True).start()
        print(json.dumps({'app_url': f'http://127.0.0.1:{server.server_port}', 'scope': str(scope),
                          'provider_url': f'http://127.0.0.1:{provider.server_port}/v1'}, ensure_ascii=False), flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            app.close()
            provider.shutdown()
            provider.server_close()
            stats['real_files_unchanged'] = all(p.exists() and hashlib.sha256(p.read_bytes()).hexdigest() == h for p, h in before.items())
            (args.data_dir / 'fixture-report.json').write_text(json.dumps(stats, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
