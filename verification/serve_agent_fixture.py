"""Local acceptance rig: real HTTP tool loop over disposable files, no real key.

This is test infrastructure, never a product data source or packaged module.
Run in the foreground, use the printed app URL, and stop with Ctrl+C.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from diskpilot.server import create_server


def response(name, args):
    return {'choices': [{'message': {'role': 'assistant', 'content': None, 'tool_calls': [{
        'id': 'fixture_' + name, 'type': 'function',
        'function': {'name': name, 'arguments': json.dumps(args, ensure_ascii=False)}}]}}]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--catalog-size', type=int, default=0)
    args = parser.parse_args()
    stats = {'requests': 0, 'external_provider_used': False, 'real_credentials_used': False}
    with tempfile.TemporaryDirectory(prefix='panxu-agent-acceptance-') as temporary:
        scope = Path(temporary) / '下载测试'
        scope.mkdir()
        for name in ('安装包副本A.zip', '安装包副本B.zip'):
            (scope / name).write_bytes(b'PanXu disposable identical fixture. Keep every real byte.')
        (scope / '家庭照片说明.txt').write_text('Disposable fixture. Not a real photo.', encoding='utf-8')

        class Provider(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                if self.path != '/v1/chat/completions':
                    self.send_error(404)
                    return
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 192 * 1024:
                    self.send_error(400)
                    return
                payload = json.loads(self.rfile.read(size))
                stats['requests'] += 1
                raw = json.dumps(payload, ensure_ascii=False)
                stats['full_fixture_path_absent'] = str(scope).replace('\\', '\\\\') not in raw
                messages = payload['messages']
                context = json.loads(messages[1]['content'])
                if context.get('last_result'):
                    result = context['last_result']
                    answer = response('present_findings', {
                        'summary': f'已核对本轮 {result["succeeded"]}/{result["total"]} 项预演结果。你选择保留的内容会继续保留。',
                        'needs_input': False})
                elif messages[-1]['role'] == 'tool':
                    last = json.loads(messages[-1]['content'])
                    if 'error' in last:
                        answer = response('present_findings', {'summary': '已收到保留要求，本轮没有继续处理。', 'needs_input': True})
                    else:
                        previous = messages[-2]['tool_calls'][0]['function']['name']
                        if previous == 'survey_scope':
                            files = [i for i in last['items'] if i['kind'] == 'file' and i['name'].endswith('.zip')]
                            answer = response('compare_files', {'ids': [i['id'] for i in files]})
                        else:
                            skipped = {c['id'] for c in context['user_choices']}
                            ids = [i for g in last.get('groups', []) if g['type'] == 'content' for i in g['ids'] if i not in skipped]
                            if ids:
                                answer = response('propose_plan', {'summary': '这两份测试安装包内容一致，可以选择清理不需要的副本。',
                                    'items': [dict(id=i, action='delete', reason='已经本地核对，文件内容相同；是否保留由你决定。',
                                                   impact='清理后会少一份下载副本，需要时可重新取得安装包。') for i in dict.fromkeys(ids)]})
                            else:
                                answer = response('present_findings', {'summary': '这些文件你已选择保留，本轮保持原样。', 'needs_input': False})
                else:
                    answer = response('survey_scope', {'scope_id': 'root'})
                encoded = json.dumps(answer, ensure_ascii=False).encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        provider = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
        thread = threading.Thread(target=provider.serve_forever, daemon=True)
        thread.start()
        app_server = create_server(args.data_dir)
        if args.catalog_size:
            from diskpilot.scanner import scan_directory
            large = Path(temporary) / '分页测试'
            large.mkdir()
            for index in range(min(max(args.catalog_size, 0), 10000)):
                group = large / f'批次-{index // 500:02d}'
                group.mkdir(exist_ok=True)
                (group / f'测试下载-{index:05d}.bin').write_bytes(b'fixture' * (index % 20 + 1))
            for index, group in enumerate(large.iterdir()):
                app_server.app.state.publish(scan_directory(str(group))['items'], f'pagination-fixture-{index}')
        app_server.app.assistant.save_settings({'base_url': f'http://127.0.0.1:{provider.server_port}/v1', 'model': 'local-acceptance-fixture'})
        def stop_when_requested():
            marker = args.data_dir / 'stop-fixture'
            while not marker.exists():
                time.sleep(.2)
            app_server.shutdown()
        threading.Thread(target=stop_when_requested, daemon=True).start()
        print(json.dumps({'app_url': f'http://127.0.0.1:{app_server.server_port}', 'scope': str(scope),
                          'provider_url': f'http://127.0.0.1:{provider.server_port}/v1'}, ensure_ascii=False), flush=True)
        try:
            app_server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            app_server.server_close()
            app_server.app.close()
            provider.shutdown()
            provider.server_close()
            stats['real_files_unchanged'] = all((scope / name).read_bytes() == b'PanXu disposable identical fixture. Keep every real byte.' for name in ('安装包副本A.zip', '安装包副本B.zip'))
            args.data_dir.mkdir(parents=True, exist_ok=True)
            (args.data_dir / 'fixture-report.json').write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
