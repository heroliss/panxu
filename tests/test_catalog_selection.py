"""Whole-task category queries and logical checkbox rules never touch real files."""
import copy
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from diskpilot.scanner import scan_directory
from diskpilot.server import Application
from diskpilot.simulation import AppError, within


class CatalogSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='panxu-selection-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.scope = self.root / 'Downloads'
        self.scope.mkdir()
        for relative in ('high.bin', 'four.bin', 'neutral.bin', 'keep.txt', 'skip.tmp', 'unrated.bin',
                         'no-advice.bin', 'sub/reselect.bin', 'sub/excluded.bin', 'whole/a.bin', 'whole/b.bin'):
            path = self.scope / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'owned-selection-fixture')
        self.other = self.root / 'Other'
        self.other.mkdir()
        (self.other / 'foreign.bin').write_bytes(b'must stay outside task')
        self.app = Application(self.root / 'profile')
        self.addCleanup(self.app.close)
        self.app.state.publish(scan_directory(str(self.scope))['items'], 'fixture')
        self.app.state.publish(scan_directory(str(self.other))['items'], 'other')
        self.refresh()
        self.task_id = self.app.harness.create_analysis({'section': 'files', 'scope': str(self.scope)})['id']

    def refresh(self):
        self.rows = {Path(row['path']).relative_to(self.scope).as_posix(): row for row in self.app.state.snapshot([])['items']
                     if within(row['path'], str(self.scope))}

    def task(self):
        return self.app.harness._get(self.task_id)

    def advice(self, relative, rating, conclusion='recommend', inheritance='none', task_id=None):
        row = self.rows[relative]
        self.app.harness.tool(task_id or self.task_id, 'record_advice', {'items': [dict(id=row['id'],
            conclusion=conclusion, cleanup_rating=rating, inheritance=inheritance, purpose='夹具用途',
            reason='仅用于选择验收', impact='只检查方案，没有执行')]}, cancel=threading.Event())

    def compile(self, positive, negative=(), **extra):
        ids = [self.rows[name]['id'] for name in positive]
        excluded = [self.rows[name]['id'] for name in negative]
        return self.app.catalog.compile_selection(ids, excluded, snapshots={row['id']: row['signature']
            for name, row in self.rows.items() if name in set(positive) | set(negative)},
            expected=self.app.catalog.token(), task=self.task(), **extra)

    def actual_files(self, result):
        targets = [next(row for row in self.rows.values() if row['id'] == ident) for ident in result['ids']]
        return {name for name, row in self.rows.items() if row['kind'] == 'file' and any(
            row['id'] == target['id'] or target['kind'] == 'directory' and within(row['path'], target['path']) for target in targets)}

    def test_automatic_is_current_task_high_only_and_does_not_swallow_specific_exceptions(self):
        self.advice('.', 5, inheritance='subtree')
        self.advice('keep.txt', 1, 'keep')
        self.advice('skip.tmp', None, 'skip')
        self.advice('unrated.bin', None, 'review')
        self.advice('no-advice.bin', None, 'no_advice')
        self.app.harness.feedback(self.task_id, keep_ids=[self.rows['four.bin']['id']])
        result = self.app.catalog.batch_selection(task=self.task(), category='high', mode='automatic')
        selected = self.actual_files(result)
        self.assertIn('high.bin', selected)
        self.assertTrue(selected.isdisjoint({'keep.txt', 'skip.tmp', 'unrated.bin', 'no-advice.bin', 'four.bin'}))
        self.assertNotIn(self.rows['.']['id'], result['ids'])
        self.assertGreater(result['counts']['blocked_reasons'].get('user_kept', 0), 0)
        self.assertEqual(len(result['groups']), 8)

    def test_other_task_score_in_same_scope_is_visible_but_never_automatic(self):
        foreign = self.app.harness.create_analysis({'section': 'files', 'ids': [self.rows['high.bin']['id']]})['id']
        self.advice('high.bin', 5, task_id=foreign)
        self.advice('.', 5, inheritance='subtree')
        page = self.app.catalog.page(task=self.task(), query='high.bin')
        self.assertEqual(page['items'][0]['assessment']['rating'], 5)
        self.assertFalse(page['items'][0]['assessment']['from_current_task'])
        result = self.app.catalog.batch_selection(task=self.task(), category='high', mode='automatic')
        self.assertNotIn('high.bin', self.actual_files(result))
        self.assertNotIn(self.rows['.']['id'], result['ids'])

    def test_manual_skip_unrated_and_low_files_can_be_selected_without_model_permission(self):
        self.advice('skip.tmp', None, 'skip')
        self.advice('keep.txt', 1, 'keep')
        self.advice('no-advice.bin', None, 'no_advice')
        self.advice('unrated.bin', None, 'review')
        for category, name in (('skipped', 'skip.tmp'), ('1', 'keep.txt'), ('no_advice', 'no-advice.bin'), ('unrated', 'unrated.bin')):
            with self.subTest(category=category):
                result = self.app.catalog.batch_selection(task=self.task(), category=category)
                self.assertIn(name, self.actual_files(result))
                self.assertTrue(any(item['category'] == category for item in result['items']))
        result = self.compile(['skip.tmp', 'keep.txt', 'no-advice.bin', 'unrated.bin'])
        self.assertEqual(self.actual_files(result), {'skip.tmp', 'keep.txt', 'no-advice.bin', 'unrated.bin'})
        self.assertFalse(self.app.state.history())

    def test_category_parent_never_selects_another_category_child(self):
        self.advice('.', 5, inheritance='subtree')
        self.advice('neutral.bin', 3, 'review')
        result = self.app.catalog.batch_selection(task=self.task(), category='5', mode='manual')
        self.assertNotIn('neutral.bin', self.actual_files(result))
        self.assertNotIn(self.rows['.']['id'], result['ids'])
        self.assertIn('high.bin', self.actual_files(result))

    def test_nearest_rule_can_reselect_one_file_inside_an_excluded_directory(self):
        result = self.compile(['.', 'sub/reselect.bin'], ['sub'])
        selected = self.actual_files(result)
        self.assertIn('sub/reselect.bin', selected)
        self.assertNotIn('sub/excluded.bin', selected)
        self.assertIn('whole/a.bin', selected)
        self.assertNotIn(self.rows['.']['id'], result['ids'])
        self.assertNotIn(self.rows['sub']['id'], result['ids'])
        self.assertIn(self.rows['whole']['id'], result['ids'])
        self.assertFalse(self.app.state.history())

    def test_manual_explicit_checkbox_overrides_temporary_user_keep(self):
        self.advice('keep.txt', 5)
        self.app.harness.feedback(self.task_id, keep_ids=[self.rows['keep.txt']['id']])
        result = self.compile(['keep.txt'])
        self.assertEqual(result['ids'], [self.rows['keep.txt']['id']])
        self.assertEqual(result['counts']['overridden_count'], 1)
        automatic = self.app.catalog.batch_selection(task=self.task(), category='high', mode='automatic')
        self.assertNotIn(self.rows['keep.txt']['id'], automatic['ids'])

    def test_true_protection_and_unsupported_items_are_reported_and_cannot_be_swallowed(self):
        self.app.assistant.save_settings({'protect_patterns': ['*keep.txt']})
        unsupported = copy.deepcopy(self.rows['four.bin'])
        unsupported['actions'] = []
        self.app.state.publish([unsupported], 'unsupported')
        result = self.compile(['.'])
        selected = self.actual_files(result)
        self.assertNotIn('keep.txt', selected)
        self.assertNotIn('four.bin', selected)
        self.assertIn('high.bin', selected)
        self.assertGreater(result['counts']['blocked_reasons']['protected'], 0)
        self.assertGreater(result['counts']['blocked_reasons']['unsupported_action'], 0)

    def test_incomplete_directory_only_expands_observed_content_and_reports_gap(self):
        partial = copy.deepcopy(self.rows['sub'])
        partial['details'].update(complete=False, subtree_complete=False, can_simulate_delete=False)
        self.app.state.publish([partial], 'partial')
        with patch('os.scandir', side_effect=AssertionError('Compiler must never scan')):
            result = self.compile(['.'])
        self.assertTrue(result['counts']['partial'])
        self.assertEqual(result['counts']['unscanned_directories'], 1)
        self.assertNotIn(self.rows['sub']['id'], result['ids'])
        self.assertIn('sub/reselect.bin', self.actual_files(result))
        self.assertTrue(result['warnings'])

    def test_task_scope_never_expands_directory_over_other_task_catalog_rows(self):
        self.task_id = self.app.harness.create_analysis({'section': 'files', 'ids': [self.rows['.']['id']]})['id']
        result = self.compile(['.'])
        self.assertEqual(result['ids'], [])
        self.assertTrue(result['counts']['partial'])
        self.assertEqual(result['counts']['scope_gap_directories'], 1)
        page = self.app.catalog.page(task=self.task())
        self.assertEqual(page['catalog_total'], 1)
        self.assertTrue(all(within(row['path'], str(self.scope)) for row in page['items']))

    def test_excluded_signatures_and_unknown_exclusions_are_not_silently_ignored(self):
        positive, negative = self.rows['.'], self.rows['keep.txt']
        for excluded, snapshot in ((negative['id'], 'stale'), ('unknown-id', 'any')):
            with self.subTest(excluded=excluded), self.assertRaises(AppError) as caught:
                self.app.catalog.compile_selection([positive['id']], [excluded], task=self.task(),
                    snapshots={positive['id']: positive['signature'], excluded: snapshot}, expected=self.app.catalog.token())
            self.assertEqual(caught.exception.code, 'selection_changed')

    def test_task_choices_invalidate_selection_revision_without_relying_on_global_catalog(self):
        original = self.app.catalog.page(task=self.task())
        self.app.harness.feedback(self.task_id, keep_ids=[self.rows['high.bin']['id']])
        self.assertEqual(self.app.catalog.token(), original['revision'])
        with self.assertRaises(AppError) as caught:
            self.app.catalog.batch_selection(task=self.task(), category='all', expected=original['revision'],
                expected_selection_revision=original['selection_revision'])
        self.assertEqual(caught.exception.code, 'selection_changed')

    def test_full_filter_query_and_compiler_batch_beyond_five_hundred(self):
        for index in range(520):
            (self.scope / f'bulk-{index:03d}.bin').write_bytes(b'owned')
        self.app.state.publish(scan_directory(str(self.scope))['items'], 'fixture')
        self.refresh()
        self.task_id = self.app.harness.create_analysis({'section': 'files', 'scope': str(self.scope), 'kind': 'file'})['id']
        first = self.app.catalog.batch_selection(task=self.task(), category='all')
        self.assertEqual(len(first['ids']), 500)
        self.assertTrue(first['requires_batches'])
        self.assertTrue(first['has_more'])
        second = self.app.catalog.batch_selection(task=self.task(), category='all', cursor=first['next_cursor'])
        self.assertFalse(set(first['ids']) & set(second['ids']))
        self.assertEqual(len(first['ids']) + len(second['ids']), first['counts']['total_targets'])
        self.assertEqual(first['counts']['total_targets'], 531)
        self.assertFalse(second['has_more'])
        selected = [row for row in self.rows.values() if row['kind'] == 'file']
        compiled = self.app.catalog.compile_selection([row['id'] for row in selected], task=self.task(),
            snapshots={row['id']: row['signature'] for row in selected}, expected=self.app.catalog.token())
        self.assertEqual(compiled['counts']['total_targets'], 531)
        self.assertEqual(len(compiled['ids']), 500)
        self.assertTrue(compiled['requires_batches'])
        with self.assertRaises(AppError):
            self.app.catalog.batch_selection(task=self.task(), category='high', cursor=first['next_cursor'])
        self.assertFalse(self.app.state.history())

    def test_direct_four_stays_in_first_auto_batch_before_hundreds_of_inherited_fives(self):
        for index in range(600):
            (self.scope / f'cache-{index:03d}.bin').write_bytes(b'owned')
        self.app.state.publish(scan_directory(str(self.scope))['items'], 'fixture')
        self.refresh()
        self.task_id = self.app.harness.create_analysis({'section': 'files', 'scope': str(self.scope)})['id']
        self.advice('.', 5, inheritance='subtree')
        self.advice('keep.txt', 1, 'keep')
        self.advice('four.bin', 4)
        first = self.app.catalog.batch_selection(task=self.task(), category='high', mode='automatic')
        self.assertEqual(first['items'][0]['id'], self.rows['four.bin']['id'])
        self.assertEqual(first['items'][0]['assessment'], {'state': 'explicit', 'rating': 4})
        self.assertTrue(first['requires_batches'])
        self.assertEqual(len(first['ids']), 500)
        self.assertIn(self.rows['four.bin']['id'], first['ids'])
        self.assertNotIn('keep.txt', self.actual_files(first))

    def filter_rows(self, *, task_id='current', **query):
        return self.app.post('/api/assessments/select', dict(task_id=self.task_id if task_id == 'current' else task_id,
            mode='manual', category=query.pop('rating', 'all'), representation='rows', **query))

    def listed_rows(self, *, task_id='current', **query):
        task_id = self.task_id if task_id == 'current' else task_id
        rows, offset = [], 0
        while True:
            params = dict(query, offset=offset, limit=3)
            if task_id is not None:
                params['task_id'] = task_id
            page = self.app.get('/api/files', {key: [str(value)] for key, value in params.items()})
            rows.extend(page['items'])
            if not page['has_more']:
                return rows
            offset += page['limit']

    def test_row_selection_reuses_visible_view_filters_across_pages_and_current_source(self):
        self.advice('.', 5, inheritance='subtree')
        self.advice('four.bin', 4)
        self.advice('skip.tmp', None, 'skip')
        other = self.app.harness.create_analysis({'section': 'files', 'ids': [self.rows['high.bin']['id']]})['id']
        self.advice('high.bin', 5, task_id=other)
        for query in (dict(view='suggestions'), dict(view='all', rating='5'),
                      dict(view='all', query='.bin', kind='file', scope=str(self.scope)),
                      dict(view='tree', parent_id=self.rows['.']['id']),
                      dict(view='tree', parent_id=self.rows['sub']['id'], query='reselect')):
            with self.subTest(query=query):
                listed = self.listed_rows(**query)
                selected = self.filter_rows(**query)
                self.assertEqual(selected['ids'], [row['id'] for row in listed if row['can_logical_select']])
                self.assertEqual(selected['counts']['total_targets'], len(selected['ids']))
                self.assertEqual(selected['representation'], 'rows')
        suggestions = self.filter_rows(view='suggestions')
        self.assertNotIn(self.rows['high.bin']['id'], suggestions['ids'])
        self.assertNotIn(self.rows['whole/a.bin']['id'], suggestions['ids'])
        self.assertFalse(self.app.state.history())

    def test_tree_row_selection_keeps_partial_directory_as_a_rule_and_compile_keeps_exclusions(self):
        partial = copy.deepcopy(self.rows['sub'])
        partial['details'].update(complete=False, subtree_complete=False, can_simulate_delete=False)
        self.app.state.publish([partial], 'partial')
        listed = self.listed_rows(view='tree', parent_id=self.rows['.']['id'])
        selected = self.filter_rows(view='tree', parent_id=self.rows['.']['id'])
        self.assertEqual(selected['ids'], [row['id'] for row in listed if row['can_logical_select']])
        self.assertIn(partial['id'], selected['ids'])
        result = self.compile(['.', 'sub/reselect.bin'], ['sub'])
        self.assertNotIn('sub/excluded.bin', self.actual_files(result))
        self.assertIn('sub/reselect.bin', self.actual_files(result))
        self.assertNotIn(self.rows['.']['id'], result['ids'])
        self.assertFalse(self.app.state.history())

    def test_row_cursors_bind_view_parent_filters_representation_and_task_revision(self):
        first = self.filter_rows(view='all', limit=1)
        self.assertTrue(first['has_more'])
        for changed in (dict(view='suggestions'), dict(view='tree', parent_id=self.rows['.']['id']),
                        dict(query='bin'), dict(kind='file'), dict(rating='5'),
                        dict(scope=str(self.scope / 'sub')), dict(representation='roots'), dict(task_id=None)):
            payload = dict(task_id=self.task_id, mode='manual', category='all', representation='rows',
                           view='all', limit=1, cursor=first['next_cursor'])
            changed = dict(changed)
            if 'rating' in changed:
                changed['category'] = changed.pop('rating')
            payload.update(changed)
            with self.subTest(changed=changed), self.assertRaises(AppError):
                self.app.post('/api/assessments/select', payload)
        self.app.harness.feedback(self.task_id, keep_ids=[self.rows['high.bin']['id']])
        with self.assertRaises(AppError) as caught:
            self.filter_rows(view='all', revision=first['revision'], selection_revision=first['selection_revision'])
        self.assertEqual(caught.exception.code, 'selection_changed')

    def test_tree_navigation_cannot_disclose_an_unrelated_known_directory(self):
        foreign = next(row for row in self.app.state.snapshot([])['items']
                       if row['kind'] == 'directory' and Path(row['path']) == self.other)
        for operation in (lambda: self.listed_rows(view='tree', parent_id=foreign['id']),
                          lambda: self.filter_rows(view='tree', parent_id=foreign['id'])):
            with self.assertRaises(AppError) as caught:
                operation()
            self.assertEqual(caught.exception.code, 'outside_scope')

    def test_filtered_tree_ancestors_are_navigation_not_selectable_filter_escape(self):
        self.advice('sub/reselect.bin', 5)
        for query in (dict(rating='5'), dict(query='reselect'), dict(kind='file')):
            with self.subTest(query=query), patch('os.scandir', side_effect=AssertionError('query must not scan')):
                page = self.listed_rows(view='tree', **query)
                self.assertTrue(page)
                self.assertTrue(all(row['navigation_only'] and not row['can_logical_select'] for row in page))
                self.assertEqual(self.filter_rows(view='tree', **query)['ids'], [])
                deeper = self.filter_rows(view='tree', parent_id=self.rows['sub']['id'], **query)
                self.assertIn(self.rows['sub/reselect.bin']['id'], deeper['ids'])
        self.assertIn(self.rows['.']['id'], self.filter_rows(view='tree')['ids'])

    def test_protected_unsupported_and_stale_rows_never_forge_complete_filter_selection(self):
        self.app.assistant.save_settings({'protect_patterns': ['*keep.txt']})
        unsupported = copy.deepcopy(self.rows['four.bin'])
        unsupported['actions'] = []
        self.app.state.publish([unsupported], 'unsupported')
        first = self.filter_rows(view='all', limit=1)
        listed = self.listed_rows(view='all')
        self.assertGreater(first['counts']['matched_rows'], first['counts']['eligible_rows'])
        self.assertGreater(first['counts']['blocked_reasons']['protected'], 0)
        self.assertGreater(first['counts']['blocked_reasons']['unsupported_action'], 0)
        all_candidates = self.filter_rows(view='all')
        self.assertEqual(all_candidates['ids'], [row['id'] for row in listed if row['can_logical_select']])
        changed = copy.deepcopy(self.rows['high.bin'])
        changed['signature'] = 'changed-owned-snapshot'
        self.app.state.publish([changed], 'changed')
        with self.assertRaises(AppError) as caught:
            self.filter_rows(view='all', cursor=first['next_cursor'], revision=first['revision'],
                             selection_revision=first['selection_revision'])
        self.assertEqual(caught.exception.code, 'selection_changed')

    def test_taskless_manual_rows_and_logical_plan_stay_files_only_and_preserve_directory_exclusions(self):
        from diskpilot.windows import _item
        process = _item('process', 'owned-global-process', 'Owned process', 'Fixture', 'Fixture', 'stop_process',
                        {'synthetic': False, 'pid': 123, 'create_time': 1}, path='fixture.exe')
        self.app.state.publish([process], 'owned-program')
        self.advice('.', 5, inheritance='subtree')
        self.advice('four.bin', 4)
        for view in ('all', 'suggestions', 'tree'):
            result = self.filter_rows(task_id=None, view=view)
            self.assertEqual(result['ids'], [row['id'] for row in self.listed_rows(task_id=None, view=view)
                                           if row['can_logical_select']])
            self.assertTrue(all(row['kind'] in ('file', 'directory') for row in result['items']))
        self.assertEqual(self.filter_rows(task_id=None, kind='process')['ids'], [])
        selected, excluded = [self.rows[name] for name in ('.', 'sub/reselect.bin')], [self.rows['sub']]
        page = self.app.catalog.page()
        payload = dict(task_id=None, selected_ids=[row['id'] for row in selected], excluded_ids=[row['id'] for row in excluded],
            snapshots={row['id']: row['signature'] for row in selected + excluded},
            revision=page['revision'], selection_revision=page['selection_revision'])
        compiled = self.app.post('/api/assessments/compile', payload)
        self.assertIn('sub/reselect.bin', self.actual_files(compiled))
        self.assertNotIn('sub/excluded.bin', self.actual_files(compiled))
        planned = self.app.post('/api/assessments/plan', payload)
        self.assertIsNone(planned['source_task_id'])
        self.assertEqual(planned['picked_ids'], compiled['ids'])
        self.assertTrue(all(row['kind'] in ('file', 'directory') for row in planned['plan']['items']))
        self.assertFalse(self.app.state.history())
        self.assertTrue((self.scope / 'sub/excluded.bin').exists())
        for task_id in ('', 'unknown-task'):
            with self.subTest(task_id=task_id), self.assertRaises(AppError):
                self.app.post('/api/assessments/compile', dict(payload, task_id=task_id))
        with self.assertRaises(AppError):
            self.app.post('/api/assessments/compile', dict(payload, selected_ids=[process['id']], excluded_ids=[],
                snapshots={process['id']: process['signature']}))
        with self.assertRaises(AppError):
            self.app.post('/api/assessments/select', dict(mode='automatic', category='high', representation='rows'))

    def test_taskless_row_cursor_and_plan_keep_honest_total_and_five_hundred_execution_batch(self):
        for index in range(520):
            (self.scope / f'cross-page-{index:03d}.bin').write_bytes(b'owned')
        self.app.state.publish(scan_directory(str(self.scope))['items'], 'fixture')
        self.refresh()
        first = self.filter_rows(task_id=None, view='all', kind='file', scope=str(self.scope))
        second = self.filter_rows(task_id=None, view='all', kind='file', scope=str(self.scope), cursor=first['next_cursor'])
        self.assertEqual(len(first['ids']), 500)
        self.assertTrue(first['has_more'])
        self.assertTrue(first['requires_batches'])
        ids = first['ids'] + second['ids']
        self.assertEqual(len(ids), first['counts']['total_targets'])
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(ids, [row['id'] for row in self.listed_rows(task_id=None, view='all', kind='file', scope=str(self.scope))])
        payload = dict(task_id=None, selected_ids=ids, excluded_ids=[], snapshots={**first['snapshots'], **second['snapshots']},
                       revision=first['revision'], selection_revision=first['selection_revision'])
        compiled = self.app.post('/api/assessments/compile', payload)
        self.assertEqual(compiled['counts']['total_targets'], len(ids))
        self.assertEqual(compiled['counts']['returned'], 500)
        planned = self.app.post('/api/assessments/plan', payload)
        self.assertEqual(len(planned['picked_ids']), 500)
        self.assertEqual(planned['selection']['counts']['total_targets'], len(ids))
        self.assertTrue(planned['selection']['has_more'])
        self.assertFalse(self.app.state.history())


if __name__ == '__main__':
    unittest.main()
