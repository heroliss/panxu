"""Size/accounting regressions; all files and deletions are temporary fixtures."""
import ctypes
import os
import stat
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from helpers import TempDirTest
from panxu import fsops, junk, sandbox, winapi


class FileSizeTest(TempDirTest):
    def metadata(self, attrs=0, size=64 * 1024 ** 3, mode=stat.S_IFREG):
        return SimpleNamespace(st_mode=mode, st_size=size, st_file_attributes=attrs)

    def test_ordinary_files_use_cached_stat_without_an_extra_query(self):
        st = self.metadata(size=1234)
        with patch.object(winapi, 'file_allocation_size') as query, patch.object(os, 'lstat') as lstat:
            self.assertEqual(fsops.file_size('fixture.bin', st), 1234)
        query.assert_not_called()
        lstat.assert_not_called()

    def test_sparse_and_compressed_use_allocation_including_zero(self):
        for attrs in (winapi.FILE_ATTRIBUTE_SPARSE_FILE, winapi.FILE_ATTRIBUTE_COMPRESSED):
            for allocated in (0, 128 * 1024, 5 * 1024 ** 3):
                with self.subTest(attrs=attrs, allocated=allocated):
                    with patch.object(winapi, 'file_allocation_size', return_value=allocated) as query:
                        self.assertEqual(fsops.file_size('fixture.bin', self.metadata(attrs)), allocated)
                    query.assert_called_once()

    def test_unavailable_allocation_falls_back_to_logical_size(self):
        with patch.object(winapi, 'file_allocation_size', return_value=None):
            self.assertEqual(fsops.file_size('fixture.bin', self.metadata(winapi.FILE_ATTRIBUTE_SPARSE_FILE)),
                             64 * 1024 ** 3)

    def test_cloud_files_links_and_directories_never_query_allocation(self):
        examples = [self.metadata(winapi.FILE_ATTRIBUTE_SPARSE_FILE | bit)
                    for bit in (winapi.FILE_ATTRIBUTE_OFFLINE, winapi.FILE_ATTRIBUTE_RECALL_ON_OPEN,
                                winapi.FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS)]
        examples.extend([self.metadata(mode=stat.S_IFLNK), self.metadata(mode=stat.S_IFDIR),
                         self.metadata(winapi.FILE_ATTRIBUTE_REPARSE_POINT)])
        with patch.object(winapi, 'file_allocation_size') as query:
            for st in examples:
                self.assertEqual(fsops.file_size('fixture.bin', st), 0)
        query.assert_not_called()

    def test_missing_path_sizes_and_cancellation(self):
        self.assertEqual(fsops.path_sizes(os.path.join(self.tmp, 'missing')), (0, 0, 0))
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(fsops.Cancelled):
            fsops.path_sizes(self.tmp, cancel)

    def test_tree_and_delete_account_for_allocation(self):
        first = self.make('tree/one.bin', 5000)
        self.make('tree/sub/two.bin', 8000)
        tree = os.path.join(self.tmp, 'tree')
        with patch.object(fsops, 'file_size', return_value=128):
            self.assertEqual(fsops.path_sizes(tree), (256, 13000, 2))
            self.assertEqual(fsops.path_size(tree), (256, 2))
            self.assertEqual(fsops.path_sizes(first), (128, 5000, 1))
            deleted = fsops.delete_matching(tree, lambda name: name == 'one.bin')
            self.assertEqual((deleted.bytes, deleted.files), (128, 1))
            deleted = fsops.delete_path(tree)
            self.assertEqual((deleted.bytes, deleted.files), (128, 1))
            self.assertEqual(deleted.measured[tree], (128, 8000, 1))

    def test_single_file_delete_accounts_for_allocation(self):
        path = self.make('one.bin', 5000)
        with patch.object(fsops, 'file_size', return_value=128):
            deleted = fsops.delete_path(path)
        self.assertEqual((deleted.bytes, deleted.files), (128, 1))
        self.assertEqual(deleted.measured[path], (128, 5000, 1))
        merged = fsops.DeleteStats()
        merged.merge(deleted)
        self.assertEqual(merged.measured, deleted.measured)
        self.assertNotIn('measured', merged.as_dict())

    def test_recycle_and_permanent_fallback_account_for_allocation(self):
        # A fake recycle implementation, never the developer's Windows Recycle Bin.
        for binned in (True, False):
            with self.subTest(binned=binned):
                path = self.make('selected.bin', 5000)

                def move(selected, hwnd):
                    for item in selected:
                        fsops.delete_path(item)
                    return 0, False

                arrived = {os.path.normcase(os.path.abspath(path))} if binned else set()
                with patch.object(fsops, 'file_size', return_value=128), \
                        patch.object(winapi, 'recycle_paths', side_effect=move), \
                        patch.object(fsops, '_arrived_in_bin', return_value=arrived):
                    result, done = fsops.recycle([path])
                self.assertEqual((result.bytes, result.files, done), (128, 1, [path]))
                self.assertEqual(result.permanent_bytes, 0 if binned else 128)
                self.assertEqual(result.measured[path], (128, 5000, 1))

    def test_link_sizes_do_not_include_the_target(self):
        target = self.make('outside/keep.bin', 5000)
        link = os.path.join(self.tmp, 'link')
        os.symlink(target, link)
        self.assertEqual(fsops.file_size(link), 0)
        self.assertEqual(fsops.path_sizes(link), (0, 0, 0))

    def test_simulated_allocation_is_injected_and_confined(self):
        sb = sandbox.Sandbox(os.path.join(self.tmp, 'sim'))
        inside = self.make('sim/C/fake.bin', 5000)
        outside = self.make('outside/keep.bin', 9000)
        with patch.object(winapi, '_sim', sb):
            self.assertEqual(winapi.file_allocation_size(inside), 5000)
            # No host metadata query, even if the requested path exists.
            with patch.object(os, 'lstat', side_effect=AssertionError('queried outside simulation')):
                self.assertEqual(winapi.file_allocation_size(outside), 0)
        link = os.path.join(sb.root, 'link')
        os.symlink(outside, link)
        self.assertEqual(sb.file_allocation_size(link), 0)


class RecycleBinSizeTest(TempDirTest):
    def engine(self):
        return junk.JunkEngine(self.posix_env(), admin=False, processes=set())

    def test_scan_labels_shell_size_as_an_estimate(self):
        with patch.object(winapi, 'recycle_bin_info', return_value=(64 * 1024 ** 3, 1)):
            row = self.engine().scan(['recycle_bin'])[0]
        self.assertIn('估算', row['size_note'])
        self.assertIn('标称', row['size_note'])

    def test_cleanup_counts_net_free_space_across_the_same_drives(self):
        before = {'C:\\': 1000, 'D:\\': 2000}
        after = {'C:\\': 1700, 'D:\\': 1800, 'E:\\': 999999}
        with patch.object(junk, 'free_by_drive', side_effect=[before, after]) as snapshot, \
                patch.object(winapi, 'empty_recycle_bin', return_value=True) as empty, \
                patch.object(winapi, 'recycle_bin_info', return_value=(64 * 1024 ** 3, 1)) as shell:
            result = self.engine().clean(['recycle_bin'])
        self.assertEqual(result['freed'], 500)
        self.assertEqual(snapshot.call_args_list[1].args, (list(before),))
        empty.assert_called_once_with(None)
        shell.assert_not_called()

    def test_missing_snapshots_and_failed_empty_never_use_shell_estimate(self):
        for snapshots, emptied in (([{}, {}], True), ([{'C:\\': 1000}, {}], True),
                                    ([{'C:\\': 1000}], False),
                                    ([{'C:\\': 1000}, {'C:\\': 900}], True)):
            with self.subTest(snapshots=snapshots, emptied=emptied):
                with patch.object(junk, 'free_by_drive', side_effect=snapshots), \
                        patch.object(winapi, 'empty_recycle_bin', return_value=emptied), \
                        patch.object(winapi, 'recycle_bin_info', return_value=(64 * 1024 ** 3, 1)) as shell:
                    result = self.engine().clean(['recycle_bin'])
                self.assertEqual(result['freed'], 0)
                shell.assert_not_called()

    def test_snapshot_uses_injected_sandbox_drives_and_usage(self):
        first = os.path.join(self.tmp, 'sim', 'C')
        second = os.path.join(self.tmp, 'sim', 'D')
        sim = SimpleNamespace(drives=lambda: [{'root': first}, {'root': second}])
        with patch.object(winapi, 'simulation', return_value=sim), \
                patch.object(winapi, 'disk_usage', side_effect=[SimpleNamespace(free=100), OSError('unreadable')]) as usage, \
                patch('psutil.disk_partitions', side_effect=AssertionError('queried host drives')):
            self.assertEqual(junk.free_by_drive(), {first: 100})
        self.assertEqual([call.args[0] for call in usage.call_args_list], [first, second])

    def test_snapshot_explicit_roots_skip_discovery_and_duplicates(self):
        root = os.path.join(self.tmp, 'drive')
        with patch.object(winapi, 'simulation', side_effect=AssertionError('rediscovered drives')), \
                patch.object(winapi, 'disk_usage', return_value=SimpleNamespace(free=123)) as usage:
            self.assertEqual(junk.free_by_drive([root, root]), {root: 123})
        usage.assert_called_once_with(root)


@unittest.skipUnless(winapi.IS_WINDOWS, 'Win32 API behavior requires Windows')
class WindowsAllocationTest(TempDirTest):
    def test_win32_high_dword_zero_and_error_sentinel(self):
        for low, high, error, expected in ((0, 0, 0, 0), (123, 2, 0, (2 << 32) + 123),
                                            (0xffffffff, 0, 0, 0xffffffff),
                                            (0xffffffff, 3, 0, (3 << 32) | 0xffffffff),
                                            (0xffffffff, 0, 5, None)):
            with self.subTest(low=low, high=high, error=error):
                def query(path, high_ptr):
                    self.assertEqual(ctypes.get_last_error(), 0)
                    high_ptr._obj.value = high
                    ctypes.set_last_error(error)
                    return low

                ctypes.set_last_error(5)
                with patch.object(winapi._kernel32, 'GetCompressedFileSizeW', side_effect=query):
                    self.assertEqual(winapi.file_allocation_size('fake.bin'), expected)

    def test_real_sparse_file_counts_disk_bytes_and_deletion(self):
        path = os.path.join(self.tmp, 'sparse.bin')
        logical = 8 * 1024 ** 3 + 1
        with open(path, 'wb') as fh:
            if not winapi.make_sparse(fh):
                self.skipTest('temporary volume does not support sparse files')
            fh.write(b'x')
            fh.seek(logical - 1)
            fh.write(b'x')
        st = os.lstat(path)
        self.assertTrue(st.st_file_attributes & winapi.FILE_ATTRIBUTE_SPARSE_FILE)
        allocated = winapi.file_allocation_size(path)
        self.assertIsNotNone(allocated)
        self.assertGreater(allocated, 0)
        self.assertLess(allocated, 2 * 1024 ** 2)
        self.assertEqual(fsops.path_sizes(path), (allocated, logical, 1))
        deleted = fsops.delete_path(path)
        self.assertEqual((deleted.bytes, deleted.files), (allocated, 1))
        self.assertEqual(deleted.measured[path], (allocated, logical, 1))
        self.assertFalse(os.path.exists(path))


if __name__ == '__main__':
    unittest.main()
