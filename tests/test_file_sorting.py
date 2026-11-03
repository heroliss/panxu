"""Whole-current-layer ordering is bounded, stable, and metadata-only."""
import copy
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from diskpilot.catalog import FileCatalog, ordered_rows
from diskpilot.file_browser import DirectoryBrowser
from diskpilot.llm import ModelAssistant
from diskpilot.scanner import metadata_item, scan_directory
from diskpilot.simulation import AppError, SimulationState
from diskpilot.tasks import TaskHarness


class FileOrderingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='panxu-sort-owned-')
        self.addCleanup(self.tmp.cleanup)
        self.scope = Path(self.tmp.name) / 'scope'
        self.scope.mkdir()
        self.state = SimulationState(Path(self.tmp.name) / 'data')
        self.addCleanup(self.state.close)
        self.harness = TaskHarness(self.state, ModelAssistant(Path(self.tmp.name) / 'data'))
        self.catalog = FileCatalog(self.state, lambda: [], self.harness.assessments)
        self.browser = DirectoryBrowser(self.state, self.catalog, max_cached_entries=101, entry_budget=128, time_budget=1)
        self.addCleanup(self.browser.close)

    def files(self, count):
        for i in range(count):
            (self.scope / f'owned-{i:04}.bin').write_bytes(b'x' * (i + 1))

    def page(self, **options):
        return self.browser.page(str(self.scope), **options)

    def sorted_pages(self, sort, direction='asc', limit=50):
        options = dict(sort=sort, direction=direction, limit=limit)
        result = self.page(**options)
        preparation = []
        previous = None
        while result['status'] == 'sorting':
            preparation.append(result)
            self.assertEqual(result['items'], [])
            self.assertIsNone(result['sort_progress']['total_entries'])
            previous = result['next_cursor']
            result = self.page(cursor=previous, **options)
        pages = [result]
        while result['has_more']:
            result = self.page(cursor=result['next_cursor'], **options)
            pages.append(result)
        return pages, preparation, previous

    def test_sort_enumerates_current_layer_in_bounded_slices_then_orders_all_pages(self):
        self.files(350)
        with patch('builtins.open', side_effect=AssertionError('No file contents may be read')):
            pages, preparation, _ = self.sorted_pages('size', 'desc')
        self.assertTrue(preparation)
        self.assertLessEqual(preparation[0]['enumerated_entries'], 128)
        values = [row['size_bytes'] for page in pages for row in page['items']]
        self.assertEqual(values, list(range(350, 0, -1)))
        self.assertEqual(len(pages), 7)
        session = next(iter(self.browser._directories.values()))
        self.assertEqual(session.rows, [])
        self.assertEqual(session.stored_count, 350)
        self.assertEqual(self.state.history(), [])

    def test_sort_never_recurses_and_unknown_folder_sizes_stay_last_in_both_directions(self):
        known = self.scope / 'known-folder'
        known.mkdir()
        (known / 'inside.txt').write_bytes(b'123456789')
        (self.scope / 'small.bin').write_bytes(b'x')
        (self.scope / 'large.bin').write_bytes(b'1234567')
        self.state.publish(scan_directory(str(self.scope))['items'], 'owned-scan')
        unknown = self.scope / 'unknown-folder'
        unknown.mkdir()
        (unknown / 'unread.txt').write_bytes(b'do not enumerate me')
        for direction, expected in (('asc', [1, 7, 9, None]), ('desc', [9, 7, 1, None])):
            pages, _, _ = self.sorted_pages('size', direction)
            rows = pages[0]['items']
            self.assertEqual([row['size_bytes'] for row in rows], expected)
            self.assertFalse(rows[-1]['size_known'])
            self.assertEqual(rows[-1]['size_kind'], 'unknown')
            observed = next(row for row in rows if row['name'] == 'known-folder')
            self.assertTrue(observed['size_known'])
        self.assertNotIn('unread.txt', {row['name'] for row in self.state.snapshot([])['items']})

    def test_preparation_and_completed_cursors_are_idempotent_and_bound_to_order(self):
        self.files(350)
        pages, _, preparation_cursor = self.sorted_pages('name', 'asc')
        first = pages[0]
        repeated = self.page(sort='name', direction='asc', cursor=preparation_cursor)
        self.assertEqual([row['id'] for row in repeated['items']], [row['id'] for row in first['items']])
        repeated = self.page(sort='name', direction='asc', cursor=first['current_cursor'])
        self.assertEqual(repeated['items'], first['items'])
        for options in (dict(sort='name', direction='desc'), dict(sort='size', direction='asc')):
            with self.assertRaises(AppError) as error:
                self.page(cursor=first['next_cursor'], **options)
            self.assertEqual(error.exception.code, 'browser_cursor_expired')
        self.assertEqual(len(self.page(sort='name', direction='asc', cursor=first['next_cursor'])['items']), 50)

    def test_sorted_cache_supports_http_threads_and_closes_private_sqlite_on_expiry(self):
        self.files(350)
        first = self.page(sort='name')
        session = next(iter(self.browser._directories.values()))
        store = session.store
        with ThreadPoolExecutor(max_workers=1) as pool:
            second = pool.submit(self.page, sort='name', cursor=first['next_cursor']).result()
        self.assertGreater(second['enumerated_entries'], first['enumerated_entries'])
        self.browser.close()
        with self.assertRaises(sqlite3.ProgrammingError):
            store.execute('SELECT 1')

    def test_catalog_sorts_complete_observed_query_before_paging_and_binds_page_revision(self):
        self.files(120)
        rows = scan_directory(str(self.scope))['items']
        for row in rows:
            row['details']['timestamps']['created_at'] = None
        for i, row in enumerate(sorted((row for row in rows if row['kind'] == 'file'), key=lambda row: row['name'])):
            if i < 118:
                row['details']['timestamps']['created_at'] = f'2026-01-01T00:{i // 60:02}:{i % 60:02}+00:00'
        self.state.publish(rows, 'owned-observations')
        one = self.catalog.page(kind='file', sort='created', direction='desc')
        two = self.catalog.page(kind='file', sort='created', direction='desc', offset=50,
                                expected_page_revision=one['page_revision'])
        three = self.catalog.page(kind='file', sort='created', direction='desc', offset=100,
                                  expected_page_revision=one['page_revision'])
        result = [row for page in (one, two, three) for row in page['items']]
        self.assertEqual([row['name'] for row in result], [f'owned-{i:04}.bin' for i in range(117, -1, -1)] + ['owned-0118.bin', 'owned-0119.bin'])
        self.assertIsNone(result[-1]['created_at'])
        with self.assertRaises(AppError):
            self.catalog.page(kind='file', sort='created', direction='asc', offset=50,
                              expected_page_revision=one['page_revision'])
        with self.assertRaises(AppError):
            self.catalog.page(kind='file', sort='created', direction='desc', query='owned-0000', offset=50,
                              expected_page_revision=one['page_revision'])
        with self.assertRaises(AppError):
            self.catalog.page(kind='file', sort='created', direction='desc', rating='unrated', offset=50,
                              expected_page_revision=one['page_revision'])
        self.assertEqual(one['selection_revision'], two['selection_revision'])

    def test_partial_directory_measurement_is_preserved_as_a_lower_bound(self):
        folder = self.scope / 'partial-folder'
        folder.mkdir()
        (folder / 'owned.txt').write_bytes(b'12345')
        rows = scan_directory(str(self.scope))['items']
        for row in rows:
            if row['path'] == str(folder):
                row['details'].update(complete=False, subtree_complete=False)
        self.state.publish(rows, 'owned-partial-scan')
        result = self.page()['items'][0]
        self.assertEqual(result['size_bytes'], 5)
        self.assertFalse(result['size_known'])
        self.assertEqual(result['size_kind'], 'observed_lower_bound')
        self.assertFalse(result['can_prepare'])

    def test_new_scores_invalidate_a_frozen_rating_order_without_invalidating_plain_name_order(self):
        self.files(130)
        rows = scan_directory(str(self.scope))['items']
        self.state.publish(rows, 'owned-scan')
        target = next(row for row in rows if row['kind'] == 'file')
        task = self.harness.create_analysis({'section': 'files', 'ids': [target['id']]})
        first = self.page(sort='rating')
        self.assertEqual(first['status'], 'sorting')
        self.harness.tool(task['id'], 'record_advice', {'items': [dict(id=target['id'], conclusion='keep',
            cleanup_rating=1, purpose='Owned fixture', reason='Keep this artificial sample', impact='No action')]},
            cancel=threading.Event())
        with self.assertRaises(AppError) as error:
            self.page(sort='rating', cursor=first['next_cursor'])
        self.assertEqual(error.exception.code, 'browser_cursor_expired')
        pages, _, _ = self.sorted_pages('rating')
        self.assertEqual(pages[0]['items'][0]['id'], target['id'])

    def test_rating_sort_places_unknown_or_stale_after_known_scores_in_both_directions(self):
        rows = [dict(id='z', name='z', assessment={'rating': None}),
                dict(id='a', name='a', assessment={'rating': 1}),
                dict(id='b', name='b', assessment={'rating': 5}),
                dict(id='c', name='c', assessment={'rating': 5, 'stale': True})]
        self.assertEqual([row['id'] for row in ordered_rows(copy.deepcopy(rows), 'rating', 'asc')], ['a', 'b', 'c', 'z'])
        self.assertEqual([row['id'] for row in ordered_rows(copy.deepcopy(rows), 'rating', 'desc')], ['b', 'a', 'c', 'z'])

    def test_metadata_creation_is_birthtime_only_and_file_modification_is_not_analysis_time(self):
        path = self.scope / 'owned.txt'
        path.write_text('fixture', encoding='utf-8')
        actual = os.lstat(path)
        values = {name: getattr(actual, name) for name in dir(actual) if name.startswith('st_')}
        values.pop('st_birthtime_ns', None)
        old = metadata_item(str(path), SimpleNamespace(**values), root=str(self.scope), depth=1,
                            parent_id=None, project=None, is_dir=False)
        self.assertIsNone(old['details']['timestamps']['created_at'])
        values['st_birthtime_ns'] = 1_000_000_000
        values['st_mtime_ns'] = 2_000_000_000
        item = metadata_item(str(path), SimpleNamespace(**values), root=str(self.scope), depth=1,
                             parent_id=None, project=None, is_dir=False)
        self.assertEqual(item['details']['timestamps']['created_at'], '1970-01-01T00:00:01.000000+00:00')
        self.assertEqual(item['details']['timestamps']['modified_at'], '1970-01-01T00:00:02.000000+00:00')
        self.assertNotEqual(item['details']['timestamps']['observed_at'], item['details']['timestamps']['modified_at'])

    def test_invalid_sort_and_direction_are_rejected_without_enumeration(self):
        with patch('diskpilot.file_browser.os.scandir', side_effect=AssertionError('No IO for invalid sort')):
            for options in (dict(sort='unknown'), dict(direction='backwards'), dict(sort=True)):
                with self.subTest(options=options), self.assertRaises(AppError):
                    self.page(**options)

    def test_sorting_the_cache_parent_does_not_invalidate_itself_or_expose_owned_cache(self):
        # Put our private temporary cache inside the named disposable scope to
        # reproduce browsing Temp without inspecting the machine's real Temp.
        self.files(130)
        actual = tempfile.TemporaryDirectory
        with patch('diskpilot.file_browser.tempfile.TemporaryDirectory',
                   side_effect=lambda **options: actual(dir=self.scope, **options)):
            pages, preparation, _ = self.sorted_pages('name')
        self.assertTrue(preparation)
        rows = [row for page in pages for row in page['items']]
        owned = next(row for row in rows if row['name'].startswith('panxu-browser-sort-'))
        self.assertFalse(owned['browseable'])
        self.assertFalse(owned['can_logical_select'])
        cache = Path(self.browser._spool_cache.name)
        with self.assertRaises(AppError) as error:
            self.browser.page(str(cache))
        self.assertEqual(error.exception.code, 'owned_browser_cache')
        self.browser.close()
        self.assertFalse(cache.exists())


if __name__ == '__main__':
    unittest.main()
