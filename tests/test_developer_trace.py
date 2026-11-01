from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from diskpilot.trace import DeveloperTrace


class DeveloperTraceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.trace = DeveloperTrace(self.tmp.name, lambda text: text.replace('fixture-secret', '[REDACTED]'))
        self.addCleanup(self.trace.close)

    def test_capture_is_opt_in_and_not_retroactive(self):
        self.trace.record({'kind': 'request', 'payload': 'before opt in'})
        self.assertEqual(self.trace.page()['total'], 0)
        self.trace.update(enabled=True)
        self.trace.record({'kind': 'request', 'payload': 'captured'})
        page = self.trace.page()
        self.assertEqual(page['total'], 1)
        self.assertIsNotNone(page['capture_started_at'])
        self.trace.update(enabled=False)
        self.trace.record({'kind': 'response', 'payload': 'after disabled'})
        self.assertEqual(self.trace.page()['total'], 1)

    def test_full_protocol_size_payload_preserved_but_credentials_and_reasoning_not(self):
        self.trace.update(enabled=True)
        content = 'request data ' * 18000
        self.trace.record({'kind': 'request', 'endpoint': 'https://fixture.example/v1', 'payload': {
            'messages': [{'role': 'user', 'content': content}], 'authorization': 'Bearer fixture-secret',
            'reasoning_content': 'never store this reasoning', 'thinking': {'type': 'enabled', 'clear_thinking': False},
            '_continuation': {'private': 'opaque content'}, 'echo': 'fixture-secret'}})
        event = self.trace.page()['entries'][0]['payload']
        self.assertEqual(event['payload']['messages'][0]['content'], content)
        self.assertEqual(event['payload']['thinking']['type'], 'enabled')
        raw = json.dumps(event)
        self.assertNotIn('fixture-secret', raw)
        self.assertNotIn('never store this reasoning', raw)
        self.assertNotIn('opaque content', raw)
        self.assertIn('fixture.example', event['endpoint'])

    def test_context_is_thread_local_and_restored(self):
        self.trace.update(enabled=True)
        def record(task):
            with self.trace.context(task):
                self.trace.record({'kind': 'tool_call', 'payload': {'task': task}})
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(record, ('task-a', 'task-b')))
        self.trace.record({'kind': 'response', 'payload': 'without task'})
        rows = self.trace.page()['entries']
        self.assertIsNone(rows[0]['task_id'])
        self.assertEqual({r['task_id'] for r in rows[1:]}, {'task-a', 'task-b'})
        self.assertTrue(all(r['payload']['payload']['task'] == r['task_id'] for r in rows[1:]))

    def test_count_and_byte_retention_are_bounded_and_pages_disjoint(self):
        self.trace.update(enabled=True)
        with patch('diskpilot.trace.MAX_ENTRIES', 5):
            for index in range(8):
                self.trace.record({'kind': 'response', 'payload': index})
        first, second = self.trace.page(limit=2), self.trace.page(offset=2, limit=2)
        self.assertEqual(first['total'], 5)
        self.assertTrue(first['has_more'])
        self.assertFalse({r['seq'] for r in first['entries']} & {r['seq'] for r in second['entries']})
        with patch('diskpilot.trace.MAX_BYTES', 6000):
            for _ in range(5):
                self.trace.record({'kind': 'response', 'payload': 'x' * 2500})
        self.assertLessEqual(self.trace.db.execute('SELECT SUM(bytes) FROM entries').fetchone()[0], 6000)

    def test_oversize_is_explicit_not_silent_and_clear_removes_entries(self):
        self.trace.update(enabled=True)
        with patch('diskpilot.trace.MAX_EVENT_BYTES', 1000):
            self.trace.record({'kind': 'response', 'payload': 'x' * 2000})
        payload = self.trace.page()['entries'][0]['payload']
        self.assertTrue(payload['truncated'])
        self.assertGreater(payload['original_bytes'], 1000)
        self.assertTrue(self.trace.update(clear=True)['enabled'])
        self.assertEqual(self.trace.page()['total'], 0)


if __name__ == '__main__':
    unittest.main()
