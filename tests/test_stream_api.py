"""Real loopback HTTP/SSE with disposable profiles and an in-process model peer."""
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest.mock import Mock, patch

from diskpilot.server import create_server


def frame(value):
    return ('data: ' + json.dumps(value, ensure_ascii=False) + '\n\n').encode()


class StreamApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        self.profile = self.folder / 'profile'
        self.scope = self.folder / 'scope'
        self.scope.mkdir()
        self.sentinel = self.scope / 'source.txt'
        self.sentinel.write_bytes(b'disposable source content must stay unchanged')
        self.source_hash = hashlib.sha256(self.sentinel.read_bytes()).hexdigest()
        self.server = create_server(self.profile)
        self.app = self.server.app
        self.app.assistant._windows = False  # synthetic key stays in memory on every platform
        self.app.assistant._transport = Mock(side_effect=AssertionError('external transport is forbidden'))
        self.thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=.02))
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.clients = []

    def tearDown(self):
        self.app.live_streams.close()  # wake every SSE waiter before disposing databases
        for client in self.clients:
            client.close()
        self.server.shutdown()
        self.thread.join(2)
        self.server.server_close()
        self.app.close()
        try:
            self.assertFalse(self.thread.is_alive())
            self.assertEqual(hashlib.sha256(self.sentinel.read_bytes()).hexdigest(), self.source_hash)
        finally:
            self.tmp.cleanup()

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=3) as response:
            return json.load(response)

    def post(self, path, payload, *, csrf=None):
        request = urllib.request.Request(self.base + path, data=json.dumps(payload).encode(), headers={
            'Content-Type': 'application/json', 'X-CSRF-Token': self.app.csrf if csrf is None else csrf})
        with urllib.request.urlopen(request, timeout=3) as response:
            return json.load(response)

    def error(self, expected, operation):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            operation()
        response = caught.exception
        try:
            self.assertEqual(response.code, expected)
            return json.load(response)
        finally:
            response.close()

    def task(self):
        return self.app.harness.create('只分析可丢弃测试目录，不处理文件。', str(self.scope))['id']

    def connect(self, task_id):
        response = urllib.request.urlopen(self.base + f'/api/tasks/{task_id}/stream/events', timeout=3)
        self.clients.append(response)
        return response

    def read_event(self, response):
        data = []
        for _ in range(128):
            line = response.readline().decode('utf-8')
            self.assertTrue(line, 'SSE must remain open until a complete snapshot arrives')
            if line.startswith('data:'):
                data.append(line[5:].lstrip().rstrip('\r\n'))
            elif not line.strip() and data:
                return json.loads('\n'.join(data))
        self.fail('SSE framing exceeded the test read budget')

    def read_until(self, response, predicate):
        for _ in range(16):
            event = self.read_event(response)
            if predicate(event['stream']):
                return event['stream']
        self.fail('the expected real SSE snapshot did not arrive')

    def configured(self):
        return self.post('/api/settings', {'provider': 'deepseek', 'protocol': 'chat_completions',
            'base_url': 'http://127.0.0.1:12345/v1', 'model': 'deepseek-v4-pro',
            'api_key': 'fixture-local-key-only', 'stream': True})['settings']

    def test_shared_preferences_preserve_provider_connection_and_in_memory_key(self):
        before = self.configured()
        key = self.app.assistant._api_key
        result = self.post('/api/preferences', {'custom_instructions': '  保留源码与照片  '})
        self.assertEqual(result['preferences'], {'custom_instructions': '保留源码与照片'})
        self.assertEqual(result['settings']['custom_instructions'], '保留源码与照片')
        for field in ('provider', 'protocol', 'base_url', 'model', 'stream', 'send_paths', 'has_key'):
            self.assertEqual(result['settings'][field], before[field], field)
        self.assertTrue(result['settings']['has_key'])
        self.assertEqual(self.app.assistant._api_key, key)
        self.assertNotIn(key, json.dumps(result))
        self.assertNotIn(key, self.app.assistant._file.read_text(encoding='utf-8'))
        self.app.assistant._transport.assert_not_called()
        self.assertFalse(self.app.jobs, 'saving preferences does not start analysis')

    def test_preferences_reject_connection_overrides_and_invalid_values_atomically(self):
        self.configured()
        before = self.app.assistant.public_settings()
        key = self.app.assistant._api_key
        for payload in ({}, {'custom_instructions': False}, {'custom_instructions': 'x' * 4001},
                        {'custom_instructions': 'new', 'provider': 'custom'},
                        {'custom_instructions': 'new', 'api_key': 'replacement'}):
            with self.subTest(keys=list(payload)):
                result = self.error(400, lambda: self.post('/api/preferences', payload))
                self.assertEqual(result['code'], 'invalid_preferences')
                self.assertEqual(self.app.assistant.public_settings(), before)
                self.assertEqual(self.app.assistant._api_key, key)
        self.error(403, lambda: self.post('/api/preferences', {'custom_instructions': 'new'}, csrf='wrong'))

    def test_connection_probe_failure_does_not_rollback_saved_settings_or_key(self):
        before = self.configured()
        key = self.app.assistant._api_key
        with patch.object(self.app.assistant, 'test_connection', side_effect=ValueError('fixture unavailable')):
            result = self.error(400, lambda: self.post('/api/settings/test', {}))
        self.assertEqual(result['code'], 'connection_test_failed')
        self.assertEqual(self.app.assistant.public_settings(), before)
        self.assertEqual(self.app.assistant._api_key, key)
        self.assertEqual(json.loads(self.app.assistant._file.read_text(encoding='utf-8'))['model'], before['model'])

    def test_unknown_tasks_are_404_even_when_a_preview_exists_for_that_id(self):
        self.app.live_streams.record('unknown-task', dict(kind='turn_start', turn_id='one', text='must stay hidden'))
        for suffix in ('stream', 'stream/events'):
            with self.subTest(suffix=suffix):
                result = self.error(404, lambda: self.get('/api/tasks/unknown-task/' + suffix))
                self.assertEqual(result['code'], 'unknown_task')
                self.assertNotIn('must stay hidden', json.dumps(result))

    def test_json_fallback_and_sse_have_legal_null_and_task_isolated_public_snapshots(self):
        first, second = self.task(), self.task()
        self.assertEqual(self.get(f'/api/tasks/{first}/stream'), {'stream': None, 'volatile': True})
        response = self.connect(first)
        self.assertEqual(response.status, 200)
        self.assertTrue(response.headers['Content-Type'].startswith('text/event-stream'))
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
        self.assertEqual(self.read_event(response), {'stream': None})
        self.app.assistant.stream_hook(dict(kind='turn_start', turn_id='outside-context', text='not a task'))
        with self.app.trace.context(first):
            self.app.assistant.stream_hook(dict(kind='turn_start', turn_id='one', protocol='chat_completions', text='公开正文'))
        streamed = self.read_until(response, lambda row: row and row['text'] == '公开正文')
        self.assertEqual(streamed['status'], 'streaming')
        self.assertEqual(self.get(f'/api/tasks/{first}/stream')['stream'], streamed)
        self.assertIsNone(self.get(f'/api/tasks/{second}/stream')['stream'])
        self.assertEqual(self.app.trace.page()['total'], 0, 'live preview does not silently enable capture')

    def test_sse_connection_limit_is_bounded_and_unknown_task_checks_happen_first(self):
        task_id = self.task()
        for _ in range(4):
            self.assertEqual(self.read_event(self.connect(task_id)), {'stream': None})
        result = self.error(429, lambda: self.get(f'/api/tasks/{task_id}/stream/events'))
        self.assertEqual(result['code'], 'stream_limit')
        self.error(404, lambda: self.get('/api/tasks/not-a-task/stream/events'))

    def test_stream_assets_are_served_as_local_scripts(self):
        for path, marker in (('/stream-view.js', 'PanxuStreamView'), ('/stream-channel.js', 'PanxuStreamChannel')):
            with urllib.request.urlopen(self.base + path, timeout=3) as response:
                self.assertTrue(response.headers['Content-Type'].startswith('text/javascript'))
                self.assertIn(marker, response.read().decode('utf-8'))
                self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_real_sse_delivers_partial_public_text_before_completion_without_tool_execution(self):
        self.configured()
        task_id = self.task()
        response = self.connect(task_id)
        self.assertEqual(self.read_event(response), {'stream': None})
        partial_ready, finish = threading.Event(), threading.Event()
        results, errors = [], []
        tools = [{'type': 'function', 'function': {'name': 'inspect_items', 'parameters': {
            'type': 'object', 'properties': {'ids': {'type': 'array', 'items': {'type': 'string'}}}, 'required': ['ids']}}}]

        def chunks():
            yield frame({'choices': [{'index': 0, 'delta': {'content': '公开片段。', 'reasoning_content': 'private-reasoning-canary'}}]})
            time.sleep(.06)  # cross the real adapter's 50ms public preview coalescing window
            yield frame({'choices': [{'index': 0, 'delta': {'tool_calls': [{'index': 0, 'id': 'fixture-call',
                'type': 'function', 'function': {'name': 'inspect_items', 'arguments': '{"ids":'}}]}}]})
            partial_ready.set()
            if not finish.wait(3):
                raise AssertionError('fixture release timeout')
            yield frame({'choices': [{'index': 0, 'delta': {'tool_calls': [{'index': 0,
                'function': {'arguments': '["private-tool-target-canary"]}'}}]}, 'finish_reason': 'tool_calls'}]})
            yield b'data: [DONE]\n\n'

        self.app.assistant._transport = lambda *_args: chunks()

        def request_model():
            try:
                with self.app.trace.context(task_id):
                    results.append(self.app.assistant.agent_turn([{'role': 'user', 'content': '测试公开回复'}], tools))
            except BaseException as error:
                errors.append(error)

        worker = threading.Thread(target=request_model)
        before_revision = self.app.state.revision
        with patch.object(self.app.harness, 'tool', wraps=self.app.harness.tool) as call_tool:
            worker.start()
            try:
                self.assertTrue(partial_ready.wait(2))
                partial = self.read_until(response, lambda row: row and row['tool_pending'])
                self.assertTrue(worker.is_alive(), 'the source model stream is deliberately still blocked')
                self.assertEqual(partial['status'], 'streaming')
                self.assertEqual(partial['text'], '公开片段。')
                self.assertNotIn('private-reasoning-canary', json.dumps(partial))
                self.assertNotIn('arguments', partial)
                self.assertNotIn('tool_calls', partial)
                self.assertFalse(results, 'partial tool arguments must not be returned as a complete response')
                finish.set()
                worker.join(3)
                self.assertFalse(worker.is_alive())
                self.assertFalse(errors, repr(errors))
                completed = self.read_until(response, lambda row: row and row['status'] == 'completed')
                self.assertEqual(completed['text'], '公开片段。')
                self.assertNotIn('private-tool-target-canary', json.dumps(completed))
                self.assertEqual(json.loads(results[0]['tool_calls'][0]['function']['arguments']), {'ids': ['private-tool-target-canary']})
                call_tool.assert_not_called()
            finally:
                finish.set()
                worker.join(3)
        self.assertEqual(self.app.state.revision, before_revision)
        with self.app.state.lock:
            for table in ('action_attempts', 'operation_receipts', 'batches', 'overlay'):
                self.assertEqual(self.app.state.db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0], 0, table)
        self.assertFalse(self.app.jobs)


if __name__ == '__main__':
    unittest.main()
