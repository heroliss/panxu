import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from diskpilot.scan_index import ScanIndex
from diskpilot.scanner import scan_directory, validate_item_snapshot


class ScanIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / 'files'
        self.root.mkdir()
        self.db_path = self.base / 'state' / 'index.sqlite3'
        self.index = ScanIndex(self.db_path)

    def tearDown(self):
        self.index.close()
        self.temp.cleanup()

    def write(self, relative, data=b'data'):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def finish(self, scan_id, budget=256):
        for _ in range(20000):
            result = self.index.advance(scan_id, max_entries=budget)
            self.assertLessEqual(result['summary']['work_done'], budget)
            self.assertLessEqual(len(result['items']), 200)
            if result['summary']['status'] != 'running':
                return result
        self.fail('durable scan failed to make bounded forward progress')

    def pages(self, scan_id, cursor=None, limit=37):
        items = []
        for _ in range(20000):
            result = self.index.page(scan_id, cursor=cursor, limit=limit)
            self.assertLessEqual(len(result['items']), limit)
            items.extend(result['items'])
            cursor = result['next_cursor']
            if not result['has_more']:
                return items, cursor
            if not result['items']:
                self.fail('completed scan page unexpectedly stalled')
        self.fail('pagination failed to terminate')

    def restart(self):
        self.index.close()
        self.index = ScanIndex(self.db_path)

    def test_five_thousand_flat_files_are_reachable_without_prefix_rescans(self):
        for number in range(5000):
            self.write(f'file-{number:05d}.bin', b'x')
        real_scandir = os.scandir
        opens = []

        def observed(path):
            opens.append(os.fspath(path))
            return real_scandir(path)

        with patch('diskpilot.scan_index.os.scandir', side_effect=observed):
            scan_id = self.index.start(str(self.root))
            result = self.finish(scan_id, budget=137)
        self.assertEqual(opens, [str(self.root)])
        self.assertTrue(result['coverage']['complete'])
        self.assertEqual(result['aggregate']['file_count'], 5000)
        self.assertEqual(result['aggregate']['logical_bytes'], 5000)
        items, _ = self.pages(scan_id)
        files = [item for item in items if item['kind'] == 'file']
        self.assertEqual(len(files), 5000)
        self.assertEqual(len({item['id'] for item in files}), 5000)
        self.assertEqual({item['name'] for item in files}, {f'file-{n:05d}.bin' for n in range(5000)})
        directory = next(item for item in items if item['kind'] == 'directory')
        self.assertTrue(directory['details']['subtree_complete'])
        self.assertEqual(directory['actions'], [])
        self.assertTrue(directory['details']['requires_subtree_validation'])

    def test_restart_after_materialization_preserves_cursor_without_reenumeration(self):
        for number in range(90):
            self.write(f'item-{number:04d}.bin')
        scan_id = self.index.start(str(self.root))
        for _ in range(1000):
            result = self.index.advance(scan_id, max_entries=1)
            if result['coverage']['enumeration_complete']:
                break
        else:
            self.fail('names never materialized')
        first = self.index.page(scan_id, limit=17)
        saved_cursor = first['next_cursor']
        saved_work = result['coverage']['work_total']
        self.restart()
        self.assertEqual(self.index.status(scan_id)['coverage']['work_total'], saved_work)
        with patch('diskpilot.scan_index.os.scandir', side_effect=AssertionError('finished directory rescanned')):
            result = self.finish(scan_id)
        self.assertTrue(result['coverage']['complete'])
        self.assertEqual(result['coverage']['recheck_required_count'], 0)
        rest, _ = self.pages(scan_id, saved_cursor, limit=17)
        all_items = first['items'] + rest
        self.assertEqual(len(all_items), 91)
        self.assertEqual(len({item['id'] for item in all_items}), 91)

    def test_restart_of_unfinished_enumeration_is_explicit_and_keeps_observations(self):
        for number in range(130):
            self.write(f'item-{number:04d}.bin', b'xx')
        scan_id = self.index.start(str(self.root))
        early = self.index.advance(scan_id, max_entries=36)
        first_count = early['summary']['scanned_entries']
        self.assertGreater(first_count, 0)
        early_page = self.index.page(scan_id, limit=8)
        self.restart()
        status = self.index.status(scan_id)
        self.assertEqual(status['summary']['scanned_entries'], first_count)
        self.assertEqual(status['coverage']['recheck_required_count'], 1)
        self.assertTrue(any('重新枚举' in text for text in status['warnings']))
        result = self.finish(scan_id, budget=41)
        self.assertTrue(result['coverage']['complete'])
        self.assertGreater(result['coverage']['replay_work'], 0)
        self.assertEqual(result['aggregate']['file_count'], 130)
        self.assertEqual(result['aggregate']['logical_bytes'], 260)
        rest, _ = self.pages(scan_id, early_page['cursor'])
        all_items = early_page['items'] + rest
        self.assertEqual(len(all_items), 131)
        self.assertEqual(len({item['id'] for item in all_items}), 131)

    def test_cancel_resume_and_restart_do_not_claim_completion(self):
        for number in range(70):
            self.write(f'{number}.bin')
        scan_id = self.index.start(str(self.root))
        checks = []

        def cancel():
            checks.append(True)
            return len(checks) > 9

        result = self.index.advance(scan_id, max_entries=200, cancel=cancel)
        self.assertEqual(result['summary']['status'], 'cancelled')
        self.assertEqual(result['summary']['work_done'], 9)
        self.assertFalse(self.index._live)
        self.assertFalse(result['coverage']['complete'])
        count = result['summary']['scanned_entries']
        self.assertEqual(self.index.advance(scan_id)['summary']['work_done'], 0)
        self.restart()
        self.assertEqual(self.index.status(scan_id)['summary']['status'], 'cancelled')
        self.assertEqual(self.index.status(scan_id)['summary']['scanned_entries'], count)
        self.index.resume(scan_id)
        self.assertTrue(self.finish(scan_id)['coverage']['complete'])

    def test_repeated_cancellation_releases_handles_and_resume_meters_replay(self):
        for number in range(20):
            self.write(f'{number:03d}.bin')
        scans = []
        for _ in range(10):
            scan_id = self.index.start(str(self.root))
            scans.append(scan_id)
            self.index.advance(scan_id, max_entries=5)
            iterator = self.index._live[scan_id][1]
            paused = self.index.cancel(scan_id)
            self.assertFalse(self.index._live)
            self.assertEqual(paused['summary']['status'], 'cancelled')
            self.assertEqual(paused['coverage']['recheck_required_count'], 1)
            with self.assertRaises(StopIteration):
                next(iterator)
        scan_id = scans[0]
        self.index.resume(scan_id)
        complete = self.finish(scan_id, budget=11)
        self.assertTrue(complete['coverage']['complete'])
        self.assertEqual(complete['aggregate']['file_count'], 20)
        self.assertGreater(complete['coverage']['replay_work'], 0)
        items, _ = self.pages(scan_id)
        self.assertEqual(len(items), 21)
        self.assertEqual(len({item['id'] for item in items}), 21)

    def test_completed_advance_rechecks_root_before_reporting_complete(self):
        self.write('old.bin')
        scan_id = self.index.start(str(self.root))
        self.assertTrue(self.finish(scan_id)['coverage']['complete'])
        self.write('added-after-completion.bin')
        result = self.index.advance(scan_id)
        self.assertEqual(result['summary']['status'], 'invalidated')
        self.assertFalse(result['coverage']['complete'])
        self.assertEqual(result['summary']['work_done'], 0)
        self.assertTrue(all(not item['actions'] for item in result['items']))

    def test_directory_identity_change_invalidates_retained_observations(self):
        self.write('old.bin')
        scan_id = self.index.start(str(self.root))
        self.finish(scan_id)
        old = self.base / 'old-files'
        self.root.rename(old)
        self.root.mkdir()
        self.write('new.bin')
        result = self.index.page(scan_id)
        self.assertEqual(result['summary']['status'], 'invalidated')
        self.assertFalse(result['coverage']['complete'])
        self.assertTrue(all(not item['actions'] for item in result['items']))
        self.assertIn('old.bin', {item['name'] for item in result['items']})
        self.assertNotIn('new.bin', {item['name'] for item in result['items']})
        self.assertEqual(self.index.advance(scan_id)['summary']['work_done'], 0)

    def test_file_changes_and_new_ancestor_protection_invalidate_pages(self):
        file = self.write('nested/data.bin', b'a')
        scan_id = self.index.start(str(self.root))
        self.finish(scan_id)
        file.write_bytes(b'changed-length')
        result = self.index.page(scan_id)
        self.assertTrue(result['coverage']['invalidated'])
        self.assertTrue(all(not item['actions'] for item in result['items']))
        other = self.index.start(str(self.root))
        self.finish(other)
        (self.base / '.git').mkdir()
        self.assertEqual(self.index.status(other)['summary']['status'], 'invalidated')

    def test_inaccessible_directory_and_depth_limit_preserve_failed_coverage(self):
        denied = self.write('denied/private.bin').parent
        self.write('available.bin')
        real_scandir = os.scandir

        def selective(path):
            if os.fspath(path) == str(denied):
                raise PermissionError('fixture')
            return real_scandir(path)

        scan_id = self.index.start(str(self.root))
        with patch('diskpilot.scan_index.os.scandir', side_effect=selective):
            result = self.finish(scan_id)
        self.assertEqual(result['summary']['status'], 'partial')
        self.assertEqual(result['aggregate']['file_count'], 1)
        self.assertGreater(result['coverage']['error_count'], 0)
        self.assertFalse(result['coverage']['enumeration_complete'])
        self.assertTrue(all(item['actions'] == [] for item in result['items'] if item['kind'] == 'directory'))
        limited = self.index.start(str(self.root), max_depth=1)
        result = self.finish(limited)
        self.assertFalse(result['coverage']['complete'])
        self.assertGreater(result['coverage']['depth_limited'], 0)
        self.assertFalse(result['coverage']['enumeration_complete'])

    def test_no_symlink_is_followed_and_no_false_complete_directory(self):
        outside = self.base / 'outside'
        outside.mkdir()
        (outside / 'secret.bin').write_bytes(b'outside')
        self.write('own.bin', b'x')
        try:
            os.symlink(outside, self.root / 'link', target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f'environment cannot create symlink: {exc}')
        scan_id = self.index.start(str(self.root))
        result = self.finish(scan_id)
        self.assertEqual(result['summary']['status'], 'partial')
        self.assertEqual(result['coverage']['reparse_skipped'], 1)
        self.assertEqual(result['aggregate']['file_count'], 1)
        items, _ = self.pages(scan_id)
        self.assertNotIn('secret.bin', {item['name'] for item in items})
        with self.assertRaises(ValueError):
            self.index.start(str(self.root / 'link'))

    def test_shared_scanner_classification_signatures_and_hardlink_accounting(self):
        one = self.write('same.bin', b'shared')
        self.write('personal.txt')
        self.write('.ssh/config')
        self.write('project/.git/HEAD')
        self.write('project/Library/build.bin')
        try:
            os.link(one, self.root / 'alias.bin')
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f'environment cannot create hardlink: {exc}')
        legacy = {item['path']: item for item in scan_directory(str(self.root))['items'] if item['kind'] == 'file'}
        scan_id = self.index.start(str(self.root))
        result = self.finish(scan_id)
        items, _ = self.pages(scan_id)
        for item in items:
            if item['kind'] == 'file':
                old = legacy[item['path']]
                for key in ('id', 'signature', 'risk', 'category', 'actions'):
                    self.assertEqual(item[key], old[key])
                self.assertTrue(validate_item_snapshot(item))
        self.assertEqual(result['aggregate']['hardlink_duplicate_bytes'], 6)
        root = next(item for item in items if item['path'] == str(self.root))
        self.assertGreater(root['details']['protected_entries'], 0)
        self.assertFalse(root['details']['can_simulate_delete'])

    def test_cursor_is_bound_to_scan_and_acquisition_is_not_pagination(self):
        self.write('a.bin')
        self.write('b.bin')
        scan_id = self.index.start(str(self.root))
        early = self.index.advance(scan_id, max_entries=2)
        page = self.index.page(scan_id, limit=1)
        self.assertEqual(len(page['items']), 1)
        self.assertTrue(page['has_more'])
        cursor = page['cursor']
        self.finish(scan_id)
        rest, _ = self.pages(scan_id, cursor, limit=1)
        self.assertEqual(len(rest), 2)
        other = self.index.start(str(self.root))
        with self.assertRaises(ValueError):
            self.index.page(other, cursor=cursor)
        for cursor in ('broken', '!', 'a' * 300):
            with self.assertRaises(ValueError):
                self.index.page(scan_id, cursor=cursor)
        for limit in (0, True, 201):
            with self.assertRaises(ValueError):
                self.index.page(scan_id, limit=limit)
        self.assertFalse(early['coverage']['complete'])

    def test_cancel_signal_failure_pauses_instead_of_claiming_success(self):
        self.write('data.bin')
        scan_id = self.index.start(str(self.root))

        def failed():
            raise RuntimeError('fixture')

        result = self.index.advance(scan_id, cancel=failed)
        self.assertEqual(result['summary']['status'], 'cancelled')
        self.assertEqual(result['summary']['work_done'], 0)
        self.assertTrue(any('取消信号' in warning for warning in result['warnings']))
        self.index.resume(scan_id)
        self.assertTrue(self.finish(scan_id)['coverage']['complete'])

    def test_persisted_total_entry_limit_is_not_a_per_step_or_model_budget(self):
        self.index.close()
        self.index = ScanIndex(self.db_path, max_total_entries=10)
        for number in range(30):
            self.write(f'{number:03d}.bin')
        scan_id = self.index.start(str(self.root))
        result = self.finish(scan_id, budget=4)
        self.assertEqual(result['summary']['status'], 'partial')
        self.assertTrue(result['coverage']['limit_reached'])
        self.assertFalse(result['coverage']['enumeration_complete'])
        self.assertEqual(result['coverage']['spooled_entries'], 10)
        self.assertEqual(result['aggregate']['file_count'], 9)
        self.assertTrue(any('缩小范围' in value for value in result['warnings']))
        items, _ = self.pages(scan_id, limit=3)
        self.assertEqual(len(items), 10)
        self.restart()  # The new constructor's larger default cannot raise this scan's limit.
        result = self.index.advance(scan_id, max_entries=512)
        self.assertEqual(result['coverage']['entry_limit'], 10)
        self.assertEqual(result['coverage']['spooled_entries'], 10)
        self.assertEqual(result['summary']['work_done'], 0)
        for value in (0, True, 100001):
            with self.assertRaises(ValueError):
                ScanIndex(self.db_path, max_total_entries=value)


if __name__ == '__main__':
    unittest.main()
