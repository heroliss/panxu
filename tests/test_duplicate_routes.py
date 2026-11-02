"""Independent folder lookup must never create model or cleanup work."""
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from diskpilot.server import Application
from diskpilot.simulation import AppError


class DuplicateRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scope = self.root / 'owned-files'
        self.scope.mkdir()
        (self.scope / 'a.bin').write_bytes(b'local-file' * 100)
        (self.scope / 'b.bin').write_bytes(b'local-file' * 100)
        (self.scope / 'c.bin').write_bytes(b'other-file' * 100)
        self.before = {p.name: p.read_bytes() for p in self.scope.iterdir()}
        self.app = Application(self.root / 'data')
        self.addCleanup(self.app.close)

    def wait(self, key):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            job = self.app.job(key)
            if job['status'] != 'running':
                return job
            time.sleep(.01)
        self.fail('owned fixture did not finish in time')

    def test_local_scan_without_model_or_catalog_publication(self):
        old = self.app.state.snapshot([])
        with patch.object(self.app.assistant, 'agent_turn', side_effect=AssertionError('no model')):
            data = self.app.post('/api/duplicates/scan', {'paths': [str(self.scope)]})
            job = self.wait(data['job_id'])
        self.assertEqual(job['status'], 'completed')
        result = job['result']
        self.assertEqual(result['algorithm'], 'sha256')
        self.assertFalse(result['partial'])
        self.assertEqual(result['summary']['content_groups'], 1)
        self.assertEqual({x['name'] for x in result['groups'][0]['items']}, {'a.bin', 'b.bin'})
        self.assertEqual(self.app.state.snapshot([]), old)
        self.assertIsNone(self.app.harness.latest())
        self.assertFalse(self.app.state.history())
        self.assertNotIn('_cancel', job)
        self.assertEqual({p.name: p.read_bytes() for p in self.scope.iterdir()}, self.before)

    def test_invalid_request_does_not_start_background_work(self):
        for payload in ({'paths': ['relative']}, {'paths': [str(self.scope)], 'delete': True},
                        {'paths': [str(self.scope)], 'max_bytes': True}):
            with self.assertRaises(AppError):
                self.app.post('/api/duplicates/scan', payload)
        self.assertFalse(self.app.jobs)

    def test_budget_partial_is_not_reported_as_complete_inventory(self):
        data = self.app.post('/api/duplicates/scan', {'paths': [str(self.scope)], 'max_bytes': 1})
        job = self.wait(data['job_id'])
        self.assertEqual(job['status'], 'completed')
        self.assertTrue(job['result']['partial'])
        self.assertFalse(job['result']['groups'])
        self.assertLessEqual(job['result']['bytes_read'], 1)
        self.assertEqual({p.name: p.read_bytes() for p in self.scope.iterdir()}, self.before)


if __name__ == '__main__':
    unittest.main()
