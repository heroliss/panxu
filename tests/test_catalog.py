import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from diskpilot.catalog import FileCatalog
from diskpilot.presets import resolve_preset
from diskpilot.scanner import scan_directory
from diskpilot.server import Application
from diskpilot.simulation import AppError, logical_estimate, top_level_items


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.scope = self.root / 'Downloads'
        self.scope.mkdir()
        for index in range(123):
            (self.scope / f'download-{index:03d}.bin').write_bytes(bytes([index]) * (index + 1))
        self.app = Application(self.root / 'store')
        self.addCleanup(self.app.close)
        self.publish()

    def publish(self):
        self.app.state.publish(scan_directory(str(self.scope))['items'], 'scope')

    def test_page_is_bounded_sorted_and_disjoint(self):
        first = self.app.get('/api/files', {})
        second = self.app.get('/api/files', {'offset': ['50'], 'revision': [first['revision']]})
        self.assertEqual(len(first['items']), 50)
        self.assertEqual(first['total'], 124)
        self.assertTrue(first['has_more'])
        self.assertFalse({i['id'] for i in first['items']} & {i['id'] for i in second['items']})
        self.assertGreaterEqual(first['items'][-1]['size_bytes'], second['items'][0]['size_bytes'])
        self.assertNotIn('evidence', first['items'][0])
        self.assertLess(len(json.dumps(first)), len(json.dumps(self.app.view())))

    def test_compact_boot_keeps_summary_and_omits_catalog(self):
        with patch('diskpilot.server.disks', return_value=[]):
            compact = self.app.get('/api/bootstrap', {'compact': ['1']})
        self.assertNotIn('items', compact)
        self.assertEqual(compact['summary']['visible_file_items'], 124)
        self.assertEqual(compact['catalog_revision'], self.app.catalog.token())

    def test_search_scope_and_last_page(self):
        result = self.app.catalog.page(query='download-122', scope=str(self.scope))
        self.assertEqual([i['name'] for i in result['items']], ['download-122.bin'])
        self.assertEqual(self.app.catalog.page(scope=str(self.root / 'elsewhere'))['total'], 0)
        self.assertEqual(len(self.app.catalog.page(offset=100)['items']), 24)
        self.assertFalse(self.app.catalog.page(offset=100)['has_more'])

    def test_rescan_and_policy_change_invalidate_page_tokens(self):
        token = self.app.catalog.page()['revision']
        self.publish()
        with self.assertRaises(AppError) as caught:
            self.app.catalog.page(expected=token)
        self.assertEqual(caught.exception.code, 'catalog_changed')
        old = self.app.catalog.page()['revision']
        catalog = FileCatalog(self.app.state, lambda: ['*download-122*'])
        self.assertNotEqual(catalog.token(), old)
        protected = catalog.page(query='download-122')['items'][0]
        self.assertFalse(protected['can_select'])

    def test_off_page_protected_descendant_blocks_parent(self):
        catalog = FileCatalog(self.app.state, lambda: ['*download-000*'])
        parent = catalog.page(limit=1)['items'][0]
        self.assertEqual(parent['kind'], 'directory')
        self.assertFalse(parent['can_select'])

    def test_stale_cross_page_selection_rejected_before_plan(self):
        item = self.app.catalog.page(query='download-122')['items'][0]
        file = self.scope / item['name']
        file.write_bytes(b'new snapshot')
        self.publish()
        with self.assertRaises(AppError) as caught:
            self.app.post('/api/plan', {'ids': [item['id']], 'snapshots': {item['id']: item['signature']}})
        self.assertEqual(caught.exception.code, 'selection_changed')
        self.assertEqual(file.read_bytes(), b'new snapshot')

    def test_current_selection_still_prepares_and_requires_explicit_choice(self):
        item = self.app.catalog.page(query='download-122')['items'][0]
        plan = self.app.post('/api/plan', {'ids': [item['id']], 'snapshots': {item['id']: item['signature']}})
        self.assertEqual(len(plan['items']), 1)
        self.assertEqual((self.scope / item['name']).stat().st_size, 123)

    def test_invalid_page_arguments_are_user_errors(self):
        for options in ({'limit': 0}, {'limit': 101}, {'offset': -1}, {'query': 'x' * 201}, {'risk': 'garbage'}):
            with self.subTest(options=options), self.assertRaises(AppError):
                self.app.catalog.page(**options)
        with self.assertRaises(AppError):
            self.app.get('/api/files', {'offset': ['not a number']})

    def test_snapshot_cache_does_not_leak_mutations_and_tracks_overlay(self):
        first = self.app.state.view()
        first['items'].clear()
        self.assertEqual(len(self.app.state.view()['items']), 124)
        with patch.object(self.app.state, '_build_view', wraps=self.app.state._build_view) as build:
            self.app.state.overview()
            self.app.catalog.page()
            self.assertEqual(build.call_count, 0)
            item = self.app.catalog.page(query='download-122')['items'][0]
            batch = self.app.state.execute(self.app.state.prepare([item['id']])['id'])
            self.assertEqual(self.app.catalog.page(query='download-122')['total'], 0)
            self.assertEqual(build.call_count, 1)
            self.app.state.undo(batch['batch']['id'])
            self.assertEqual(self.app.catalog.page(query='download-122')['total'], 1)

    def test_presets_capture_intention_scope_and_priority_locations(self):
        local = self.scope / 'Local'
        cache = local / 'D3DSCache'
        cache.mkdir(parents=True)
        with patch.dict(os.environ, {'LOCALAPPDATA': str(local), 'TEMP': str(self.root / 'outside')}), \
             patch('diskpilot.presets.system_root', return_value=str(self.scope)):
            spec = resolve_preset('c_cleanup')
            self.assertEqual(spec['paths'][0][0], str(self.scope))
            self.assertIn(str(cache), spec['priority'])
            task = self.app.harness.create(preset='c_cleanup')
        internal = self.app.harness._get(task['id'])
        context = self.app.harness._context(internal)
        self.assertEqual(task['preset'], 'c_cleanup')
        self.assertEqual(context['priority_scopes'], ['priority-1'])
        self.assertNotIn(str(self.scope), json.dumps(context))

    def test_background_preset_has_no_filesystem_scope(self):
        task = self.app.harness.create(preset='background')
        self.assertEqual(task['scopes'], [])
        self.assertEqual(task['scope_kind'], 'applications')
        for preset in ('unknown', ['invalid']):
            with self.assertRaises(AppError):
                resolve_preset(preset)


class HierarchyTests(unittest.TestCase):
    def test_nested_windows_paths_and_same_names_do_not_double_count(self):
        def item(path, kind, size):
            return dict(path=path, kind=kind, size_bytes=size, details={})
        rows = [item('C:\\cache', 'directory', 10), item('C:\\cache\\a', 'file', 5),
                item('C:\\cache\\sub', 'directory', 5), item('C:\\cache-other\\b', 'file', 7),
                item('D:\\cache\\a', 'file', 8), item('c:\\CACHE', 'directory', 10)]
        self.assertEqual(len(top_level_items(rows)), 3)
        self.assertEqual(logical_estimate(rows), 25)

    def test_root_directory_covers_children(self):
        rows = [dict(path='C:\\', kind='directory', size_bytes=20, details={}),
                dict(path='C:\\abc\\file', kind='file', size_bytes=20, details={})]
        self.assertEqual(logical_estimate(rows), 20)


if __name__ == '__main__':
    unittest.main()
