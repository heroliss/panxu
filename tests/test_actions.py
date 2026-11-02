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
        runtime = ActionRuntime(self.state)
        result = runtime.run('task', 1, self.entries[:1])
        self.assertEqual(result['items'][0]['status'], 'failed')
        self.assertEqual(self.state.history(), [])
        attempt = runtime.incomplete_history()['attempts'][0]
        self.assertEqual(attempt['status'], 'failed')
        self.assertEqual(attempt['item']['name'], self.entries[0]['name'])
        self.assertIsNone(attempt['batch_id'])

    def test_history_keeps_incomplete_outcomes_and_current_receipts_distinct(self):
        runtime = ActionRuntime(self.state, fault=lambda item, phase: phase == 'after')
        pending = runtime.run('unverified-task', 1, self.entries[:1])
        event = threading.Event()
        event.set()
        runtime.run('cancelled-task', 1, self.entries[1:], cancel=event)
        rows = runtime.incomplete_history()['attempts']
        self.assertEqual({r['status'] for r in rows}, {'unverified', 'cancelled'})
        recorded = next(r for r in rows if r['status'] == 'unverified')
        self.assertEqual(recorded['current_state'], 'recorded')
        self.assertEqual(next(r for r in rows if r['status'] == 'cancelled')['current_state'], 'not_applied')
        self.assertEqual(runtime.history()[0]['items'][0]['verification_status'], 'unverified')
        self.assertEqual(recorded['item']['path'], self.entries[0]['path'])
        self.assertEqual(recorded['actual_reclaimed_bytes'], 0)
        self.state.undo(pending['items'][0]['batch_id'])
        self.assertEqual(next(r for r in runtime.incomplete_history()['attempts']
                              if r['status'] == 'unverified')['current_state'], 'undone')
        self.assertEqual([p.read_bytes() for p in self.scope.iterdir()], [b'keep-real-bytes'] * 2)

    def test_history_is_bounded_and_excludes_success_and_specialist_attempts(self):
        runtime = ActionRuntime(self.state)
        runtime.run('success-task', 1, self.entries[:1])
        cancelled = threading.Event()
        cancelled.set()
        for index in range(3):
            runtime.run('cancel-task-' + str(index), 1, self.entries[1:], cancel=cancelled)
        runtime._save('specialist', 'specialist-task', 1, 'failed',
                      {'entry': dict(self.entries[0], kind='cleanup_tool'), 'backend': 'simulation'})
        limited = runtime.incomplete_history(2)
        self.assertEqual(len(limited['attempts']), 2)
        self.assertTrue(limited['attempts_has_more'])
        all_rows = runtime.incomplete_history()
        self.assertEqual(len(all_rows['attempts']), 3)
        self.assertFalse(all_rows['attempts_has_more'])
        self.assertTrue(all(row['status'] == 'cancelled' for row in all_rows['attempts']))

    def test_history_resolves_partial_restore_and_purge_per_item_without_rewriting_outcome(self):
        runtime = ActionRuntime(self.state)
        plan = self.state.prepare([item['id'] for item in self.entries])
        self.state.execute(plan['id'])
        store = self.state.recovery_store
        records = store.list()['items']
        for row, action in zip(records, ('restore', 'purge')):
            recovery_plan = store.prepare([row['id']], action)
            store.execute(recovery_plan['id'], recovery_plan['confirmation'])
        original = self.state.history()[0]
        before = dict(original)
        projected = runtime.history()[0]
        self.assertEqual({i['current_state'] for i in projected['items']}, {'undone', 'purged'})
        self.assertTrue(all(i['status'] == 'simulated' for i in projected['items']))
        self.assertEqual(self.state.history()[0], before)
        self.assertTrue(all('current_state' not in i for i in original['items']))

    def test_receipt_is_atomic_with_overlay_and_idempotent(self):
        entry = self.entries[0]
        plan = self.state.prepare([entry['id']])
        first = self.state.execute(plan['id'], operation_id='stable-operation')
        second = self.state.execute('not-needed-on-retry', operation_id='stable-operation')
        self.assertEqual(first['batch']['id'], second['batch']['id'])
        self.assertEqual(len(self.state.history()), 1)


if __name__ == '__main__':
    unittest.main()
