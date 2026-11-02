"""Lazy browsing uses disposable files and never reads their contents."""
import copy
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from diskpilot.catalog import FileCatalog
from diskpilot.file_browser import DirectoryBrowser
from diskpilot.llm import ModelAssistant
from diskpilot.scanner import scan_directory
from diskpilot.simulation import AppError, SimulationState
from diskpilot.tasks import TaskHarness


class FileBrowserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='panxu-browser-owned-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.scope = self.root / 'scope'
        self.scope.mkdir()
        self.state = SimulationState(self.root / 'data')
        self.addCleanup(self.state.close)
        self.harness = TaskHarness(self.state, ModelAssistant(self.root / 'data'))
        self.catalog = FileCatalog(self.state, lambda: [], self.harness.assessments)
        self.browser = DirectoryBrowser(self.state, self.catalog, time_budget=1)
        self.addCleanup(self.browser.close)

    def files(self, count):
        for index in range(count):
            (self.scope / f'{index:04}.bin').write_bytes(b'disposable fixture')

    def page(self, **kwargs):
        return self.browser.page(str(self.scope), **kwargs)

    def test_large_directory_first_page_is_bounded_and_does_not_read_contents(self):
        self.files(1000)
        actual = os.scandir
        visited = []
        class Counted:
            def __init__(self, path): self.entries = actual(path)
            def __next__(self):
                entry = next(self.entries); visited.append(entry.name); return entry
            def close(self): self.entries.close()
        with patch('diskpilot.file_browser.os.scandir', Counted), patch('builtins.open', side_effect=AssertionError('no file body')):
            page = self.page()
        self.assertEqual(len(page['items']), 50)
        self.assertLessEqual(len(visited), 51)
        self.assertTrue(page['has_more'])
        self.assertFalse(page['enumeration_complete'])
        self.assertIsNone(page['coverage']['known_total'])
        self.assertEqual(page['coverage']['kind'], 'direct_children_only')
        self.assertFalse(page['coverage']['subtree_complete'])
        self.assertEqual(self.state.history(), [])

    def test_pages_are_disjoint_and_current_layer_completion_is_not_subtree_completion(self):
        self.files(123)
        child = self.scope / 'child'
        child.mkdir()
        (child / 'nested.txt').write_text('must not enumerate', encoding='utf-8')
        first = self.page()
        second = self.page(cursor=first['next_cursor'])
        third = self.page(cursor=second['next_cursor'])
        ids = [item['id'] for p in (first, second, third) for item in p['items']]
        self.assertEqual(len(ids), 124)
        self.assertEqual(len(set(ids)), 124)
        self.assertTrue(third['enumeration_complete'])
        self.assertFalse(third['has_more'])
        self.assertFalse(third['coverage']['subtree_complete'])
        self.assertNotIn('nested.txt', {item['name'] for item in self.state.snapshot([])['items']})

    def test_metadata_ids_and_file_signatures_match_the_scanner(self):
        self.files(3)
        scanned = {item['name']: item for item in scan_directory(str(self.scope))['items']}
        browsed = self.page()['items']
        for row in browsed:
            self.assertEqual(row['id'], scanned[row['name']]['id'])
            self.assertEqual(row['signature'], scanned[row['name']]['signature'])
            self.assertIn('modified_at', row)
            self.assertIn('created_at', row)

    def test_directory_size_is_unknown_and_not_authorized_for_whole_delete(self):
        child = self.scope / 'folder'
        child.mkdir()
        (child / 'inside.bin').write_bytes(b'owned')
        row = self.page()['items'][0]
        self.assertEqual(row['kind'], 'directory')
        self.assertIsNone(row['size_bytes'])
        self.assertFalse(row['size_known'])
        self.assertTrue(row['browseable'])
        self.assertFalse(row['can_prepare'])
        self.assertFalse(row['can_select'])
        self.assertTrue(row['can_logical_select'])
        self.assertIsNone(row['analysis_summary']['subtree_total'])
        with self.assertRaises(AppError): self.state.prepare([row['id']], action='delete')

    def test_browsing_preserves_a_compatible_complete_scan_observation(self):
        child = self.scope / 'folder'
        child.mkdir()
        (child / 'inside.bin').write_bytes(b'owned')
        scanned = scan_directory(str(self.scope))['items']
        self.state.publish(scanned, 'scan-fixture')
        before = {item['id']: copy.deepcopy(item) for item in self.state.snapshot([])['items']}
        row = self.page()['items'][0]
        stored = next(item for item in self.state.snapshot([])['items'] if item['id'] == row['id'])
        self.assertEqual(stored['signature'], before[row['id']]['signature'])
        self.assertTrue(stored['details']['subtree_complete'])
        self.assertEqual(row['analysis_summary']['known_children'], 1)

    def test_directory_change_invalidates_cursor_and_refresh_starts_new_layer(self):
        self.files(123)
        first = self.page()
        (self.scope / 'new.bin').write_bytes(b'owned new file')
        with self.assertRaises(AppError) as error: self.page(cursor=first['next_cursor'])
        self.assertEqual(error.exception.code, 'browser_cursor_expired')
        fresh = self.page(refresh=True)
        self.assertNotEqual(fresh['next_cursor'], first['next_cursor'])

    def test_refresh_does_not_reuse_an_old_cursor(self):
        self.files(123)
        first = self.page()
        self.page(refresh=True)
        with self.assertRaises(AppError): self.page(cursor=first['next_cursor'])

    def test_expired_and_evicted_iterators_are_closed(self):
        self.files(123)
        browser = DirectoryBrowser(self.state, self.catalog, max_cached_directories=1, ttl_seconds=0)
        self.addCleanup(browser.close)
        first = browser.page(str(self.scope))
        with self.assertRaises(AppError): browser.page(str(self.scope), cursor=first['next_cursor'])
        browser.page(str(self.scope))
        self.assertEqual(len(browser._directories), 1)
        other = self.root / 'other'
        other.mkdir()
        browser.page(str(other))
        self.assertEqual(len(browser._directories), 1)
        self.assertTrue(next(iter(browser._directories.values())).complete)

    def test_rolling_cache_can_continue_past_its_memory_limit(self):
        self.files(350)
        browser = DirectoryBrowser(self.state, self.catalog, max_cached_entries=101, time_budget=1)
        self.addCleanup(browser.close)
        page = browser.page(str(self.scope), limit=100)
        old_cursor = page['next_cursor']
        ids = [item['id'] for item in page['items']]
        while page['has_more']:
            page = browser.page(str(self.scope), cursor=page['next_cursor'], limit=100)
            ids.extend(item['id'] for item in page['items'])
        self.assertEqual(len(set(ids)), 350)
        self.assertLessEqual(len(next(iter(browser._directories.values())).rows), 101)
        with self.assertRaises(AppError): browser.page(str(self.scope), cursor=old_cursor, limit=100)

    def test_credentials_and_user_protection_are_not_actionable(self):
        private = self.scope / '.ssh'
        private.mkdir()
        (private / 'key').write_text('owned secret fixture', encoding='utf-8')
        row = self.page()['items'][0]
        self.assertFalse(row['browseable'])
        self.assertFalse(row['can_analyze'])
        self.assertFalse(row['can_logical_select'])
        with self.assertRaises(AppError) as error: self.browser.page(str(private))
        self.assertEqual(error.exception.code, 'protected_browser_path')

    def test_links_are_visible_metadata_but_never_followed(self):
        target = self.root / 'target'
        target.mkdir()
        (target / 'private.txt').write_text('do not follow', encoding='utf-8')
        link = self.scope / 'link'
        try: link.symlink_to(target, target_is_directory=True)
        except OSError: self.skipTest('Creating disposable symlinks is unavailable')
        row = self.page()['items'][0]
        self.assertFalse(row['browseable'])
        self.assertFalse(row['can_logical_select'])
        with self.assertRaises(AppError): self.browser.page(str(link))
        self.assertNotIn('private.txt', {item['name'] for item in self.state.snapshot([])['items']})

    def test_network_and_relative_paths_are_rejected_before_io(self):
        for path in ('relative', '\\\\server\\share', '//server/share', 'C:relative'):
            with self.subTest(path=path), patch('diskpilot.file_browser.os.lstat', side_effect=AssertionError('no network IO')):
                with self.assertRaises(AppError): self.browser.page(path)

    def test_access_denied_is_a_distinct_failure(self):
        with patch('diskpilot.file_browser.os.scandir', side_effect=PermissionError('fixture denied')):
            with self.assertRaises(AppError) as error: self.page()
        self.assertEqual(error.exception.code, 'directory_access_denied')
        self.assertEqual(error.exception.status, 403)

    def test_user_protected_file_has_no_browse_action(self):
        (self.scope / 'keep.bin').write_bytes(b'owned protected fixture')
        catalog = FileCatalog(self.state, lambda: ['keep.bin'], self.harness.assessments)
        browser = DirectoryBrowser(self.state, catalog, time_budget=1)
        self.addCleanup(browser.close)
        row = browser.page(str(self.scope))['items'][0]
        self.assertEqual(row['risk'], 'protected')
        self.assertFalse(row['can_logical_select'])
        self.assertFalse(row['can_analyze'])

    def test_cloud_placeholder_metadata_is_not_downloaded_or_actionable(self):
        path = self.scope / 'cloud.bin'
        path.write_bytes(b'owned placeholder fixture')
        actual = os.lstat
        def cloud(value, *args, **kwargs):
            info = actual(value, *args, **kwargs)
            if Path(value) == path:
                attrs = {name: getattr(info, name) for name in dir(info) if name.startswith('st_')}
                attrs['st_file_attributes'] = attrs.get('st_file_attributes', 0) | 0x1000
                return SimpleNamespace(**attrs)
            return info
        with patch('diskpilot.file_browser.os.lstat', cloud), patch('builtins.open', side_effect=AssertionError('no content read')):
            row = self.page()['items'][0]
        self.assertFalse(row['can_logical_select'])
        self.assertFalse(row['can_analyze'])
        self.assertIn('云端', row['browse_block_reason'])

    def test_cloud_parent_is_rejected_before_looking_up_child_metadata(self):
        parent = self.scope / 'offline'
        parent.mkdir()
        child = parent / 'nested'
        child.mkdir()
        actual = os.lstat
        visited = []
        def cloud(value, *args, **kwargs):
            visited.append(Path(value))
            self.assertNotEqual(Path(value), child)
            info = actual(value, *args, **kwargs)
            if Path(value) == parent:
                attrs = {name: getattr(info, name) for name in dir(info) if name.startswith('st_')}
                attrs['st_file_attributes'] = attrs.get('st_file_attributes', 0) | 0x1000
                return SimpleNamespace(**attrs)
            return info
        with patch('diskpilot.file_browser.os.lstat', cloud):
            with self.assertRaises(AppError) as error: self.browser.page(str(child))
        self.assertEqual(error.exception.code, 'cloud_placeholder')
        self.assertIn(parent, visited)

    def test_existing_inherited_children_keep_their_old_scan_coverage(self):
        self.files(2)
        self.state.publish(scan_directory(str(self.scope))['items'], 'scan-fixture')
        task = self.harness.create_analysis({'section': 'files', 'scope': str(self.scope)})
        parent = next(item for item in self.state.snapshot([])['items'] if item['path'] == str(self.scope))
        self.harness.tool(task['id'], 'record_advice', {'items': [dict(id=parent['id'], conclusion='recommend',
            cleanup_rating=5, inheritance='subtree', purpose='Owned directory', reason='fixture', impact='fixture')]}, cancel=threading.Event())
        for row in self.page()['items']:
            self.assertEqual(row['assessment']['state'], 'inherited')
            self.assertEqual(row['assessment']['rating'], 5)

    def test_old_directory_advice_does_not_score_a_new_browse_only_child(self):
        self.files(2)
        self.state.publish(scan_directory(str(self.scope))['items'], 'scan-fixture')
        task = self.harness.create_analysis({'section': 'files', 'scope': str(self.scope)})
        parent = next(item for item in self.state.snapshot([])['items'] if item['path'] == str(self.scope))
        self.harness.tool(task['id'], 'record_advice', {'items': [dict(id=parent['id'], conclusion='recommend',
            cleanup_rating=5, inheritance='subtree', purpose='Owned directory', reason='fixture', impact='fixture')]}, cancel=threading.Event())
        # Publish a new metadata-only child without changing the stored richer
        # parent. This represents a nested child found by lazy browsing.
        nested = self.scope / 'fresh'
        nested.mkdir()
        file = nested / 'new.bin'
        file.write_bytes(b'owned new fixture')
        browser = DirectoryBrowser(self.state, self.catalog, time_budget=1)
        self.addCleanup(browser.close)
        row = browser.page(str(nested))['items'][0]
        self.assertEqual(row['assessment']['state'], 'unanalysed')
        self.assertIsNone(row['assessment']['rating'])
        self.assertTrue(row['assessment']['outside_analysis_coverage'])
        ident = row['id']
        global_row = next(item for item in self.catalog.page(limit=100)['items'] if item['id'] == ident)
        self.assertEqual(global_row['assessment']['state'], 'unanalysed')
        self.assertTrue(global_row['assessment']['outside_analysis_coverage'])

    def test_browsing_does_not_expand_task_identity_scope(self):
        selected = self.scope / 'chosen.bin'
        other = self.scope / 'other.bin'
        selected.write_bytes(b'owned selected')
        other.write_bytes(b'owned other')
        self.state.publish(scan_directory(str(self.scope))['items'], 'scan-fixture')
        selected_item = next(i for i in self.state.snapshot([])['items'] if i['path'] == str(selected))
        task = self.harness.create_analysis({'section': 'files', 'ids': [selected_item['id']]})
        before = copy.deepcopy(self.harness._get(task['id']))
        rows = {row['name']: row for row in self.page(task=before)['items']}
        self.assertTrue(rows['chosen.bin']['in_task_scope'])
        self.assertFalse(rows['other.bin']['in_task_scope'])
        self.assertFalse(rows['other.bin']['can_logical_select'])
        self.assertTrue(rows['other.bin']['analysis_requires_new_task'])
        self.assertEqual(before['known_ids'], self.harness._get(task['id'])['known_ids'])

    def test_drive_list_is_an_entry_list_not_a_scan(self):
        browser = DirectoryBrowser(self.state, self.catalog, drive_provider=lambda: [
            dict(path=str(self.scope), name='Owned', total_bytes=100, free_bytes=60, used_bytes=40)])
        self.addCleanup(browser.close)
        with patch('diskpilot.file_browser.os.scandir', side_effect=AssertionError('no scan')):
            page = browser.page()
        self.assertEqual(page['items'][0]['kind'], 'drive')
        self.assertEqual(page['items'][0]['free_bytes'], 60)
        self.assertEqual(page['coverage']['kind'], 'drive_list')


if __name__ == '__main__':
    unittest.main()
