"""Receipt grouping is read-only metadata, never a broader undo identity."""
from pathlib import Path
import tempfile
import unittest

from diskpilot.actions import ActionRuntime
from diskpilot.scanner import scan_directory
from diskpilot.simulation import SimulationState


class HistoryGroupingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='panxu-history-group-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scope = self.root / 'owned'
        self.scope.mkdir()
        for name in ('a.tmp', 'b.tmp', 'c.tmp'):
            (self.scope / name).write_bytes(b'owned grouping sample, physically unchanged')
        self.state = SimulationState(self.root / 'profile')
        self.addCleanup(self.state.close)
        entries = scan_directory(str(self.scope))['items']
        self.state.publish(entries, 'owned-history-group')
        self.entries = [dict(row, action='delete') for row in entries if row['kind'] == 'file']
        self.runtime = ActionRuntime(self.state)

    def test_success_receipts_get_exact_task_and_version_without_verification_warning(self):
        self.runtime.run('task-a', 1, self.entries[:2])
        self.runtime.run('task-a', 2, self.entries[2:])
        rows = self.runtime.history()
        self.assertEqual(len(rows), 3)
        self.assertEqual(sum(row['operation_group'] == {'task_id': 'task-a', 'plan_version': 1} for row in rows), 2)
        self.assertEqual(sum(row['operation_group'] == {'task_id': 'task-a', 'plan_version': 2} for row in rows), 1)
        self.assertTrue(all('verification_status' not in row['items'][0] for row in rows))
        self.assertTrue(all('operation_group' not in row for row in self.state.history()))

    def test_legacy_multi_item_batch_has_no_invented_runtime_group(self):
        plan = self.state.prepare([row['id'] for row in self.entries])
        receipt = self.state.execute(plan['id'])['batch']
        row = self.runtime.history()[0]
        self.assertEqual(row['id'], receipt['id'])
        self.assertEqual(len(row['items']), 3)
        self.assertNotIn('operation_group', row)

    def test_independent_task_metadata_and_current_undo_do_not_rewrite_original_outcome(self):
        first = self.runtime.run('task-a', 1, self.entries[:1])
        self.runtime.run('task-b', 1, self.entries[1:2])
        receipt = first['items'][0]['batch_id']
        self.state.undo(receipt)
        rows = {row['operation_group']['task_id']: row for row in self.runtime.history()}
        self.assertEqual(rows['task-a']['items'][0]['status'], 'simulated')
        self.assertEqual(rows['task-a']['items'][0]['current_state'], 'undone')
        self.assertEqual(rows['task-b']['items'][0]['current_state'], 'recorded')
        self.assertEqual([path.read_bytes() for path in self.scope.iterdir()], [b'owned grouping sample, physically unchanged'] * 3)

    def test_unverified_commit_keeps_both_group_identity_and_verification_warning(self):
        runtime = ActionRuntime(self.state, fault=lambda entry, phase: phase == 'after')
        runtime.run('task-a', 1, self.entries[:1])
        row = runtime.history()[0]
        self.assertEqual(row['operation_group'], {'task_id': 'task-a', 'plan_version': 1})
        self.assertEqual(row['items'][0]['verification_status'], 'unverified')
        self.assertEqual(row['items'][0]['current_state'], 'recorded')


if __name__ == '__main__':
    unittest.main()
