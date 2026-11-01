import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from diskpilot.demo import demo_items
from diskpilot.scanner import scan_directory
from diskpilot.simulation import AppError, SimulationState
from diskpilot.duplicates import verify_duplicates


class SimulationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.state = SimulationState(self.root / 'data')
        self.state.replace_demo()

    def tearDown(self):
        self.state.close()
        self.tmp.cleanup()

    def test_mixed_actions_are_only_overlay_and_undo(self):
        ids = ['demo-cache-file', 'demo-startup', 'demo-service', 'demo-process', 'demo-software']
        plan = self.state.prepare(ids)
        self.assertEqual(len(plan['items']), 5)
        with patch('os.remove', side_effect=AssertionError('real deletion')), patch('os.rename', side_effect=AssertionError('real move')):
            result = self.state.execute(plan['id'])
            self.assertEqual(result['summary']['simulated_items'], 5)
            self.assertEqual(result['batch']['actual_reclaimed_bytes'], 0)
            result = self.state.undo(result['batch']['id'])
        self.assertEqual(result['summary']['simulated_items'], 0)

    def test_protection_and_bad_action_block(self):
        for key in ('demo-personal', 'demo-system', 'demo-privacy', 'demo-project'):
            with self.assertRaises(AppError):
                self.state.prepare([key])
        with self.assertRaises(AppError):
            self.state.prepare(['demo-startup'], action='delete')
        with self.assertRaises(AppError):
            self.state.prepare(['demo-cache-file'], ['*ShaderCache*'])

    def test_parent_child_selection_not_double_counted(self):
        plan = self.state.prepare(['demo-cache-folder', 'demo-cache-file'])
        self.assertEqual(len(plan['items']), 1)
        result = self.state.execute(plan['id'])
        self.assertEqual(result['summary']['simulated_bytes'], 73400320)
        self.assertEqual(result['summary']['simulated_items'], 2)

    def test_protected_descendant_prevents_parent(self):
        items = demo_items()
        parent = next(x for x in items if x['id'] == 'demo-cache-folder')
        child = next(x for x in items if x['id'] == 'demo-cache-file')
        child.update(risk='protected', actions=[])
        self.state.publish([parent, child], 'test')
        with self.assertRaisesRegex(AppError, '保护'):
            self.state.prepare([parent['id']])

    def test_catalog_and_policy_changes_invalidate_frozen_plan(self):
        p = self.state.prepare(['demo-cache-file'])
        with self.assertRaises(AppError):
            self.state.execute(p['id'], ['new-rule'])
        self.state.replace_demo()
        with self.assertRaises(AppError):
            self.state.execute(p['id'])

    def test_double_execution_and_repeat_simulation_block(self):
        p = self.state.prepare(['demo-cache-file'])
        self.state.execute(p['id'])
        with self.assertRaises(AppError):
            self.state.execute(p['id'])
        with self.assertRaises(AppError):
            self.state.prepare(['demo-cache-file'])
        self.state.reset()
        self.assertEqual(len(self.state.prepare(['demo-cache-file'])['items']), 1)

    def test_real_file_untouched_and_changed_target_rejected(self):
        fixture = self.root / 'fixture'
        fixture.mkdir()
        a = fixture / '保留原样.txt'
        a.write_text('sentinel-original', encoding='utf-8')
        before = hashlib.sha256(a.read_bytes()).hexdigest()
        result = scan_directory(str(fixture))
        self.state.publish(result['items'], 'fixture')
        file_item = next(x for x in result['items'] if x['kind'] == 'file')
        plan = self.state.prepare([file_item['id']])
        self.state.execute(plan['id'])
        self.assertEqual(hashlib.sha256(a.read_bytes()).hexdigest(), before)
        self.assertTrue(a.exists())
        self.state.reset()
        plan = self.state.prepare([file_item['id']])
        a.write_text('changed-after-preview', encoding='utf-8')
        with self.assertRaisesRegex(AppError, '变化'):
            self.state.execute(plan['id'])
        self.assertEqual(a.read_text(encoding='utf-8'), 'changed-after-preview')

    def test_incomplete_directory_is_not_actionable(self):
        item = next(x for x in demo_items() if x['kind'] == 'directory')
        item['details']['complete'] = False
        self.state.publish([item], 'partial')
        with self.assertRaises(AppError):
            self.state.prepare([item['id']])

    def test_persistence_and_reset_leave_history(self):
        self.state.execute(self.state.prepare(['demo-startup'])['id'])
        self.state.close()
        self.state = SimulationState(self.root / 'data')
        self.assertEqual(self.state.view()['summary']['simulated_items'], 1)
        self.state.reset()
        self.assertEqual(self.state.view()['summary']['simulated_items'], 0)
        self.assertEqual(self.state.history()[0]['status'], 'undone')

    def test_duplicates_verify_content_without_deleting(self):
        fixture = self.root / 'copies'
        fixture.mkdir()
        for name in ('a.txt', 'b.txt'):
            (fixture / name).write_bytes(b'identical-test-data')
        (fixture / 'c.txt').write_bytes(b'different-test-data')
        result = scan_directory(str(fixture))
        files = [x for x in result['items'] if x['kind'] == 'file']
        report = verify_duplicates(files)
        same = [x for x in report['groups'] if x['type'] == 'content']
        self.assertEqual(len(same), 1)
        self.assertEqual({x['name'] for x in same[0]['items']}, {'a.txt', 'b.txt'})
        self.assertEqual(len(list(fixture.iterdir())), 3)

    def test_unknown_or_unbounded_selection_rejected(self):
        for ids in ([], ['unknown'], 'demo-cache-file', [1], ['x'] * 501):
            with self.assertRaises(AppError):
                self.state.prepare(ids)

    def test_scan_to_preview_change_is_rejected(self):
        folder = self.root / 'stale'
        folder.mkdir()
        file = folder / 'sample.txt'
        file.write_text('before')
        result = scan_directory(str(folder))
        self.state.publish(result['items'], 'filesystem:' + str(folder))
        item = next(x for x in result['items'] if x['kind'] == 'file')
        file.write_text('changed-before-preview')
        with self.assertRaises(AppError):
            self.state.prepare([item['id']])

    def test_hardlink_logical_estimate_is_not_double_counted(self):
        import os
        folder = self.root / 'hardlinks'
        folder.mkdir()
        a, b = folder / 'a.txt', folder / 'b.txt'
        a.write_bytes(b'one-shared-file')
        os.link(a, b)
        result = scan_directory(str(folder))
        self.state.publish(result['items'], 'filesystem:' + str(folder))
        files = [x for x in result['items'] if x['kind'] == 'file']
        plan = self.state.prepare([x['id'] for x in files])
        self.assertEqual(len(plan['items']), 2)  # Two path actions, one data entity.
        self.assertEqual(plan['estimated_bytes'], len(b'one-shared-file'))
        view = self.state.execute(plan['id'])
        self.assertEqual(view['summary']['simulated_bytes'], len(b'one-shared-file'))
        self.assertTrue(a.exists() and b.exists())

    def test_nested_rescan_invalidates_simulated_ancestor(self):
        folder = self.root / 'parent'
        child = folder / 'child'
        child.mkdir(parents=True)
        file = child / 'sample.txt'
        file.write_text('before')
        result = scan_directory(str(folder))
        self.state.publish(result['items'], 'filesystem:' + str(folder))
        parent = next(x for x in result['items'] if x['path'] == str(folder))
        self.state.execute(self.state.prepare([parent['id']])['id'])
        file.write_text('a new revision')
        current = scan_directory(str(child))
        self.state.publish(current['items'], 'filesystem:' + str(child))
        view = self.state.view()
        self.assertFalse(any(x['simulated'] for x in view['items']))

    def test_user_protection_applies_to_hidden_directory_members(self):
        folder = self.root / 'protected-members'
        folder.mkdir()
        (folder / 'keep.secret-document.txt').write_text('preserve')
        result = scan_directory(str(folder))
        # Emulate a catalog where the scanner bounded its displayed child rows.
        parent = next(x for x in result['items'] if x['kind'] == 'directory')
        self.state.publish([parent], 'filesystem:' + str(folder))
        with self.assertRaises(AppError):
            self.state.prepare([parent['id']], ['*secret-document*'])


if __name__ == '__main__':
    unittest.main()
