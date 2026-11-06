"""Development only: the real UI against the real backend in a 演练环境, in any browser.

    python tools/ui_harness.py [--port 8766] [--sandbox DIR] [--live]
    python tools/ui_harness.py --native [--stage-delay 2]

Serves the app's HTML bundle on http://127.0.0.1:PORT/ with a shim that sends
``pywebview.api.call`` to ``Bridge.call`` over HTTP, so pages, jobs and the
backend can be checked together without pywebview. Without --live a scripted
model answers (no network). The simulation is kept in %LOCALAPPDATA%\\PanXu\\ui-harness
(rebuild it from the banner). This is a test tool: the app itself never runs a
web server (see docs/DESIGN.md). --native opens the same desktop window in the
simulation, without an HTTP server. Folder pickers and window functions are
only available in that native mode.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
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
    parser.add_argument('--native', action='store_true', help='open an isolated native window instead of a browser server')
    parser.add_argument('--stage-delay', type=float, default=0, help='development only: pause at each checkup stage (0-2 seconds)')
    args = parser.parse_args()

    # one reusable folder (the server is usually stopped by killing it), inside PanXu's own protected data
    # folder rather than %TEMP%, where its tens of GB of sparse files would inflate a real junk scan
    sb = sandbox.Sandbox(args.sandbox or os.path.join(default_data_dir(), 'ui-harness'))
    sandbox.activate(sb)
    window_ref = {}
    backend = Backend(os.path.join(sb.root, 'data'), sandbox=sb, window_provider=lambda: window_ref.get('w'))
    if args.stage_delay:
        start = backend.jobs.start
        def delayed_start(kind, fn, *a, **kw):
            def delayed(job):
                update = job.update
                def progress(**values):
                    update(**values)
                    if 'stage' in values:
                        time.sleep(max(0, min(args.stage_delay, 2)))
                job.update = progress
                return fn(job)
            return start(kind, delayed, *a, **kw)
        backend.jobs.start = delayed_start
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
    if args.native:
        import webview
        from panxu.app import create_app_window
        window_ref['w'] = create_app_window(backend, '盘序（窗口演练）')
        webview.start(gui='edgechromium', storage_path=os.path.join(sb.root, 'data', 'native-webview'))
        backend.jobs.stop()
        sandbox.deactivate()
        return
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


if __name__ == '__main__':
    main()
