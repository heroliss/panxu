from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from diskpilot.actions import ActionRuntime, SimulationBackend
from diskpilot.scanner import scan_directory
from diskpilot.simulation import SimulationState


class Interrupted(BaseException):
    pass


class ActionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.scope = self.root / 'fixtures'
        self.scope.mkdir()
        for name in ('a.bin', 'b.bin'):
            (self.scope / name).write_bytes(b'keep-real-bytes')
        self.state = SimulationState(self.root / 'state')
        report = scan_directory(str(self.scope))
        self.state.publish(report['items'], 'fixture')
        self.entries = [dict(i, action='delete') for i in report['items'] if i['kind'] == 'file']

    def tearDown(self):
        self.state.close()
        self.tmp.cleanup()

    def test_partial_failure_is_journaled_and_actual_files_stay_unchanged(self):
        bad = self.entries[1]['id']
        runtime = ActionRuntime(self.state, fault=lambda item, phase: item['id'] == bad and phase == 'before')
        with patch('os.remove', side_effect=AssertionError('real deletion')), patch('os.rename', side_effect=AssertionError('real move')):
            result = runtime.run('task', 1, self.entries)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['succeeded'], 1)
        self.assertEqual(result['actual_reclaimed_bytes'], 0)
        self.assertEqual([p.read_bytes() for p in self.scope.iterdir()], [b'keep-real-bytes'] * 2)
        # A lost HTTP response/retry cannot repeat either outcome.
        self.assertEqual(runtime.run('task', 1, self.entries), result)
        self.assertEqual(len(self.state.history()), 1)

    def test_apply_receipt_recovers_crash_after_virtual_change_before_result_write(self):
        def crash(item, phase):
            if phase == 'after':
                raise Interrupted()
        runtime = ActionRuntime(self.state, fault=crash)
        with self.assertRaises(Interrupted):
            runtime.run('task', 1, self.entries[:1])
        self.assertEqual(len(self.state.history()), 1)
        recovered = ActionRuntime(self.state)
        recovered.recover()
        result = recovered.run('task', 1, self.entries[:1])
        self.assertEqual(result['status'], 'succeeded')
        self.assertTrue(result['items'][0]['verified'])
        self.assertEqual(len(self.state.history()), 1)

    def test_unverified_result_and_cancel_are_not_success(self):
        runtime = ActionRuntime(self.state, fault=lambda item, phase: phase == 'after')
        result = runtime.run('task', 1, self.entries[:1])
        self.assertEqual(result['items'][0]['status'], 'unverified')
        cancellation = threading.Event()
        cancellation.set()
        result = runtime.run('task', 2, self.entries[1:], cancel=cancellation)
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual(len(self.state.history()), 1)

    def test_changed_target_fails_before_virtual_apply(self):
        file = Path(self.entries[0]['path'])
        file.write_bytes(b'changed-after-review')
        result = ActionRuntime(self.state).run('task', 1, self.entries[:1])
        self.assertEqual(result['items'][0]['status'], 'failed')
        self.assertEqual(self.state.history(), [])

    def test_receipt_is_atomic_with_overlay_and_idempotent(self):
        entry = self.entries[0]
        plan = self.state.prepare([entry['id']])
        first = self.state.execute(plan['id'], operation_id='stable-operation')
        second = self.state.execute('not-needed-on-retry', operation_id='stable-operation')
        self.assertEqual(first['batch']['id'], second['batch']['id'])
        self.assertEqual(len(self.state.history()), 1)


if __name__ == '__main__':
    unittest.main()
