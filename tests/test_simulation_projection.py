"""Internal batch projections preserve journal semantics and public isolation."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from diskpilot.scanner import scan_directory
from diskpilot.simulation import SimulationState


class SimulationProjectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='panxu-batch-projection-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scope = self.root / 'owned'; self.scope.mkdir()
        for name in ('one.bin', 'two.bin'):
            (self.scope / name).write_bytes(b'owned unchanged fixture')
        self.state = SimulationState(self.root / 'state')
        self.addCleanup(self.state.close)
        self.state.publish(scan_directory(str(self.scope))['items'], 'owned-fixture')
        self.entry = next(row for row in self.state.snapshot()['items'] if row['kind'] == 'file')

    def test_prepare_only_copies_selected_objects_and_never_mutates_snapshot(self):
        before_name = self.entry['name']
        with patch.object(self.state, 'view', side_effect=AssertionError('No full inventory copy for preparation')):
            plan = self.state.prepare([self.entry['id']])
        plan['items'][0]['name'] = 'changed isolated plan'
        plan['items'][0]['details']['projection-test'] = True
        current = next(row for row in self.state.snapshot()['items'] if row['id'] == self.entry['id'])
        self.assertEqual(current['name'], before_name)
        self.assertNotIn('projection-test', current['details'])

    def test_batch_projection_and_replay_share_one_atomic_receipt_and_retention_record(self):
        plan = self.state.prepare([self.entry['id']])
        revision = self.state.revision
        with patch.object(self.state, 'view', side_effect=AssertionError('Internal receipt does not need the full inventory')):
            first = self.state.execute(plan['id'], operation_id='owned-projection', include_view=False)
            replay = self.state.execute('consumed-plan', operation_id='owned-projection', include_view=False)
        self.assertEqual(set(first), {'batch'})
        self.assertEqual(first, replay)
        self.assertEqual(first['batch']['actual_reclaimed_bytes'], 0)
        self.assertEqual(self.state.revision, revision + 1)
        self.assertEqual(len(self.state.history()), 1)
        with self.state.lock:
            self.assertEqual(self.state.db.execute('SELECT COUNT(*) FROM overlay WHERE active=1').fetchone()[0], 1)
            self.assertEqual(self.state.db.execute("SELECT COUNT(*) FROM recovery_entries WHERE status='retained'").fetchone()[0], 1)
        # Public replay keeps its full response contract even for a receipt
        # originally produced through the compact internal projection.
        public = self.state.execute('consumed-plan', operation_id='owned-projection')
        self.assertTrue({'mode', 'source', 'items', 'summary', 'history', 'batch'} <= set(public))
        self.assertEqual(public['batch'], first['batch'])
        self.assertTrue(all(path.read_bytes() == b'owned unchanged fixture' for path in self.scope.iterdir()))

    def test_default_execute_returns_an_independent_full_view(self):
        plan = self.state.prepare([self.entry['id']])
        result = self.state.execute(plan['id'])
        self.assertTrue({'mode', 'source', 'items', 'summary', 'history', 'batch'} <= set(result))
        row = next(row for row in result['items'] if row['id'] == self.entry['id'])
        self.assertTrue(row['simulated'])
        row['name'] = 'changed public view'
        cached = next(row for row in self.state.snapshot()['items'] if row['id'] == self.entry['id'])
        self.assertNotEqual(cached['name'], row['name'])


if __name__ == '__main__':
    unittest.main()
