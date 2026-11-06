import os
from unittest import mock

from helpers import TempDirTest

from panxu import checkup, duplicates, fsops, junk, paths
from panxu.analyst import Analyst, VerdictCache, _fmt_size
from panxu.api import Backend
from panxu.insights import Insights
from panxu.knowledge import Knowledge
from panxu.oplog import OperationLog
from panxu.scanner import SpaceScan


MB = 1024 * 1024


class AllocatedSizeIntegrationTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.env = self.posix_env()
        self.policy = paths.ProtectionPolicy(self.env, allowed_roots=[self.tmp])

    def allocation(self, by_name):
        def occupied(path, st=None):
            st = st if st is not None else os.lstat(fsops.long_path(path))
            return by_name.get(os.path.basename(path), st.st_size)
        return mock.patch.object(fsops, 'file_size', side_effect=occupied)

    def bare_backend(self, scan):
        # Only the shared scan/pre-flight methods are exercised: no registry,
        # installed programs, real settings or OS state is consulted.
        backend = Backend.__new__(Backend)
        backend.scan = scan
        backend.policy = lambda: self.policy
        backend.knowledge = Knowledge(self.env)
        backend.plan = backend.insights = backend.dups = None
        return backend

    def test_junk_scan_plan_clean_and_log_count_occupied_bytes(self):
        old = self.make('Users/alice/AppData/Local/Temp/old.tmp', 2 * MB, age_days=2)
        fresh = self.make('Users/alice/AppData/Local/Temp/fresh.tmp', MB)
        log = OperationLog(os.path.join(self.tmp, 'data', 'history.jsonl'))
        engine = junk.JunkEngine(self.env, admin=False, processes=set(), oplog=log)
        with self.allocation({'old.tmp': 4096}):
            rows = engine.scan(['user_temp'])
            builder = checkup.PlanBuilder(self.policy)
            builder.add_junk(rows)
            self.assertEqual((rows[0]['size'], rows[0]['logical_size'], rows[0]['count']), (4096, 2 * MB, 1))
            item = builder.items[0]
            self.assertEqual((item['size'], item['logical_size']), (4096, 2 * MB))
            self.assertTrue(item['selected'])
            result = engine.clean(['user_temp'])
        self.assertEqual(result['freed'], 4096)
        self.assertEqual(result['rules'][0]['freed'], 4096)
        self.assertEqual(log.entries()[0]['freed'], 4096)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(fresh))

    def test_personal_versions_compare_content_but_findings_and_plan_use_occupied_bytes(self):
        original = self.make('Users/alice/Documents/report.txt', 2 * MB, age_days=30)
        copy = self.make('Users/alice/Documents/report(1).txt', 2 * MB, age_days=29)
        with self.allocation({'report.txt': MB + MB // 2, 'report(1).txt': MB}):
            scan = Insights(self.env).collect([os.path.dirname(original)])
            found = next(f for f in scan.findings() if f['kind'] == 'name_versions')
        self.assertEqual((found['size'], found['logical_size']), (2 * MB + MB // 2, 4 * MB))
        copies = {f['path']: f for f in found['files']}
        self.assertTrue(copies[copy]['remove'], 'different allocation must not prevent identical-content matching')
        self.assertFalse(copies[original]['remove'])
        self.assertEqual(copies[copy]['logical_size'], 2 * MB)
        self.assertEqual(scan.stats['bytes'], found['size'])
        builder = checkup.PlanBuilder(self.policy)
        builder.add_insights([found], {})
        self.assertEqual(len(builder.items), 1)
        item = builder.items[0]
        self.assertEqual((item['size'], item['logical_size'], item['paths']), (MB, 2 * MB, [copy]))
        self.assertFalse(item['selected'], 'allocation accounting never auto-selects personal files')

    def test_duplicates_keep_logical_length_for_hash_and_guard_but_size_each_copy(self):
        copies = [self.make(f'dup/{name}.bin', 2 * MB, age_days=age)
                  for name, age in (('one', 30), ('two', 20), ('three', 10))]
        used = {'one.bin': 4096, 'two.bin': 8192, 'three.bin': 12288}
        with self.allocation(used):
            result = duplicates.find_duplicates([os.path.dirname(copies[0])], min_size=1)
        self.assertEqual(len(result['groups']), 1)
        group = result['groups'][0]
        self.assertEqual(group['size'], 2 * MB)
        self.assertEqual(group['wasted'], 8192 + 12288)
        self.assertEqual(result['stats']['wasted'], group['wasted'])
        self.assertEqual({f['path']: f['size'] for f in group['files']},
                         {p: used[os.path.basename(p)] for p in copies})
        for keeper in copies:
            choice = checkup.dup_choice(group['size'], group['files'], {keeper})
            self.assertEqual(choice['size'], sum(used.values()) - used[os.path.basename(keeper)])
            self.assertEqual(choice['logical_size'], 4 * MB)
        every = checkup.dup_choice(group['size'], group['files'], set())
        self.assertEqual((every['size'], every['logical_size']), (sum(used.values()), 6 * MB))
        self.assertTrue(duplicates.verify_unchanged(copies[0], group['size'], group['files'][0]['mtime']))
        self.assertFalse(duplicates.verify_unchanged(copies[0], 4096, group['files'][0]['mtime']))
        builder = checkup.PlanBuilder(self.policy)
        builder.add_duplicates(result)
        item = builder.items[0]
        self.assertEqual(item['size'], sum(used[os.path.basename(p)] for p in item['paths']))
        self.assertEqual(item['logical_size'], group['size'] * len(item['paths']))
        self.assertFalse(item['selected'])

    def test_preflight_and_removed_measurements_update_scan_in_both_units(self):
        target = self.make('area/sub/large.bin', 10000)
        self.make('area/keep.bin', 3000)
        with self.allocation({'large.bin': 16, 'keep.bin': 2000}):
            scan = SpaceScan(os.path.join(self.tmp, 'area')).run()
            backend = self.bare_backend(scan)
            rows = backend.m_check_paths([target, os.path.dirname(target)])
            for row in rows:
                self.assertEqual((row['size'], row['logical_size'], row['files']), (16, 10000, 1))
                self.assertIsNone(row['blocked'])
            stats = fsops.delete_path(target)
        self.assertEqual(stats.measured[target], (16, 10000, 1))
        backend.forget_paths([target], stats.measured)
        self.assertEqual((scan.root.size, scan.root.logical_size, scan.root.files), (2000, 3000, 1))
        self.assertFalse(any(f['path'] == target for f in scan.top_files))

    def test_ai_summary_ranks_types_and_files_by_occupied_bytes(self):
        self.make('summary/huge.bin', 10000)
        self.make('summary/solid.mp4', 3000)
        analyst = Analyst(VerdictCache(os.path.join(self.tmp, 'ai_cache.json')), self.env, Knowledge(self.env))
        with self.allocation({'huge.bin': 16, 'solid.mp4': 2000}):
            scan = SpaceScan(os.path.join(self.tmp, 'summary')).run()
            summary = analyst.summarize(scan, scan.root, 1, anonymize=True)
        self.assertEqual((summary['size'], summary['logical_size']), (_fmt_size(2016), _fmt_size(13000)))
        self.assertEqual(summary['types'][0], ['.mp4', _fmt_size(2000)])
        self.assertEqual(summary['largest_files'][0], ['solid.mp4', _fmt_size(2000)])
        self.assertEqual(summary['largest_files'][1], ['huge.bin', _fmt_size(16)])
