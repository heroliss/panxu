import os
import time
import unittest
import zipfile
from unittest.mock import patch

from helpers import TempDirTest, age
from panxu import checkup, duplicates, insights, junk, paths
from panxu.scanner import FLAG_CLOUD, FLAG_DENIED, FLAG_EXCLUDED, FLAG_LINKS, Node, SpaceScan


class ScopeTest(TempDirTest):
    def test_multiple_disks_have_one_tree_unique_ids_and_correct_deletion_updates(self):
        a = self.make('C/Data/a.bin', 20)
        b = self.make('D/Stuff/b.bin', 30)
        c, d = [os.path.join(self.tmp, n) for n in ('C', 'D')]
        scan = SpaceScan([c, d, os.path.dirname(a)]).run()
        self.assertEqual(scan.root.size, 50)
        self.assertEqual(scan.path_of(scan.root), '')
        self.assertIsNone(scan.find(''))
        self.assertEqual(scan.own_files(scan.root), ([], 0, 0))
        self.assertEqual(len({n.id for n in scan.nodes}), len(scan.nodes))
        self.assertEqual(scan.path_of(scan.find(os.path.dirname(b))), os.path.dirname(b))
        self.assertEqual({f['path'] for f in scan.top_files}, {a, b})
        scan.remove_path(b, 30, 1)
        self.assertEqual(scan.stats['bytes'], 20)
        self.assertEqual(scan.find(d).size, 0)

    def test_space_scan_obeys_exclusions_and_does_not_list_omitted_files_live(self):
        kept = self.make('disk/keep/a', 7)
        secret = self.make('disk/secret/private', 11)
        root = os.path.dirname(os.path.dirname(kept))
        scan = SpaceScan([root], skip=lambda p: paths.is_within(p, os.path.dirname(secret))).run()
        self.assertEqual(scan.root.size, 7)
        self.assertTrue(scan.root.flags & FLAG_EXCLUDED)
        self.assertTrue(scan.stats['incomplete'])
        self.assertIsNone(scan.find(os.path.dirname(secret)))
        files, _, _ = scan.own_files(scan.find(os.path.dirname(kept)))
        self.assertEqual([f['path'] for f in files], [kept])

    def test_space_scope_includes_data_drives_but_not_other_accounts(self):
        env = self.posix_env()
        for p in ('Users/alice/Desktop/a', 'Users/bob/private/a', 'Data/a', 'other/Projects/a'):
            self.make(p)
        other = os.path.join(self.tmp, 'other')
        with patch('panxu.winapi.known_folder', return_value=''):
            policy = paths.ProtectionPolicy(env, allowed_roots=[self.tmp], drive_roots=[self.tmp])
            skip = checkup.space_skip(env, policy)
            self.assertFalse(skip(os.path.join(self.tmp, 'Users/alice/Desktop')))
            self.assertTrue(skip(os.path.join(self.tmp, 'Users/bob/private')))
            roots = checkup.space_roots(env, {}, [{'root': other, 'fixed': True}, {'root': self.tmp, 'fixed': True}])
            self.assertEqual([paths.key(r) for r in roots], [paths.key(self.tmp)])

    def test_creation_time_counts_as_activity(self):
        file = self.make('disk/app/file', 8)
        # Preserve an old archive timestamp while creation is fresh on Windows.
        old = time.time() - 400 * 86400
        os.utime(file, (old, old))
        scan = SpaceScan(os.path.join(self.tmp, 'disk')).run()
        if os.name == 'nt':
            self.assertGreater(scan.root.changed, old + 300 * 86400)


class DuplicateScopeTest(TempDirTest):
    def test_time_limit_keeps_completed_groups_before_visiting_more_candidates(self):
        for name, size in [('a', 200000), ('b', 200000), ('c', 190000), ('d', 190000)]:
            self.make('batches/' + name, size)
        clock = [0]
        partial = duplicates._hash_partial

        def slow_next_batch(path, size):
            if size == 190000:
                clock[0] = 100
            return partial(path, size)

        with patch('panxu.duplicates.time.monotonic', side_effect=lambda: clock[0]), \
                patch('panxu.duplicates._hash_partial', side_effect=slow_next_batch):
            result = duplicates.find_duplicates([os.path.join(self.tmp, 'batches')], min_size=1, max_hash_seconds=10)
        self.assertTrue(result['stats']['timed_out'])
        self.assertEqual([g['size'] for g in result['groups']], [200000])

    def test_time_limit_never_returns_a_partially_verified_duplicate(self):
        self.make('limited/a.bin', 200000)
        self.make('limited/b.bin', 200000)
        result = duplicates.find_duplicates([os.path.join(self.tmp, 'limited')], min_size=1, max_hash_seconds=0)
        self.assertTrue(result['stats']['timed_out'])
        self.assertTrue(result['stats']['truncated'])
        self.assertEqual(result['groups'], [])

    def test_incomplete_reference_search_never_claims_a_copy_is_unused(self):
        code = self.make('project/main.js', 20)
        duplicate = self.make('project/asset.bin', 10)
        f = {'path': duplicate, 'place': 'project', 'place_root': os.path.dirname(code)}
        stats = duplicates.check_references([{'files': [f]}], max_bytes=1)
        self.assertFalse(stats['complete'])
        self.assertFalse(f['refs']['complete'])
        self.assertEqual(duplicates.removal_score(f)[0], 30)
        with open(code, 'w', encoding='utf-8') as out:
            out.write('load("asset.bin")')
        stats = duplicates.check_references([{'files': [f]}], skip=lambda p: p == code)
        self.assertFalse(stats['complete'])
        self.assertFalse(f['refs']['complete'])

    def test_cross_disk_duplicates_and_overlapping_roots(self):
        a = self.make('C/Data/first.txt', 200)
        b = self.make('D/Archive/second.txt', 200)
        roots = [os.path.dirname(a), os.path.join(self.tmp, 'C'), os.path.join(self.tmp, 'D')]
        result = duplicates.find_duplicates(roots, min_size=1)
        self.assertEqual(result['stats']['files'], 2)
        self.assertEqual({f['path'] for f in result['groups'][0]['files']}, {a, b})

    def test_file_skip_applies_to_system_files_at_drive_root(self):
        a = self.make('C/pagefile.sys', 200)
        b = self.make('C/Data/copy.bin', 200)
        result = duplicates.find_duplicates([os.path.join(self.tmp, 'C')], min_size=1, skip=lambda p: p == a)
        self.assertEqual(result['groups'], [])
        self.assertEqual(result['stats']['files'], 1)

    def test_limits_are_explicit_and_never_report_partial_hash_as_duplicate(self):
        for n in ('one', 'two', 'three'):
            self.make('disk/' + n, 140000)
        root = os.path.join(self.tmp, 'disk')
        result = duplicates.find_duplicates([root], min_size=1, max_files=2)
        self.assertTrue(result['stats']['truncated'])
        self.assertEqual(result['stats']['files'], 2)
        result = duplicates.find_duplicates([root], min_size=1, max_hash_bytes=400000)
        self.assertTrue(result['stats']['truncated'])
        self.assertGreater(result['stats']['deferred_files'], 0)
        self.assertEqual(result['groups'], [])

    def test_subsecond_changes_invalidate_a_duplicate(self):
        a = self.make('a', 10)
        before = os.stat(a).st_mtime
        os.utime(a, (before + 0.5, before + 0.5))
        self.assertFalse(duplicates.verify_unchanged(a, 10, before))


class ScoringTest(TempDirTest):
    def test_rule_cost_activity_and_irreversible_actions(self):
        def score(rule, **kw):
            return checkup.rule_assessment({'id': rule, 'size': 100, **kw})
        self.assertGreater(score('user_temp')['score'], score('shader_cache')['score'])
        self.assertGreater(score('chrome', stale_bytes=90)['score'], score('chrome', recent_bytes=90)['score'])
        self.assertEqual(score('chrome', size=1000000)['score'], score('chrome')['score'])
        self.assertFalse(score('maven')['eligible'])
        self.assertLess(score('maven')['score'], 50)
        self.assertFalse(score('recycle_bin')['eligible'])

    def test_high_ai_confidence_does_not_override_incomplete_or_valuable_contents(self):
        env = self.posix_env()
        with patch('panxu.winapi.known_folder', return_value=''):
            policy = paths.ProtectionPolicy(env, allowed_roots=[self.tmp])
        for flag in (FLAG_CLOUD, FLAG_DENIED, FLAG_LINKS, FLAG_EXCLUDED):
            node = Node('oldcache', None)
            node.flags = flag
            node.mtime = time.time() - 400 * 86400
            builder = checkup.PlanBuilder(policy, threshold=0)
            suggestion = {'path': os.path.join(env['LOCALAPPDATA'], 'oldcache'), 'size': 100,
                          'source': 'ai', 'verdict': 'clean', 'confidence': 1, 'node_id': 0}
            scan = type('Scan', (), {'node': lambda _, n: node})()
            builder.add_ai_dirs([suggestion], env, scan=scan)
            self.assertFalse(builder.items[0]['selected'])
            self.assertLessEqual(builder.items[0]['score'], 30)

    def test_cache_word_does_not_mean_catalog_or_dialog(self):
        for name in ('catalog', 'dialog', 'templates', 'savelogic'):
            self.assertFalse(checkup.cache_like(name), name)
        for name in ('GPUCache', 'Code Cache', 'logs', '.cache', 'temp'):
            self.assertTrue(checkup.cache_like(name), name)

    def test_new_installer_and_resumable_download_score_lower(self):
        self.assertLess(insights.file_score('installer_installed', {'installed_version_comparison': 'newer'}), 50)
        self.assertLess(insights.file_score('installer_installed', {'age_days': 1}), 60)
        self.assertLess(insights.file_score('incomplete_download', {'age_days': 4}),
                        insights.file_score('incomplete_download', {'age_days': 60}))
        self.assertLess(insights.KIND_SCORE['empty_folders'], 50)

    def test_names_only_cannot_promote_archives_or_old_personal_files(self):
        for kind in ('stale_large', 'old_downloads', 'archive_extracted', 'name_versions'):
            question = insights.content_question(kind, {'remove': False})
            self.assertTrue(question)
            self.assertEqual(insights.with_ai(30, {'verdict': 'delete'}, bool(question)), 30)


class ZipEvidenceTest(TempDirTest):
    def archive(self, names):
        archive = os.path.join(self.tmp, 'pack.zip')
        with zipfile.ZipFile(archive, 'w') as z:
            for name, data in names.items():
                z.writestr(name, data)
        folder = os.path.join(self.tmp, 'pack')
        os.makedirs(folder, exist_ok=True)
        return archive, folder

    def test_same_size_different_content_is_not_verified(self):
        archive, folder = self.archive({'a.txt': b'abc'})
        self.make('pack/a.txt', 3)
        self.assertFalse(insights._verify_zip(archive, folder)[0])
        with open(os.path.join(folder, 'a.txt'), 'wb') as f:
            f.write(b'abc')
        self.assertTrue(insights._verify_zip(archive, folder)[0])

    def test_ninety_percent_extracted_is_not_complete(self):
        archive, folder = self.archive({f'{i}.txt': b'x' for i in range(10)})
        for i in range(9):
            self.make(f'pack/{i}.txt', 1)
        self.assertFalse(insights._verify_zip(archive, folder)[0])

    def test_path_traversal_and_large_entry_sets_are_never_verified(self):
        archive, folder = self.archive({'../outside': b'x'})
        self.make('outside', 1)
        self.assertFalse(insights._verify_zip(archive, folder)[0])
        archive, folder = self.archive({f'{i}': b'' for i in range(5001)})
        self.assertIsNone(insights._verify_zip(archive, folder)[0])


class JunkScopeTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.env = self.posix_env()
        self.folders = patch('panxu.winapi.known_folder', return_value='')
        self.folders.start()
        self.addCleanup(self.folders.stop)

    def test_exclusions_apply_when_scanning_and_are_rechecked_before_deletion(self):
        a = self.make('Users/alice/AppData/Local/Temp/old/a', 10, 3)
        b = self.make('Users/alice/AppData/Local/Temp/private/b', 20, 3)
        excluded = [os.path.dirname(b)]
        engine = junk.JunkEngine(self.env, admin=False, processes=set(), excluded=lambda: excluded)
        self.assertEqual(engine.scan(['user_temp'])[0]['size'], 10)
        excluded.append(a)  # setting changed after the scan
        self.assertEqual(engine.clean(['user_temp'])['freed'], 0)
        self.assertTrue(os.path.exists(a) and os.path.exists(b))

    def test_new_rules_only_clean_render_caches_and_obey_process_guard(self):
        a = self.make('Users/alice/AppData/Roaming/obsidian/Cache/data', 10, 30)
        b = self.make('Users/alice/AppData/Roaming/obsidian/IndexedDB/notes', 20)
        c = self.make('Users/alice/AppData/Roaming/obsidian/obsidian.json', 30)
        busy = junk.JunkEngine(self.env, admin=False, processes={'obsidian.exe'})
        self.assertEqual(busy.scan(['obsidian'])[0]['size'], 10)
        self.assertTrue(busy.clean(['obsidian'])['rules'][0]['skipped'])
        engine = junk.JunkEngine(self.env, admin=False, processes=set())
        self.assertEqual(engine.clean(['obsidian'])['freed'], 10)
        self.assertFalse(os.path.exists(a))
        self.assertTrue(os.path.exists(b) and os.path.exists(c))

    def test_excluded_component_store_never_offers_or_runs_a_command(self):
        keep = self.make('Windows/WinSxS/private/data', 10)
        engine = junk.JunkEngine(self.env, admin=True, processes=set(), excluded=[os.path.dirname(keep)])
        row = engine.scan(['component_store'])[0]
        self.assertTrue(row['scope_blocked'])
        self.assertFalse(row['available'])
        with patch.object(engine, '_run_command') as run:
            self.assertTrue(engine.clean(['component_store'])['rules'][0]['skipped'])
            run.assert_not_called()

    def test_rule_counts_old_and_recent_allocated_bytes(self):
        self.make('Users/alice/AppData/Local/pip/cache/old', 80, 100)
        self.make('Users/alice/AppData/Local/pip/cache/new', 20, 1)
        row = junk.JunkEngine(self.env, admin=False, processes=set()).scan(['pip'])[0]
        self.assertEqual((row['size'], row['stale_bytes'], row['recent_bytes']), (100, 80, 20))


if __name__ == '__main__':
    unittest.main()
