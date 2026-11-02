import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
import time
from unittest.mock import patch

from diskpilot.server import create_server


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.server = create_server(Path(cls.tmp.name), developer_fixtures=True)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'
        cls.csrf = cls.get('/api/bootstrap')['csrf']

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.server.app.close()
        cls.tmp.cleanup()

    @classmethod
    def get(cls, path):
        with urllib.request.urlopen(cls.base + path, timeout=10) as response:
            return json.load(response)

    def post(self, path, payload, *, csrf=None, origin=None):
        headers = {'Content-Type': 'application/json', 'X-CSRF-Token': self.csrf if csrf is None else csrf}
        if origin:
            headers['Origin'] = origin
        request = urllib.request.Request(self.base + path, data=json.dumps(payload).encode(), headers=headers)
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)

    def test_health_says_live_executor_unavailable(self):
        value = self.get('/api/health')
        self.assertEqual(value['mode'], 'simulation')
        self.assertFalse(value['live_executor_available'])

    def test_history_includes_incomplete_attempts_without_changing_batch_contract(self):
        expected = dict(attempts=[dict(id='test-attempt', status='failed')], attempts_has_more=False)
        with patch.object(self.server.app.harness.runtime, 'incomplete_history', return_value=expected):
            value = self.get('/api/history')
        self.assertIsInstance(value['history'], list)
        self.assertEqual(value['attempts'], expected['attempts'])
        self.assertFalse(value['attempts_has_more'])

    def test_specialist_assets_are_served_with_the_application(self):
        for asset, mime in [('/cleanup-tools.js', 'text/javascript'), ('/specialist-links.js', 'text/javascript'),
                            ('/cleanup-tools.css', 'text/css')]:
            with self.subTest(asset=asset), urllib.request.urlopen(self.base + asset, timeout=10) as response:
                self.assertEqual(response.status, 200)
                self.assertTrue(response.headers['Content-Type'].startswith(mime))
                self.assertGreater(len(response.read()), 100)

    def test_icon_endpoint_streams_an_image_with_pending_metadata(self):
        value = dict(body=b'\x89PNG\r\n\x1a\nfixture', mime='image/png',
                     etag='"fixture-hash"', status='pending', retry_after=1)
        with patch.object(self.server.app.icons, 'get', return_value=value) as lookup:
            with urllib.request.urlopen(self.base + '/api/icon?id=known-item') as response:
                self.assertEqual(response.read(), value['body'])
                self.assertEqual(response.headers['Content-Type'], 'image/png')
                self.assertEqual(response.headers['X-Panxu-Icon-Status'], 'pending')
                self.assertEqual(response.headers['Retry-After'], '1')
                self.assertEqual(response.headers['Cache-Control'], 'no-store')
                self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
            lookup.assert_called_once_with('known-item')

    def test_icon_endpoint_does_not_offer_an_arbitrary_path_reader(self):
        for path, expected in (('/api/icon?id=missing-item', 404),
                               ('/api/icon?path=C%3A%5CWindows%5Cexplorer.exe', 400),
                               ('/api/icon?id=a&id=b', 400)):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                urllib.request.urlopen(self.base + path, timeout=10)
            self.assertEqual(caught.exception.code, expected)
            caught.exception.close()

    def test_csrf_and_foreign_origin_rejected(self):
        for options in ({'csrf': 'invalid'}, {'origin': 'https://evil.example'}):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.post('/api/reset', {}, **options)
            self.assertEqual(ctx.exception.code, 403)
            ctx.exception.close()

    def test_no_real_executor_or_arbitrary_file_endpoint(self):
        for path in ('/api/real-delete', '/api/execute-live', '/api/kill', '/api/shell'):
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                self.post(path, {'path': r'C:\Windows'})
            self.assertEqual(ctx.exception.code, 404)
            ctx.exception.close()

    def test_demo_mixed_plan_execute_undo(self):
        self.post('/api/demo', {})
        self.post('/api/reset', {})
        plan = self.post('/api/plan', {'ids': ['demo-cache-file', 'demo-startup', 'demo-service', 'demo-process']})
        value = self.post('/api/execute', {'plan_id': plan['id']})
        self.assertEqual(value['batch']['item_count'], 4)
        self.assertEqual(value['summary']['actual_reclaimed_bytes'], 0)
        undone = self.post('/api/undo', {'batch_id': value['batch']['id']})
        self.assertEqual(undone['summary']['simulated_items'], 0)

    def test_default_assistant_local_no_key_required(self):
        self.post('/api/demo', {})
        result = self.post('/api/assistant', {'question': '这些缓存可以处理吗', 'ids': ['demo-cache-file']})
        self.assertEqual(result['mode'], 'local')
        self.assertTrue(result['answer'])

    def test_static_page_has_csp_and_no_path_traversal(self):
        with urllib.request.urlopen(self.base + '/') as response:
            self.assertIn("script-src 'self'", response.headers['Content-Security-Policy'])
            self.assertIn('盘序', response.read().decode())
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get('/../run.py')
        self.assertEqual(ctx.exception.code, 404)
        ctx.exception.close()

    def test_failed_scan_keeps_old_catalog_and_redacts_invalid_limits(self):
        before = {x['id'] for x in self.get('/api/bootstrap')['items']}
        for payload in ({'path': str(Path(self.tmp.name) / 'absent')},
                        {'path': '..'},
                        {'path': self.tmp.name, 'max_entries': 'Bearer test-private-value'}):
            job = self.post('/api/scan', payload)
            for _ in range(100):
                result = self.get('/api/jobs/' + job['job_id'])
                if result['status'] != 'running':
                    break
                time.sleep(.01)
            self.assertEqual(result['status'], 'failed')
            self.assertNotIn('test-private-value', json.dumps(result))
            self.assertEqual(before, {x['id'] for x in self.get('/api/bootstrap')['items']})

    def test_task_creation_and_resume_require_valid_local_scope(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post('/api/tasks', {'goal': '整理', 'path': '..'})
        self.assertEqual(ctx.exception.code, 400)
        ctx.exception.close()
        directory = Path(self.tmp.name) / 'task-scope'
        directory.mkdir(exist_ok=True)
        result = self.post('/api/tasks', {'goal': '整理这个位置。', 'path': str(directory)})
        for _ in range(100):
            task = self.get('/api/tasks/' + result['task']['id'])['task']
            if task['status'] != 'investigating':
                break
            time.sleep(.01)
        self.assertEqual(task['status'], 'needs_model')
        self.assertEqual(task['backend'], 'simulation')
        self.assertIn(task['id'], [t['id'] for t in self.get('/api/tasks')['tasks']])
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post('/api/tasks/' + task['id'] + '/approve', {'version': 1, 'ids': ['not-a-proposal']})
        self.assertEqual(ctx.exception.code, 409)
        ctx.exception.close()


if __name__ == '__main__':
    unittest.main()
