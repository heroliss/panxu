"""User intent and direct batch preparation never depend on a model call."""
import copy
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from diskpilot.dispositions import DispositionStore
from diskpilot.scanner import scan_directory
from diskpilot.server import Application
from diskpilot.simulation import AppError
from diskpilot.windows import _item


class ItemDispositionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='panxu-intent-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.scope = self.root / 'owned'
        self.scope.mkdir()
        (self.scope / 'a.txt').write_text('owned fixture')
        (self.scope / 'b.txt').write_text('owned fixture')
        self.app = Application(self.root / 'profile')
        self.addCleanup(self.app.close)
        self.app.state.publish(scan_directory(str(self.scope))['items'], 'owned')
        self.startup = _item('startup', 'owned-registration', 'Owned startup', 'Owned fixture', 'startup', 'disable_startup',
                             dict(synthetic=False, registry_location='owned'), path='registration/owned')
        self.app.state.publish([self.startup], 'owned-app')
        self.app.harness.runtime.backend.validator = lambda items: None
        rows = self.app.state.snapshot()['items']
        self.parent = next(row for row in rows if row['path'] == str(self.scope))
        self.a = next(row for row in rows if row['name'] == 'a.txt')
        self.b = next(row for row in rows if row['name'] == 'b.txt')

    def set(self, row, value):
        return self.app.post('/api/item-dispositions', dict(id=row['id'], disposition=value, snapshot=row['signature']))

    def prepare(self, *rows):
        return self.app.post('/api/actions/plan', dict(ids=[row['id'] for row in rows],
            snapshots={row['id']: row['signature'] for row in rows}, revision=self.app.catalog.token()))

    def test_four_values_persist_and_clear_without_changing_catalog_or_files(self):
        for value in ('lean_clean', 'lean_keep', 'direct_clean', 'direct_keep'):
            item = self.set(self.a, value)['item']
            self.assertEqual(item['user_disposition']['value'], value)
            self.assertFalse(item['user_disposition']['stale'])
            self.assertEqual(DispositionStore(self.app.state).current()[self.a['id']]['value'], value)
        self.assertIsNone(self.set(self.a, None)['item']['user_disposition'])
        self.assertTrue((self.scope / 'a.txt').exists())
        self.assertEqual(self.app.state.history(), [])

    def test_stale_snapshot_and_extra_fields_rejected(self):
        with self.assertRaises(AppError):
            self.app.post('/api/item-dispositions', dict(id=self.a['id'], disposition='direct_clean', snapshot='stale'))
        with self.assertRaises(AppError):
            self.app.post('/api/item-dispositions', dict(id=self.a['id'], disposition='lean_clean', snapshot=self.a['signature'], execute=True))

    def test_directory_inheritance_and_specific_override(self):
        self.set(self.parent, 'lean_keep')
        item = self.app.get('/api/item', {'id': [self.a['id']]})['item']
        self.assertEqual(item['user_disposition']['value'], 'lean_keep')
        self.assertTrue(item['user_disposition']['inherited'])
        self.set(self.a, 'lean_clean')
        self.assertEqual(self.app.get('/api/item', {'id': [self.a['id']]})['item']['user_disposition']['value'], 'lean_clean')

    def test_replacement_signature_does_not_inherit_old_direct_authority(self):
        self.set(self.a, 'direct_clean')
        new = dict(self.a, signature='replacement')
        self.app.state.publish([new], 'replacement')
        item = self.app.get('/api/item', {'id': [self.a['id']]})['item']
        self.assertTrue(item['user_disposition']['stale'])
        self.assertIsNone(self.app.harness.assessments.dispositions.strong(new))

    def test_direct_keep_survives_edits_but_not_entity_replacement(self):
        self.set(self.a, 'direct_keep')
        edited = copy.deepcopy(self.a)
        edited['signature'] = 'changed-content-same-identity'
        self.app.state.publish([edited], 'edited-file')
        self.assertEqual(self.app.harness.assessments.dispositions.strong(edited), 'direct_keep')
        replacement = copy.deepcopy(edited)
        replacement['details']['file_identity'] = 'new-volume:new-inode'
        self.app.state.publish([replacement], 'replacement-file')
        self.assertIsNone(self.app.harness.assessments.dispositions.strong(replacement))

    def test_direct_keep_directory_protects_new_observed_children_same_identity(self):
        self.set(self.parent, 'direct_keep')
        edited = dict(self.parent, signature='new-subtree-same-directory-identity')
        self.app.state.publish([edited], 'edited-directory')
        self.assertEqual(self.app.harness.assessments.dispositions.strong(self.b), 'direct_keep')
        with self.assertRaises(AppError):
            self.prepare(self.b)

    def test_direct_keep_blocks_itself_and_parent_direct_plan(self):
        self.set(self.a, 'direct_keep')
        for row in (self.a, self.parent):
            with self.assertRaises(AppError):
                self.prepare(row)
        self.assertTrue((self.scope / 'a.txt').exists())

    def test_parent_logical_selection_expands_around_direct_keep(self):
        self.set(self.a, 'direct_keep')
        page = self.app.catalog.page()
        result = self.app.catalog.compile_selection([self.parent['id']], snapshots={self.parent['id']: self.parent['signature']},
            expected=page['revision'], expected_selection_revision=page['selection_revision'])
        self.assertEqual(result['ids'], [self.b['id']])

    def test_direct_clean_is_only_intent_and_prepares_without_model(self):
        self.set(self.startup, 'direct_clean')
        with patch.object(self.app.assistant, 'agent_turn', side_effect=AssertionError('no model')):
            result = self.prepare(self.startup)
        self.assertEqual(result['plan']['items'][0]['action'], 'disable_startup')
        self.assertEqual(result['picked_ids'], [self.startup['id']])
        self.assertEqual(self.app.state.history(), [])

    def test_selection_revision_changes_after_preference(self):
        revision = self.app.catalog.token()
        self.set(self.a, 'lean_clean')
        self.assertNotEqual(revision, self.app.catalog.token())
        with self.assertRaises(AppError):
            self.app.post('/api/actions/plan', dict(ids=[self.a['id']], snapshots={self.a['id']: self.a['signature']}, revision=revision))

    def test_default_analysis_skips_strong_choices(self):
        self.set(self.a, 'direct_clean')
        self.set(self.b, 'direct_keep')
        with patch.object(self.app, 'start_job', side_effect=AssertionError('no model job')):
            with self.assertRaises(AppError) as caught:
                self.app.post('/api/tasks/analyze', dict(ids=[self.a['id'], self.b['id']], section='files'))
        self.assertEqual(caught.exception.code, 'nothing_to_analyze')

    def test_weak_preference_visible_in_metadata_and_context(self):
        self.set(self.a, 'lean_clean')
        task = self.app.harness.create_analysis(dict(ids=[self.a['id']]))
        result = self.app.harness.tool(task['id'], 'list_items', {}, cancel=threading.Event())
        self.assertEqual(result['items'][0]['user_disposition']['value'], 'lean_clean')
        self.assertEqual(self.app.harness._context(self.app.harness._get(task['id']))['user_dispositions'][0]['value'], 'lean_clean')

    def test_analysis_skips_valid_prior_and_allows_explicit_reanalysis(self):
        task = self.app.harness.create_analysis(dict(ids=[self.a['id']]))
        self.app.harness.tool(task['id'], 'record_advice', {'items': [dict(id=self.a['id'], conclusion='review',
            purpose='fixture', reason='fixture', impact='fixture', cleanup_rating=3)]}, cancel=threading.Event())
        with self.assertRaises(AppError) as caught:
            self.app.harness.create_analysis(dict(ids=[self.a['id']], skip_analyzed=True))
        self.assertEqual(caught.exception.code, 'nothing_to_analyze')
        forced = self.app.harness.create_analysis(dict(ids=[self.a['id']], skip_analyzed=True, reanalyze=True))
        self.assertEqual(forced['analysis']['target_count'], 1)

    def test_applications_projection_contains_intent_and_revision(self):
        self.set(self.startup, 'lean_keep')
        result = self.app.get('/api/applications', {})
        row = next(row for row in result['items'] if row['id'] == self.startup['id'])
        self.assertEqual(row['user_disposition']['value'], 'lean_keep')
        self.assertEqual(result['revision'], self.app.catalog.token())

    def test_current_task_advice_skipped_by_default_but_context_explicitly_readable(self):
        task = self.app.harness.create_analysis(dict(ids=[self.a['id']]))
        self.app.harness.tool(task['id'], 'record_advice', {'items': [dict(id=self.a['id'], conclusion='review',
            purpose='fixture', reason='fixture', impact='fixture', cleanup_rating=3)]}, cancel=threading.Event())
        self.assertEqual(self.app.harness.tool(task['id'], 'list_items', {}, cancel=threading.Event())['total'], 0)
        self.assertEqual(self.app.harness.tool(task['id'], 'list_items', {'unanalysed': False}, cancel=threading.Event())['total'], 1)

    def test_forced_application_analysis_includes_existing_advice_after_collection(self):
        task = self.app.harness.create_analysis(dict(section='background', ids=[self.startup['id']]))
        self.app.harness.tool(task['id'], 'record_advice', {'items': [dict(id=self.startup['id'], conclusion='review',
            purpose='fixture', reason='fixture', impact='fixture', cleanup_rating=3)]}, cancel=threading.Event())
        forced = self.app.harness.create_analysis(dict(section='background', ids=[self.startup['id']], reanalyze=True))
        with patch('diskpilot.tasks.inventory', return_value={'items': [self.startup]}):
            result = self.app.harness.tool(forced['id'], 'collect_apps', {'section': 'background'}, cancel=threading.Event())
        self.assertEqual(result['items'][0]['id'], self.startup['id'])
        self.assertEqual(result['skipped_existing_count'], 0)

    def test_later_direct_keep_blocks_frozen_plan_at_execution_guard(self):
        prepared = self.prepare(self.a)
        self.set(self.a, 'direct_keep')
        task = self.app.harness._get(prepared['task']['id'])
        with self.assertRaises(AppError):
            self.app.harness._guard_directory(task, prepared['plan']['items'][0], respect_advice=False)

    def test_task_snapshot_save_does_not_erase_new_user_decision(self):
        task = self.app.harness.create_analysis(dict(ids=[self.a['id']]))
        staged = copy.deepcopy(self.app.harness._get(task['id']))
        self.set(self.a, 'direct_keep')
        self.app.harness._save(staged)
        self.assertEqual(self.app.get('/api/item', {'id': [self.a['id']]})['item']['user_disposition']['value'], 'direct_keep')

    def test_malformed_analysis_payload_is_client_error(self):
        with self.assertRaises(AppError):
            self.app.post('/api/tasks/analyze', [])

    def test_disposition_sql_and_cache_reads_use_shared_state_lock(self):
        store = self.app.harness.assessments.dispositions
        connection = self.app.state.db
        state_lock = self.app.state.lock

        class LockedDispositionConnection:
            def __getattr__(self, name):
                return getattr(connection, name)

            def __enter__(self):
                return connection.__enter__()

            def __exit__(self, *args):
                return connection.__exit__(*args)

            def execute(self, sql, *args):
                if 'disposition_state' in sql or 'item_dispositions' in sql:
                    self.assert_lock()
                return connection.execute(sql, *args)

            def assert_lock(self):
                if not state_lock._is_owned():
                    raise AssertionError('Disposition SQL used shared SQLite without state.lock')

        self.app.state.db = LockedDispositionConnection()
        try:
            store.revision()
            store.records()
            store.resolve([self.a])
            store.current()
            store.get(self.a)
            store.strong(self.a)
            store.direct_keep_paths([self.a])
            self.set(self.a, 'direct_keep')
        finally:
            self.app.state.db = connection

    def test_concurrent_public_polling_and_direct_plan_execution_keeps_every_receipt(self):
        # A shared SQLite connection is used by the worker and HTTP reader.
        # Public projections must not race unlocked disposition SQL/cache reads.
        for iteration in range(10):
            rows = [_item('startup', f'concurrent-owned-{iteration}-{index}', f'Owned startup {index}',
                'Owned fixture', 'startup', 'disable_startup', dict(synthetic=False), path=f'registration/{iteration}/{index}')
                for index in range(5)]
            self.app.state.publish(rows, 'concurrent-owned')
            prepared = self.prepare(*rows)
            task_id = prepared['task']['id']
            stopped = threading.Event()
            entered = threading.Event()
            errors = []

            def poll():
                entered.set()
                while not stopped.is_set():
                    try:
                        self.app.harness.public(task_id)
                        self.app.harness.assessments.dispositions.current()
                    except BaseException as exc:
                        errors.append(exc)
                        return
                    time.sleep(.0001)

            reader = threading.Thread(target=poll)
            reader.start()
            try:
                self.assertTrue(entered.wait(2))
                job = self.app.post(f'/api/tasks/{task_id}/approve',
                    dict(version=prepared['plan']['version'], ids=prepared['picked_ids']))
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    result = self.app.job(job['job_id'])
                    if result['status'] != 'running':
                        break
                    time.sleep(.001)
                self.assertEqual(result['status'], 'completed')
                final = self.app.harness.public(task_id)
                self.assertEqual(final['last_result']['succeeded'], 5, final.get('summary'))
                self.assertEqual(final['last_result']['failed'], 0)
                self.assertEqual(errors, [])
            finally:
                stopped.set()
                reader.join(2)
            self.assertFalse(reader.is_alive())


if __name__ == '__main__':
    unittest.main()
