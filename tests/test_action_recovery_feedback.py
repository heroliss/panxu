"""Entry-level current state stays separate from historical execution success."""
from pathlib import Path
import tempfile
import unittest

from diskpilot.actions import ActionRuntime
from diskpilot.scanner import scan_directory
from diskpilot.simulation import SimulationState


class ActionRecoveryFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        scope = root / 'fixtures'; scope.mkdir()
        for name in ('first.bin', 'second.bin'): (scope / name).write_bytes(b'original fixture')
        self.state = SimulationState(root / 'state'); self.addCleanup(self.state.close)
        observed = scan_directory(str(scope))['items']; self.state.publish(observed, 'owned')
        self.entries = [dict(item, action='delete') for item in observed if item['kind'] == 'file']
        self.runtime = ActionRuntime(self.state)

    def change(self, record, action):
        plan = self.state.recovery_store.prepare([record['id']], action=action)
        return self.state.recovery_store.execute(plan['id'], plan['confirmation'])

    def test_restore_updates_current_receipt_without_erasing_success_or_reexecuting(self):
        before = self.runtime.run('task', 1, self.entries[:1])
        record = self.state.recovery_store.list()['items'][0]
        self.change(record, 'restore')
        current = self.runtime.result('task', 1)
        self.assertEqual(current['succeeded'], 1)
        self.assertTrue(current['items'][0]['verified'])
        self.assertEqual(current['items'][0]['current_state'], 'undone')
        self.assertEqual(current['items'][0]['recovery_status'], 'restored')
        self.assertEqual(current['undone_actions'], {'delete': 1})
        self.assertEqual(current['purged'], 0)
        self.assertEqual(self.runtime.run('task', 1, self.entries[:1]), current)
        self.assertEqual(current['items'][0]['batch_id'], before['items'][0]['batch_id'])
        self.assertEqual(Path(self.entries[0]['path']).read_bytes(), b'original fixture')

    def test_purge_is_a_distinct_current_state_after_legacy_undo_and_reset(self):
        self.runtime.run('task', 1, self.entries[:1])
        record = self.state.recovery_store.list()['items'][0]
        self.change(record, 'purge')
        batch_id = self.runtime.result('task', 1)['items'][0]['batch_id']
        self.state.undo(batch_id); self.state.reset()
        result = self.runtime.result('task', 1)
        self.assertEqual(result['succeeded'], 1)
        self.assertEqual(result['undone'], 0)
        self.assertEqual(result['purged'], 1)
        self.assertEqual(result['purged_actions'], {'delete': 1})
        self.assertEqual(result['action_results']['delete']['purged'], 1)
        self.assertEqual(result['items'][0]['current_state'], 'purged')
        self.assertEqual(result['items'][0]['recovery_status'], 'purged')
        self.assertTrue(next(item for item in self.state.view()['items'] if item['id'] == record['item_id'])['simulated'])

    def test_mixed_legacy_batch_uses_exact_entry_recovery_not_batch_label(self):
        # Exercise a historical multi-target batch: one sibling restored while
        # another is permanently purged. Its batch status cannot describe both.
        plan = self.state.prepare([entry['id'] for entry in self.entries])
        batch = self.state.execute(plan['id'])['batch']
        for index, entry in enumerate(self.entries):
            self.runtime._save('owned-op-' + str(index), 'task', 1, 'succeeded',
                dict(entry=entry, backend='simulation', verified=True, batch_id=batch['id'], message='Recorded originally'))
        records = self.state.recovery_store.list()['items']
        self.change(records[0], 'restore'); self.change(records[1], 'purge')
        result = self.runtime.result('task', 1)
        self.assertEqual(result['succeeded'], 2)
        self.assertEqual(result['undone'], 1)
        self.assertEqual(result['purged'], 1)
        self.assertEqual({row['current_state'] for row in result['items']}, {'undone', 'purged'})
        self.assertEqual(result['actual_reclaimed_bytes'], 0)

    def test_no_receipt_never_masquerades_as_recorded(self):
        self.runtime._save('uncommitted', 'task', 1, 'failed',
            dict(entry=self.entries[0], backend='simulation', verified=False, message='Never committed'))
        self.assertEqual(self.runtime.result('task', 1)['items'][0]['current_state'], 'unverified')


if __name__ == '__main__':
    unittest.main()
