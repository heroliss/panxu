"""Development only: the real UI against the real backend in a 演练环境, in any browser.

    python tools/ui_harness.py [--port 8766] [--sandbox DIR] [--live]

Serves the app's HTML bundle on http://127.0.0.1:PORT/ with a shim that sends
``pywebview.api.call`` to ``Bridge.call`` over HTTP, so pages, jobs and the
backend can be checked together without pywebview. Without --live a scripted
model answers (no network). This is a test tool: the app itself never runs a
web server (see docs/DESIGN.md). Folder pickers and window functions are not
available here.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import sys
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from panxu import sandbox, ui_bundle  # noqa: E402
from panxu.api import Backend, Bridge  # noqa: E402
from panxu.app import default_data_dir  # noqa: E402
from panxu.settings import Settings  # noqa: E402
from sandbox_e2e import ScriptedModel, live_key  # noqa: E402

SHIM = """<script>
window.pywebview = { api: { call: async (method, params) => {
  const r = await fetch('/call?t=%s', { method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ method, params: params || {} }) });
  return r.json();
} } };
</script>"""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--sandbox', metavar='DIR', help='use (and keep) this simulation folder')
    parser.add_argument('--live', action='store_true', help='use the configured real model')
    args = parser.parse_args()

    root = args.sandbox or tempfile.mkdtemp(prefix='panxu-ui-')
    sb = sandbox.Sandbox(root if args.sandbox else os.path.join(root, 'sb'))
    sandbox.activate(sb)
    backend = Backend(os.path.join(sb.root, 'data'), sandbox=sb)
    if args.live:
        key, source = live_key()
        if not key:
            raise SystemExit('--live: 没有找到 API Key')
        ai = Settings(default_data_dir()).get()['ai']
        backend.settings.update({'ai': ai})
        backend.settings.set_secret('ai_key:' + ai['preset'], key)
        print(f'model: {ai["preset"]} / {ai["model"]} (key from {source})')
    else:
        model = ScriptedModel()
        backend.client_or_none = lambda purpose='classify': model
        backend._client = lambda purpose='classify': model
        backend.settings.set_secret('ai_key:' + backend.settings.get()['ai']['preset'], 'scripted')
    bridge = Bridge(backend)
    token = secrets.token_hex(8)  # other local pages cannot drive the backend without it
    page = ui_bundle.build().replace('<!-- mock -->', SHIM % token).encode('utf-8')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body: bytes, kind: str):
            self.send_response(code)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.split('?')[0] != '/':
                return self._send(404, b'not found', 'text/plain')
            self._send(200, page, 'text/html; charset=utf-8')

        def do_POST(self):
            if self.path != f'/call?t={token}':
                return self._send(403, b'forbidden', 'text/plain')
            req = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
            out = bridge.call(req.get('method'), req.get('params') or {})
            self._send(200, json.dumps(out, ensure_ascii=False).encode('utf-8'), 'application/json')

    server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    print(f'演练环境: {sb.root}\nhttp://127.0.0.1:{args.port}/', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        sandbox.deactivate()
        if not args.sandbox:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    main()
