"""Real loopback routes over disposable files; no native launch or live mutation."""
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request

from diskpilot.server import create_server


class WorkspaceRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.files = self.root / 'files'
        self.files.mkdir()
        for number in range(5):
            (self.files / f'{number}.txt').write_bytes(f'owned-{number}'.encode())
        self.before = self.hashes()
        self.server = create_server(self.root / 'state')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close)
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.csrf = self.request('/api/bootstrap?compact=1')['csrf']

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.server.app.close()
        self.thread.join(3)

    def hashes(self):
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in self.files.iterdir()}

    def request(self, path, body=None, expected=200):
        headers, raw = {}, None
        if body is not None:
            headers = {'Content-Type': 'application/json', 'X-CSRF-Token': self.csrf}
            raw = json.dumps(body).encode()
        request = urllib.request.Request(self.url + path, data=raw, headers=headers)
        try:
            response = urllib.request.urlopen(request, timeout=10)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            self.assertEqual(response.status, expected)
            return json.load(response)

    def test_browse_routes_are_paginated_and_read_only(self):
        query = urllib.parse.urlencode({'path': str(self.files), 'limit': 2})
        first = self.request('/api/file-browser?' + query)
        self.assertEqual(len(first['items']), 2)
        self.assertTrue(first['has_more'])
        next_query = urllib.parse.urlencode({'path': str(self.files), 'limit': 2, 'cursor': first['next_cursor']})
        second = self.request('/api/file-browser?' + next_query)
        self.assertFalse({i['id'] for i in first['items']} & {i['id'] for i in second['items']})
        self.assertEqual(self.hashes(), self.before)
        self.assertEqual(self.server.app.state.history(), [])
        self.assertEqual(self.request('/api/recovery/summary')['retained_count'], 0)

    def test_invalid_page_limit_and_unknown_task_are_rejected(self):
        self.request('/api/file-browser?' + urllib.parse.urlencode({'path': str(self.files), 'limit': 101}), expected=400)
        self.request('/api/file-browser?limit=abc', expected=400)
        self.request('/api/file-browser?' + urllib.parse.urlencode({'path': str(self.files), 'task_id': 'unknown'}), expected=404)

    def test_recovery_routes_reject_path_injection_and_empty_selection(self):
        self.request('/api/recovery/prepare', {'ids': [], 'action': 'restore'}, expected=400)
        self.request('/api/recovery/prepare', {'ids': ['invented'], 'action': 'purge', 'path': str(self.files)}, expected=400)
        self.request('/api/recovery/execute', {'plan_id': 'invented', 'confirmation': 'invented'}, expected=404)
        self.assertEqual(self.hashes(), self.before)

    def test_new_static_assets_are_served_by_fixed_allowlist(self):
        for name, mime in [('file-workspace.js', 'text/javascript'), ('file-workspace.css', 'text/css'), ('recovery.js', 'text/javascript'), ('recovery.css', 'text/css'), ('history-presentation.js', 'text/javascript'), ('history.css', 'text/css')]:
            with self.subTest(name=name), urllib.request.urlopen(self.url + '/' + name, timeout=10) as response:
                self.assertEqual(response.status, 200)
                self.assertTrue(response.headers['Content-Type'].startswith(mime))
                self.assertTrue(response.read())


if __name__ == '__main__':
    unittest.main()
