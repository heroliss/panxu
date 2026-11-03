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
        self.bridge = Bridge(Backend(os.path.join(self.tmp, 'data')))
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

    def test_duplicates_must_keep_one(self):
        for name in ('a', 'b'):
            with open(os.path.join(self.tmp, name), 'wb') as fh:
                fh.write(b'z' * 5000)
        s = wait(self.bridge, self.bridge.call('dup_scan', {'roots': [self.tmp], 'min_size_mb': 0.001})['data'])
        group = s['result']['groups'][0]
        both = [f['path'] for f in group['files']]
        r = self.bridge.call('dup_delete', {'paths_to_delete': both})
        self.assertFalse(r['ok'])
        self.assertIn('保留', r['error'])

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
        for name in ('PX.pages.junk', 'PX.pages.space', 'PX.deleteFlow', 'PX.treemap'):
            self.assertIn(name, html)
        self.assertEqual(len(re.findall(r'<script>', html)), 10)


if __name__ == '__main__':
    unittest.main()
