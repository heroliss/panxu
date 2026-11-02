"""Recovery is a durable simulated journal, never a filesystem executor."""
import hashlib
import json
import ntpath
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from diskpilot.catalog import FileCatalog
from diskpilot.file_browser import DirectoryBrowser
from diskpilot.recovery import RecoveryStore
from diskpilot.scanner import scan_directory
from diskpilot.simulation import AppError, SimulationState


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='panxu-recovery-owned-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.scope = self.root / 'scope'
        self.scope.mkdir()
        self.file = self.scope / 'owned.bin'
        self.file.write_bytes(b'owned original bytes')
        self.data = self.root / 'data'
        self.state = SimulationState(self.data)
        self.addCleanup(lambda: self.state.close())
        self.store = RecoveryStore(self.state)
        self.scan()

    def scan(self):
        rows = scan_directory(str(self.scope))['items']
        self.state.publish(rows, 'fixture')
        self.item = next(row for row in rows if row['path'] == str(self.file))
        return rows

    def delete(self, ids=None):
        return self.state.execute(self.state.prepare(ids or [self.item['id']])['id'])

    def entry(self):
        return self.store.list()['items'][0]

    def run_plan(self, ids, action):
        plan = self.store.prepare(ids, action)
        return self.store.execute(plan['id'], plan['confirmation'])

    def restart(self):
        self.state.close()
        self.state = SimulationState(self.data)
        self.store = RecoveryStore(self.state)

    def simulated(self, ident=None):
        return next(row['simulated'] for row in self.state.snapshot()['items'] if row['id'] == (ident or self.item['id']))

    def test_delete_retains_original_metadata_and_plans_only_same_volume(self):
        before = hashlib.sha256(self.file.read_bytes()).hexdigest()
        with patch('os.rename', side_effect=AssertionError('no move')), patch('os.remove', side_effect=AssertionError('no delete')), patch.object(Path, 'mkdir', side_effect=AssertionError('no recovery directory')):
            result = self.delete()
            entry = self.entry()
        self.assertEqual(entry['status'], 'retained')
        self.assertEqual(entry['batch_id'], result['batch']['id'])
        self.assertEqual(entry['original_path'], str(self.file))
        self.assertEqual(entry['snapshot']['signature'], self.item['signature'])
        self.assertEqual(entry['size_bytes'], self.file.stat().st_size)
        self.assertFalse(entry['analysis_recorded'])
        self.assertIsNone(entry['assessment'])
        self.assertIsNone(entry['auto_expiry'])
        self.assertFalse(entry['actual_moved'])
        self.assertFalse(entry['storage']['volume_identity_verified'])
        self.assertFalse(entry['storage']['same_volume_verified'])
        self.assertEqual(ntpath.splitdrive(entry['storage']['planned_path'])[0].casefold(), ntpath.splitdrive(str(self.file))[0].casefold())
        self.assertEqual(before, hashlib.sha256(self.file.read_bytes()).hexdigest())
        self.assertEqual(self.store.summary()['actual_reclaimed_bytes'], 0)

    def test_background_operations_do_not_create_file_recovery_entries(self):
        self.state.replace_demo()
        self.state.execute(self.state.prepare(['demo-startup'])['id'])
        self.assertEqual(self.store.summary()['retained_count'], 0)

    def test_existing_assessment_is_frozen_without_new_analysis(self):
        original = dict(state='explicit', rating=4, reason='Original accepted reason', purpose='Fixture', source_ref={'task_id': 'original'})
        calls = []
        def resolve(items):
            calls.append(len(items))
            return {self.item['id']: original}
        self.store.assessment_resolver = resolve
        self.delete()
        original['reason'] = 'Later changed advice'
        entry = self.entry()
        self.assertTrue(entry['analysis_recorded'])
        self.assertEqual(entry['assessment']['reason'], 'Original accepted reason')
        self.assertEqual(len(calls), 1)
        self.restart()
        self.assertEqual(self.entry()['assessment']['rating'], 4)

    def test_snapshot_on_plan_takes_priority_over_current_resolver(self):
        self.item['assessment'] = dict(state='explicit', rating=3, reason='Frozen plan reason')
        self.state.publish([self.item], 'fixture-file')
        self.store.assessment_resolver = lambda items: {self.item['id']: dict(state='explicit', rating=5, reason='Later source')}
        self.delete()
        self.assertEqual(self.entry()['assessment']['rating'], 3)

    def test_restore_requires_review_confirmation_and_unmasks_only_its_record(self):
        self.delete()
        entry = self.entry()
        plan = self.store.prepare([entry['id']], 'restore')
        self.assertEqual(plan['status'], 'awaiting_review')
        self.assertTrue(self.simulated())
        with self.assertRaises(AppError): self.store.execute(plan['id'], 'incorrect')
        self.assertTrue(self.simulated())
        result = self.store.execute(plan['id'], plan['confirmation'])
        self.assertEqual(result['items'][0]['status'], 'restored')
        self.assertFalse(self.simulated())
        self.assertEqual(self.store.list(status='restored')['items'][0]['storage']['journal_state'], 'simulated_restored')
        self.assertEqual(self.state.history()[0]['status'], 'undone')

    def test_purge_cannot_be_reversed_by_old_undo_or_reset_or_restart(self):
        deleted = self.delete()
        entry = self.entry()
        self.run_plan([entry['id']], 'purge')
        self.state.undo(deleted['batch']['id'])
        self.assertTrue(self.simulated())
        self.assertEqual(self.state.history()[0]['status'], 'partially_undone')
        self.state.reset()
        self.assertTrue(self.simulated())
        self.restart()
        self.assertTrue(self.simulated())
        self.assertEqual(self.store.list(status='purged')['items'][0]['status'], 'purged')
        with self.assertRaises(AppError): self.store.prepare([entry['id']], 'restore')
        self.assertTrue(self.file.exists())

    def test_concurrent_plans_freeze_entry_revision_and_second_action_is_rejected(self):
        self.delete()
        entry = self.entry()
        restore = self.store.prepare([entry['id']], 'restore')
        purge = self.store.prepare([entry['id']], 'purge')
        self.store.execute(purge['id'], purge['confirmation'])
        with self.assertRaises(AppError) as error: self.store.execute(restore['id'], restore['confirmation'])
        self.assertEqual(error.exception.code, 'recovery_entry_changed')
        self.assertTrue(self.simulated())

    def test_durable_completed_plan_is_a_readable_idempotent_receipt(self):
        self.delete()
        plan = self.store.prepare([self.entry()['id']], 'restore')
        result = self.store.execute(plan['id'], plan['confirmation'])
        revision = self.store.revision()
        self.restart()
        self.assertEqual(self.store.get_plan(plan['id'])['result'], result)
        self.assertEqual(result['affected_batch_ids'], [result['items'][0]['batch_id']])
        self.assertEqual(self.store.get_plan(plan['id'])['status'], 'completed')
        self.assertEqual(self.store.execute(plan['id'], plan['confirmation']), result)
        self.assertEqual(self.store.revision(), revision)

    def test_waiting_plan_survives_restart_and_can_be_confirmed(self):
        self.delete()
        plan = self.store.prepare([self.entry()['id']], 'restore')
        self.restart()
        self.assertEqual(self.store.get_plan(plan['id'])['items'], plan['items'])
        self.store.execute(plan['id'], plan['confirmation'])
        self.assertFalse(self.simulated())

    def test_expired_plan_is_readable_but_cannot_execute(self):
        self.delete()
        plan = self.store.prepare([self.entry()['id']], 'restore')
        with self.state.db:
            self.state.db.execute('UPDATE recovery_plans SET expires_at=? WHERE id=?', ('2000-01-01T00:00:00+00:00', plan['id']))
        self.assertEqual(self.store.get_plan(plan['id'])['status'], 'expired')
        with self.assertRaises(AppError): self.store.execute(plan['id'], plan['confirmation'])
        self.assertTrue(self.simulated())

    def test_malformed_confirmation_is_a_controlled_client_error(self):
        self.delete()
        plan = self.store.prepare([self.entry()['id']], 'restore')
        for value in (None, True, 123, [], {}, 'é' * 32, 'a' * 10000):
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(AppError) as error: self.store.execute(plan['id'], value)
                self.assertEqual(error.exception.status, 400)
                self.assertEqual(error.exception.code, 'recovery_confirmation_required')
        self.assertEqual(self.store.get_plan(plan['id'])['status'], 'awaiting_review')

    def test_changed_object_cannot_be_restored_and_purging_old_version_does_not_hide_new_version(self):
        self.delete()
        old = self.entry()
        self.file.write_bytes(b'owned replacement version')
        self.scan()
        self.assertFalse(self.simulated())
        with self.assertRaises(AppError): self.store.prepare([old['id']], 'restore')
        self.run_plan([old['id']], 'purge')
        self.assertFalse(self.simulated())

    def test_delete_entry_overlay_and_receipt_are_one_transaction(self):
        plan = self.state.prepare([self.item['id']])
        actual = self.store.retain_in_transaction
        def fail(*args):
            actual(*args)
            raise RuntimeError('injected journal failure')
        with patch.object(self.store, 'retain_in_transaction', fail):
            with self.assertRaises(RuntimeError): self.state.execute(plan['id'], operation_id='failed-delete')
        self.assertEqual(self.store.summary()['retained_count'], 0)
        self.assertEqual(self.state.history(), [])
        self.assertIsNone(self.state.receipt('failed-delete'))
        self.assertFalse(self.simulated())
        self.state.execute(plan['id'], operation_id='failed-delete')
        self.assertEqual(self.store.summary()['retained_count'], 1)

    def test_restore_batch_is_atomic_on_journal_failure(self):
        second = self.scope / 'second.bin'
        second.write_bytes(b'owned second fixture')
        rows = self.scan()
        self.delete([row['id'] for row in rows if row['kind'] == 'file'])
        entries = self.store.list()['items']
        plan = self.store.prepare([entry['id'] for entry in entries], 'restore')
        actual, calls = self.store._change_entry, []
        def fail(row, status):
            calls.append(row['id'])
            actual(row, status)
            if len(calls) == 2: raise RuntimeError('injected restore failure')
        with patch.object(self.store, '_change_entry', fail):
            with self.assertRaises(RuntimeError): self.store.execute(plan['id'], plan['confirmation'])
        self.assertEqual(self.store.summary()['retained_count'], 2)
        self.assertEqual(self.store.get_plan(plan['id'])['status'], 'awaiting_review')
        self.assertTrue(self.simulated())

    def test_historical_backfill_is_once_only_and_does_not_invent_lost_snapshots(self):
        payload = dict(items=[dict(id=self.item['id'], name=self.item['name'], path=self.item['path'], kind='file', action='delete')], item_count=1, estimated_bytes=999)
        with self.state.db:
            self.state.db.execute("DELETE FROM meta WHERE key='recovery_backfill_v1'")
            self.state.db.execute('INSERT INTO batches VALUES(?,?,?,?)', ('legacy-owned', '2020-01-01T00:00:00+00:00', 'simulated', json.dumps(payload)))
            self.state.db.execute('INSERT INTO overlay VALUES(?,?,?,?,1)', ('legacy-owned', self.item['id'], self.item['signature'], 'delete'))
        self.store = RecoveryStore(self.state)
        entry = self.entry()
        self.assertTrue(entry['historical'])
        self.assertFalse(entry['historical_snapshot_available'])
        self.assertIsNone(entry['assessment'])
        self.assertIsNone(entry['size_bytes'])
        self.assertEqual(self.store.summary()['unknown_size_count'], 1)
        self.assertEqual(entry['deleted_at'], '2020-01-01T00:00:00+00:00')
        self.store = RecoveryStore(self.state)
        self.assertEqual(self.store.list()['total'], 1)

    def test_backfill_of_undone_batch_remains_restored(self):
        payload = dict(items=[dict(id='legacy', name='past', path='D:\\owned\\past.bin', kind='file', action='delete')])
        with self.state.db:
            self.state.db.execute("DELETE FROM meta WHERE key='recovery_backfill_v1'")
            self.state.db.execute('INSERT INTO batches VALUES(?,?,?,?)', ('undone-owned', '2020', 'undone', json.dumps(payload)))
        self.store = RecoveryStore(self.state)
        self.assertEqual(self.store.summary()['retained_count'], 0)
        self.assertEqual(self.store.list(status='restored')['total'], 1)

    def test_summary_groups_known_logical_size_by_volume_and_does_not_count_background(self):
        self.state.replace_demo()
        self.state.execute(self.state.prepare(['demo-cache-file', 'demo-installer', 'demo-startup'])['id'])
        summary = self.store.summary()
        self.assertEqual(summary['retained_count'], 2)
        self.assertEqual(summary['file_count'], 2)
        self.assertEqual(summary['directory_count'], 0)
        self.assertEqual({entry['volume'] for entry in summary['volumes']}, {'C:\\', 'D:\\'})
        self.assertEqual(summary['logical_bytes'], 73400320 + 230686720)

    def test_pagination_filters_and_maximum_100_are_explicit(self):
        rows = []
        for index in range(125):
            rows.append(dict(self.item, id='owned-' + str(index), name=str(index), path='D:\\owned\\' + str(index),
                             signature='synthetic-' + str(index), details=dict(synthetic=True), size_bytes=index))
        self.state.publish(rows, 'synthetic-owned')
        self.delete([row['id'] for row in rows])
        first, second = self.store.list(limit=100), self.store.list(page=2, limit=100)
        self.assertEqual(len(first['items']), 100)
        self.assertTrue(first['has_more'])
        self.assertEqual(len(second['items']), 25)
        self.assertFalse(second['has_more'])
        self.assertEqual(self.store.list(volume='D:\\')['total'], 125)
        with self.assertRaises(AppError): self.store.list(limit=101)
        with self.assertRaises(AppError): self.store.prepare([first['items'][0]['id']] * 2)

    def test_mixed_batch_undo_restores_background_but_not_permanently_deleted_file(self):
        self.state.replace_demo()
        result = self.state.execute(self.state.prepare(['demo-cache-file', 'demo-startup'])['id'])
        self.run_plan([self.entry()['id']], 'purge')
        self.state.undo(result['batch']['id'])
        flags = {row['id']: row['simulated'] for row in self.state.snapshot()['items']}
        self.assertTrue(flags['demo-cache-file'])
        self.assertFalse(flags['demo-startup'])

    def test_global_reset_restores_retained_and_preserves_purged(self):
        self.state.replace_demo()
        self.state.execute(self.state.prepare(['demo-cache-file', 'demo-installer'])['id'])
        entries = {entry['item_id']: entry for entry in self.store.list()['items']}
        self.run_plan([entries['demo-cache-file']['id']], 'purge')
        self.state.reset()
        self.assertTrue(self.simulated('demo-cache-file'))
        self.assertFalse(self.simulated('demo-installer'))
        self.assertEqual(self.store.list(status='restored')['total'], 1)

    def test_child_restore_requires_parent_restore_and_batch_can_restore_both(self):
        folder = self.scope / 'folder'
        folder.mkdir()
        inner = folder / 'inside.bin'
        inner.write_bytes(b'owned inside')
        rows = self.scan()
        child = next(row for row in rows if row['path'] == str(inner))
        parent = next(row for row in rows if row['path'] == str(folder))
        self.delete([child['id']])
        child_entry = self.entry()
        self.delete([parent['id']])
        parent_entry = next(entry for entry in self.store.list()['items'] if entry['item_id'] == parent['id'])
        with self.assertRaises(AppError): self.store.prepare([child_entry['id']], 'restore')
        self.run_plan([child_entry['id'], parent_entry['id']], 'restore')
        self.assertFalse(self.simulated(child['id']))
        self.assertFalse(self.simulated(parent['id']))

    def test_purged_file_cannot_reappear_by_rescan_or_browser_refresh(self):
        self.delete()
        self.run_plan([self.entry()['id']], 'purge')
        self.state.publish(scan_directory(str(self.scope))['items'], 'filesystem:' + str(self.scope))
        self.assertTrue(self.simulated())
        browser = DirectoryBrowser(self.state, FileCatalog(self.state, lambda: []), time_budget=1)
        self.addCleanup(browser.close)
        self.assertNotIn(self.item['id'], [row['id'] for row in browser.page(str(self.scope), refresh=True)['items']])

    def test_old_purged_parent_does_not_block_new_directory_generation(self):
        folder = self.scope / 'folder'
        folder.mkdir()
        inner = folder / 'inside.bin'
        inner.write_bytes(b'owned old inside')
        rows = self.scan()
        parent = next(row for row in rows if row['path'] == str(folder))
        self.delete([parent['id']])
        old = self.entry()
        self.run_plan([old['id']], 'purge')
        # A changed directory snapshot is a different generation at the same
        # path, even when its path-based catalogue id remains stable.
        inner.write_bytes(b'owned new directory generation')
        replacement = folder / 'new.bin'
        replacement.write_bytes(b'owned replacement child')
        rows = self.scan()
        new_parent = next(row for row in rows if row['path'] == str(folder))
        self.assertNotEqual(parent['signature'], new_parent['signature'])
        child = next(row for row in rows if row['path'] == str(replacement))
        self.delete([child['id']])
        newer = next(entry for entry in self.store.list()['items'] if entry['item_id'] == child['id'])
        self.assertTrue(newer['can_restore'])
        self.run_plan([newer['id']], 'restore')
        self.assertFalse(self.simulated(child['id']))
        self.assertEqual(self.store.list(status='purged')['items'][0]['id'], old['id'])

    def test_partial_restore_retains_unrelated_background_overlay_and_batch_state(self):
        self.state.replace_demo()
        result = self.state.execute(self.state.prepare(['demo-cache-file', 'demo-startup'])['id'])
        self.run_plan([self.entry()['id']], 'restore')
        flags = {row['id']: row['simulated'] for row in self.state.snapshot()['items']}
        self.assertFalse(flags['demo-cache-file'])
        self.assertTrue(flags['demo-startup'])
        self.assertEqual(self.state.history()[0]['status'], 'partially_undone')
        self.state.undo(result['batch']['id'])
        self.assertFalse(self.simulated('demo-startup'))

    def test_explicit_development_fixtures_are_not_retained_after_fixture_discard(self):
        self.state.replace_demo()
        self.state.execute(self.state.prepare(['demo-cache-file'])['id'])
        plan = self.store.prepare([self.entry()['id']], 'purge')
        self.state.discard_development_fixtures()
        self.assertEqual(self.store.list(status='all')['total'], 0)
        with self.assertRaises(AppError): self.store.get_plan(plan['id'])


if __name__ == '__main__':
    unittest.main()
