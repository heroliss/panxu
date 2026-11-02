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


if __name__ == '__main__':
    unittest.main()
