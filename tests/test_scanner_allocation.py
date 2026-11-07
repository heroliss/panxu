import os
import queue
import stat
from types import SimpleNamespace
from unittest import mock

from helpers import TempDirTest

from panxu import scanner, winapi


class ScannerAllocationTest(TempDirTest):
    def build(self, top_limit=scanner.TOP_FILES):
        self.make('root/sparse/huge.bin', 10000)
        self.make('root/solid/medium.mp4', 2000)
        self.make('root/top.iso', 300)
        root = os.path.join(self.tmp, 'root')

        def occupied(path, st=None):
            return 16 if os.path.basename(path) == 'huge.bin' else st.st_size

        with mock.patch.object(scanner.fsops, 'file_size', side_effect=occupied, create=True), \
                mock.patch.object(scanner, 'TOP_FILES', top_limit):
            scan = scanner.SpaceScan(root, threads=2).run()
        return scan, occupied

    def test_directory_rollup_extensions_and_top_order_use_occupied_bytes(self):
        scan, _ = self.build(top_limit=2)
        self.assertEqual(scan.root.size, 2316)
        self.assertEqual(scan.root.logical_size, 12300)
        self.assertEqual(scan.stats['bytes'], 2316)
        self.assertEqual(scan.stats['logical_bytes'], 12300)
        self.assertEqual([n.name for n in scan.root.children], ['solid', 'sparse'])
        self.assertEqual([os.path.basename(f['path']) for f in scan.top_files], ['medium.mp4', 'top.iso'])
        self.assertEqual(scan.exts['.bin'], [1, 16])
        self.assertEqual(sum(c['bytes'] for c in scan.ext_summary()), 2316)

    def test_live_file_rows_keep_both_sizes_and_actual_remainder(self):
        scan, occupied = self.build()
        sparse = scan.find(os.path.join(scan.root_path, 'sparse'))
        with mock.patch.object(scanner.fsops, 'file_size', side_effect=occupied, create=True):
            rows, count, rest = scan.own_files(sparse)
            hidden, all_count, hidden_bytes = scan.own_files(sparse, limit=0)
        self.assertEqual((count, rest), (1, 0))
        self.assertEqual((rows[0]['size'], rows[0]['logical_size']), (16, 10000))
        self.assertEqual((hidden, all_count, hidden_bytes), ([], 1, 16))
        top = next(f for f in scan.top_files if f['path'] == rows[0]['path'])
        self.assertEqual((top['size'], top['logical_size']), (16, 10000))

    def test_remove_directory_updates_both_ancestor_totals(self):
        scan, _ = self.build()
        scan.remove_path(os.path.join(scan.root_path, 'sparse'), 0, 0)
        self.assertEqual((scan.root.size, scan.root.logical_size, scan.root.files), (2300, 2300, 2))
        self.assertEqual(scan.stats['logical_bytes'], 2300)
        self.assertEqual([c.name for c in scan.root.children], ['solid'])
        self.assertFalse(any(f['path'].endswith('huge.bin') for f in scan.top_files))

    def test_remove_file_outside_top_list_uses_explicit_logical_size(self):
        scan, _ = self.build(top_limit=1)
        scan.remove_path(os.path.join(scan.root_path, 'sparse', 'huge.bin'), 16, 1, logical_size=10000)
        self.assertEqual((scan.root.size, scan.root.logical_size), (2300, 2300))
        sparse = scan.find(os.path.join(scan.root_path, 'sparse'))
        self.assertEqual((sparse.size, sparse.logical_size, sparse.files), (0, 0, 0))

    def test_remove_top_file_can_recover_logical_size_for_old_callers(self):
        scan, _ = self.build()
        scan.remove_path(os.path.join(scan.root_path, 'sparse', 'huge.bin'), 16, 1)
        self.assertEqual((scan.root.size, scan.root.logical_size), (2300, 2300))

    def test_cloud_file_retains_logical_bytes_without_querying_allocation(self):
        path = self.make('cloud/online.bin', 10000)
        st = SimpleNamespace(st_mode=stat.S_IFREG, st_size=10000, st_mtime=1,
                             st_file_attributes=winapi.FILE_ATTRIBUTE_OFFLINE)
        entry = SimpleNamespace(name='online.bin', stat=lambda **kw: st)
        iterator = mock.MagicMock()
        iterator.__iter__.return_value = iter([entry])
        scan = scanner.SpaceScan(os.path.dirname(path))
        state = scanner._WorkerState()
        with mock.patch.object(scanner.os, 'scandir', return_value=iterator), \
                mock.patch.object(scanner.fsops, 'file_size', create=True) as occupied:
            scan._scan_dir(scan.root, scan.root_path, queue.Queue(), state)
        scan._finish([state], 0)
        self.assertEqual((scan.root.size, scan.root.logical_size, scan.root.files), (0, 10000, 1))
        self.assertEqual((scan.stats['cloud_bytes'], scan.stats['cloud_files']), (10000, 1))
        occupied.assert_not_called()
        self.assertEqual(scan.top_files, [])
