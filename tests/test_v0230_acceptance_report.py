"""A failed restart probe must not destroy a successful acceptance receipt."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch


ROOT = Path(__file__).resolve().parents[1]
with patch.object(sys, 'path', [str(ROOT / 'verification'), *sys.path]):
    spec = importlib.util.spec_from_file_location('owned_v0230_acceptance', ROOT / 'verification' / 'check_v0230_release.py')
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)


class AcceptanceReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='v0230-report-test-', dir=ROOT / 'verification')
        self.addCleanup(self.tmp.cleanup)
        self.scope_tmp = tempfile.TemporaryDirectory(prefix='panxu-owned-v0230-')
        self.addCleanup(self.scope_tmp.cleanup)
        self.scope = Path(self.scope_tmp.name)
        for name in checker.NAMES:
            (self.scope / name).write_bytes(b'owned sample')
        self.folder = Path(self.tmp.name)
        self.metadata = self.folder / 'fixture-meta.json'
        self.meta = dict(scope=str(self.scope), fixture_hashes={name: hashlib.sha256(b'owned sample').hexdigest() for name in checker.NAMES},
            provider_url='http://127.0.0.1:12345/v1', provider_stats='http://127.0.0.1:12345/stats',
            tasks={'custom': 'custom-task', 'duplicates': 'duplicate-task'})
        self.metadata.write_text(json.dumps(self.meta), encoding='utf-8')
        self.output = self.folder / 'agent-ui-acceptance.json'
        self.previous = dict(passed=True, status='passed', at='first-pass-time', check_count=1, artifact_sha256=None,
            checks=[{'name': 'first_pass_receipt', 'status': 'passed'}],
            receipts={'recovery_id': 'retained-sample', 'file_task': 'file-task', 'tool_id': 'user-tool', 'tool_version': 2})
        self.output.write_text(json.dumps(self.previous, indent=1), encoding='utf-8')
        self.original = self.output.read_bytes()

    def api(self):
        api = MagicMock()
        def get(path, query=None):
            if path == '/api/health':
                return {'version': checker.__version__, 'mode': 'simulation', 'live_executor_available': False}
            if path == '/api/bootstrap':
                return {'csrf': 'owned-test', 'settings': {'model': 'offline-v0230-peer', 'base_url': self.meta['provider_url'], 'send_paths': False}}
            if path == '/api/agent-budget':
                return {'model_requests': 10, 'tool_calls': 24}
            if path == '/api/recovery':
                return {'items': [{'id': 'retained-sample'}]}
            if path == '/api/tasks/file-task':
                return {'task': {'last_result': {'succeeded': 1}}}
            if path == '/api/tasks/custom-task':
                return {'task': {'last_cleanup_result': {'succeeded': 1}}}
            if path == '/api/user-tools/user-tool':
                return {'version': 2}
            if path == '/api/tasks/duplicate-task':
                return {'task': {'advice_total': 2}}
            raise AssertionError('Unexpected fake route: ' + path)
        api.get.side_effect = get
        api.opener.open.return_value = io.BytesIO(json.dumps({'requests': 10, 'errors': [], 'path_leaks': 0}).encode())
        return api

    def run_check(self, api):
        with patch.object(checker, 'API', return_value=api), contextlib.redirect_stdout(io.StringIO()):
            return checker.run('http://127.0.0.1:12345', self.metadata, self.output, recovery_only=True)

    def test_connection_failure_preserves_first_pass_bytes_and_writes_attempt_separately(self):
        api = self.api(); api.get.side_effect = ConnectionError('owned unavailable peer')
        with self.assertRaises(ConnectionError):
            self.run_check(api)
        self.assertEqual(self.output.read_bytes(), self.original)
        attempts = list(self.folder.glob('agent-ui-acceptance.recovery-failed-*.json'))
        self.assertEqual(len(attempts), 1)
        failed = json.loads(attempts[0].read_text(encoding='utf-8'))
        self.assertFalse(failed['passed'])
        self.assertEqual(failed['preserved_report'], str(self.output.resolve()))
        self.assertEqual(failed['receipts'], self.previous['receipts'])

    def test_later_failure_preserves_retry_receipts_and_prior_checks(self):
        api = self.api(); original = api.get.side_effect
        def later(path, query=None):
            if path == '/api/recovery':
                raise RuntimeError('owned late probe failure')
            return original(path, query)
        api.get.side_effect = later
        with self.assertRaises(RuntimeError):
            self.run_check(api)
        self.assertEqual(self.output.read_bytes(), self.original)
        failed = json.loads(next(self.folder.glob('*.recovery-failed-*.json')).read_text(encoding='utf-8'))
        self.assertGreater(failed['check_count'], self.previous['check_count'])
        self.assertEqual(failed['checks'][0], self.previous['checks'][0])
        self.assertEqual(failed['receipts'], self.previous['receipts'])

    def test_successful_recovery_appends_checks_and_replaces_main_report(self):
        result = self.run_check(self.api())
        self.assertTrue(result['passed']); self.assertTrue(result['recovery_after_restart_verified'])
        self.assertTrue(result['real_fixture_files_unchanged'])
        saved = json.loads(self.output.read_text(encoding='utf-8'))
        self.assertEqual(saved, result)
        self.assertEqual(saved['checks'][0], self.previous['checks'][0])
        self.assertGreater(saved['check_count'], self.previous['check_count'])
        self.assertEqual(saved['receipts'], self.previous['receipts'])
        self.assertEqual(saved['first_pass']['check_count'], self.previous['check_count'])
        self.assertEqual(list(self.folder.glob('*.recovery-failed-*.json')), [])
        self.assertEqual(list(self.folder.glob('*.tmp-*')), [])


if __name__ == '__main__':
    unittest.main()
