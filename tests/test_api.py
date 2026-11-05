import os
import re
import time
import unittest

from helpers import TempDirTest

from panxu import paths, ui_bundle
from panxu.api import Backend, Bridge


def wait(bridge, job_id, timeout=20):
    end = time.time() + timeout
    while time.time() < end:
        s = bridge.call('job', {'job_id': job_id})['data']
        if s['status'] != 'running':
            return s
        time.sleep(0.05)
    raise AssertionError('job did not finish')


class BridgeTest(TempDirTest):
    def setUp(self):
        super().setUp()
        # bases inside the temp dir: on Windows the temp dir itself lies under the real %LOCALAPPDATA%
        self.bridge = Bridge(Backend(os.path.join(self.tmp, 'data'), env=self.posix_env()))
        self.backend = self.bridge._backend

    def test_dispatch_and_errors(self):
        self.assertTrue(self.bridge.call('init')['ok'])
        self.assertFalse(self.bridge.call('__init__')['ok'])
        self.assertFalse(self.bridge.call('nope')['ok'])
        r = self.bridge.call('space_node', {'node_id': 0})
        self.assertFalse(r['ok'])
        self.assertIn('扫描', r['error'])
        self.assertFalse(self.bridge.call('init', {'unexpected': 1})['ok'])

    def test_scan_browse_and_delete_permanently(self):
        self.make('area/old/a.bin', 4000)
        self.make('area/keep/b.bin', 1000)
        area = os.path.join(self.tmp, 'area')
        s = wait(self.bridge, self.bridge.call('space_scan', {'root': area})['data'])
        self.assertEqual(s['status'], 'done')
        self.assertEqual(s['result']['stats']['bytes'], 5000)
        node = self.bridge.call('space_node', {'node_id': 0})['data']
        self.assertEqual([c['name'] for c in node['children']], ['old', 'keep'])
        target = node['children'][0]['path']
        checks = self.bridge.call('check_paths', {'items': [target]})['data']
        self.assertEqual(checks[0]['size'], 4000)
        job = self.bridge.call('delete_paths', {'items': [target], 'mode': 'permanent'})['data']
        res = wait(self.bridge, job)
        self.assertEqual(res['result']['freed'], 4000)
        self.assertFalse(os.path.exists(target))
        self.assertEqual(self.backend.scan.root.size, 1000)
        hist = self.bridge.call('history')['data']
        self.assertEqual(hist[0]['kind'], 'delete')

    def test_delete_refuses_protected_paths(self):
        r = self.bridge.call('delete_paths', {'items': ['/'], 'mode': 'permanent'})
        self.assertFalse(r['ok'])
        r = self.bridge.call('delete_paths', {'items': [], 'mode': 'recycle'})
        self.assertFalse(r['ok'])
        r = self.bridge.call('delete_paths', {'items': ['/x'], 'mode': 'shred'})
        self.assertFalse(r['ok'])

    def test_duplicates_skip_folders_programs_manage(self):
        for rel in ('area/proj/node_modules/a/lib.js', 'area/proj/node_modules/b/lib.js', 'area/app/Cache/x.bin',
                    'area/app/Cache/y.bin', 'area/mine/one.bin', 'area/mine/two.bin'):
            self.make(rel, 5000)
        s = wait(self.bridge, self.bridge.call('dup_scan', {'roots': [os.path.join(self.tmp, 'area')], 'min_size_mb': 0.001})['data'])
        found = [os.path.basename(f['path']) for g in s['result']['groups'] for f in g['files']]
        self.assertEqual(sorted(found), ['one.bin', 'two.bin'])
        # a folder the user picks explicitly is searched even if its name looks managed
        s = wait(self.bridge, self.bridge.call('dup_scan', {'roots': [os.path.join(self.tmp, 'area', 'app', 'Cache')],
                                                            'min_size_mb': 0.001})['data'])
        self.assertEqual(len(s['result']['groups']), 1)

    def test_reveal_opens_the_nearest_folder_that_is_still_there(self):
        from unittest import mock
        from panxu import winapi
        f = self.make('area/sub/file.txt', 10)
        with mock.patch.object(winapi, 'reveal_in_explorer') as shown:
            r = self.bridge.call('reveal', {'path': f})['data']
            self.assertEqual((r['shown'], r['missing']), (f, False))
            gone = os.path.join(self.tmp, 'area', 'sub', 'cleaned', 'deep', 'x.tmp')  # cleaned, folders and all
            r = self.bridge.call('reveal', {'path': gone})['data']
            self.assertEqual((r['shown'], r['missing']), (os.path.join(self.tmp, 'area', 'sub'), True))
            self.assertEqual([c.args[0] for c in shown.call_args_list], [f, os.path.join(self.tmp, 'area', 'sub')])
            self.assertFalse(self.bridge.call('reveal', {'path': 'relative\\x'})['ok'])
            self.assertFalse(self.bridge.call('reveal', {'path': 7})['ok'])

    def test_open_documents_but_never_run_programs(self):
        from unittest import mock
        from panxu import winapi
        doc = self.make('area/report.pdf', 10)
        folder = os.path.dirname(doc)
        with mock.patch.object(winapi, 'open_file') as opened, mock.patch.object(winapi, 'reveal_in_explorer') as shown:
            self.assertEqual(self.bridge.call('open_path', {'path': doc})['data'], {'opened': doc})
            self.bridge.call('open_path', {'path': folder})
            for name in ('setup.exe', 'run.BAT', 'x.ps1', 'link.lnk', 'pkg.msi', 'a.vbs'):
                r = self.bridge.call('open_path', {'path': self.make('area/' + name, 10)})
                self.assertFalse(r['ok'], name)
                self.assertIn('运行', r['error'])
            self.assertFalse(self.bridge.call('open_path', {'path': os.path.join(folder, 'gone.pdf')})['ok'])
        opened.assert_called_once_with(doc)
        shown.assert_called_once_with(folder)

    def test_flash_window_asks_for_both_titles(self):
        from unittest import mock
        from panxu import winapi
        with mock.patch.object(winapi, 'flash_window', return_value=True) as flash:
            self.assertTrue(self.bridge.call('flash_window')['data'])
        self.assertEqual(flash.call_args[0][0], ('盘序', '盘序（演练环境）'))

    def test_type_icons_only_for_types(self):
        r = self.bridge.call('type_icons', {'kinds': ['..\\x', 'C:\\a.pdf', '.pdf' + 'x' * 30]})
        self.assertTrue(r['ok'])
        self.assertEqual(r['data'], {}, 'only "folder" and plain extensions are asked for')

    def test_ai_requires_key(self):
        self.make('x/sub/y', 10)
        wait(self.bridge, self.bridge.call('space_scan', {'root': os.path.join(self.tmp, 'x')})['data'])
        self.backend.settings.update({'ai': {'min_dir_mb': 0}})
        r = self.bridge.call('ai_analyze')
        self.assertFalse(r['ok'])
        self.assertIn('API Key', r['error'])

    def test_exclusions_block_deletion(self):
        p = self.make('mine/file.bin', 10)
        self.bridge.call('settings_save', {'changes': {'excluded_paths': [os.path.join(self.tmp, 'mine')]}})
        policy = self.backend.policy()
        if os.sep == '\\':
            self.assertIsNotNone(policy.reason(p))
        else:  # posix test paths are not absolute Windows paths; check the matcher directly
            self.assertTrue(paths.is_within(p, os.path.join(self.tmp, 'mine')))


class BundleTest(unittest.TestCase):
    def test_everything_inlined(self):
        html = ui_bundle.build()
        self.assertNotRegex(html, r'<script\s+src=')
        self.assertNotRegex(html, r'<link\s+rel="stylesheet"')
        self.assertNotIn('pywebview = { api', html, 'mock must not ship in the app bundle')
        for name in ('PX.pages.junk', 'PX.pages.space', 'PX.deleteFlow', 'PX.treemap', 'PX.pages.home', 'PX.pages.mine',
                     'PX.pages.apps', 'PX.ask'):
            self.assertIn(name, html)
        self.assertEqual(len(re.findall(r'<script>', html)), 13)


if __name__ == '__main__':
    unittest.main()
