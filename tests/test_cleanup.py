import os
import unittest
from unittest.mock import patch

from helpers import WIN_ENV, TempDirTest

from panxu import junk, winapi
from panxu.oplog import OperationLog


class CatalogueTest(unittest.TestCase):
    def test_every_target_is_deep_inside_a_known_base(self):
        self.assertEqual(junk.validate_catalogue(WIN_ENV), [])

    def test_ids_unique_and_groups_known(self):
        ids = [r.id for r in junk.RULES]
        self.assertEqual(len(ids), len(set(ids)))
        groups = dict(junk.GROUPS)
        for r in junk.RULES:
            self.assertIn(r.group, groups)
            self.assertIn(r.mode, ('files', 'recycle_bin', 'cleanmgr', 'command'))
            if r.mode == 'files':
                self.assertTrue(r.targets or r.finder in junk.FINDERS, r.id)

    def test_runtime_guard_rejects_misconfigured_roots(self):
        env = dict(WIN_ENV)
        self.assertIsNotNone(junk.unsafe_root_reason('C:\\', env))
        self.assertIsNotNone(junk.unsafe_root_reason('C:\\Windows', env))
        self.assertIsNone(junk.unsafe_root_reason('C:\\Windows.old', env))
        self.assertIsNotNone(junk.unsafe_root_reason('C:\\Users', env))          # contains USERPROFILE
        self.assertIsNotNone(junk.unsafe_root_reason('C:\\Users\\alice', env))
        self.assertIsNone(junk.unsafe_root_reason('C:\\Users\\alice\\AppData\\Local\\Temp', env))
        self.assertIsNone(junk.unsafe_root_reason('C:\\Windows\\Temp', env))


class ChromiumCacheFinderTest(TempDirTest):
    """Caches of programs with a Chromium inside are found by their structure -- a Chromium profile AND the
    cache's own layout -- never by a folder's name."""

    R = 'Users/alice/AppData/Roaming/'
    L = 'Users/alice/AppData/Local/'

    def setUp(self):
        super().setUp()
        self.env = self.posix_env()
        self.open = {}   # file -> program that has it open (the stand-in for Windows' Restart Manager)
        patcher = patch.object(winapi, 'programs_using',
                               lambda files: sorted({self.open[f] for f in files if f in self.open}))
        patcher.start()
        self.addCleanup(patcher.stop)

    def engine(self, processes=()):
        return junk.JunkEngine(self.env, admin=False, processes=set(processes))

    def profile(self, base, cache_mb=3):
        """An Electron-style profile: what must stay, and its caches."""
        keep = [self.make(base + rel, 10) for rel in (
            'Local State', 'Preferences', 'Network/Cookies', 'Local Storage/leveldb/000003.log',
            'IndexedDB/app.leveldb/000005.ldb', 'Service Worker/CacheStorage/abc/index', 'config.json',
            'Cache/notes-of-the-app.txt', 'Code Cache/other/keep.bin', 'GPUCache/not-a-cache-file.txt')]
        cache = [self.make(base + rel, size) for rel, size in (
            ('Cache/Cache_Data/index', 100), ('Cache/Cache_Data/data_0', 100), ('Cache/Cache_Data/f_000001', cache_mb * 1000),
            ('Code Cache/js/index', 100), ('Code Cache/js/9f2c_0', 500), ('Code Cache/wasm/index-dir/the-real-index', 50),
            ('GPUCache/index', 100), ('GPUCache/data_0', 100), ('GPUCache/data_1', 100), ('GPUCache/f_00000a', 200))]
        return keep, cache

    def found(self):
        return sorted(os.path.relpath(root, self.env['USERPROFILE']).replace(os.sep, '/')
                      for _, root, _ in junk.rule_entries(junk.RULES_BY_ID['app_web_cache'], self.env))

    def test_only_cache_layouts_inside_a_profile_are_found(self):
        self.profile(self.R + 'NoteFlow/')
        self.profile(self.L + 'DeskWidget/EBWebView/Default/')
        self.make(self.L + 'DeskWidget/EBWebView/Local State', 5)
        for name in ('index', 'data_0', 'data_1'):
            self.make(self.L + f'DeskWidget/EBWebView/GrShaderCache/{name}', 10)
        # named Cache, but no Chromium profile around it
        self.make(self.R + 'PhotoTool/Cache/projects.db', 999)
        self.make(self.R + 'PhotoTool/Cache/Cache_Data/index', 10)
        self.make(self.R + 'PhotoTool/settings.ini', 5)
        # a profile, but its Cache folder does not have the cache's layout
        self.make(self.R + 'Odd/Local State', 5)
        self.make(self.R + 'Odd/Network/Cookies', 5)
        self.make(self.R + 'Odd/Cache/my-important-export.zip', 999)
        self.make(self.R + 'Odd/GPUCache/readme.txt', 5)
        # protected or covered elsewhere: never entered
        self.profile(self.L + 'Packages/SomeApp_8wekyb3d8bbwe/LocalCache/EBWebView/Default/')
        self.profile(self.L + 'Programs/Tool/data/')
        self.assertEqual(self.found(), [
            'AppData/Local/DeskWidget/EBWebView/Default/Cache/Cache_Data',
            'AppData/Local/DeskWidget/EBWebView/Default/Code Cache/js',
            'AppData/Local/DeskWidget/EBWebView/Default/Code Cache/wasm',
            'AppData/Local/DeskWidget/EBWebView/Default/GPUCache',
            'AppData/Local/DeskWidget/EBWebView/GrShaderCache',
            'AppData/Roaming/NoteFlow/Cache/Cache_Data',
            'AppData/Roaming/NoteFlow/Code Cache/js',
            'AppData/Roaming/NoteFlow/Code Cache/wasm',
            'AppData/Roaming/NoteFlow/GPUCache'])

    def test_what_a_fixed_rule_covers_is_not_found_again(self):
        self.profile(self.R + 'Code/')            # the vscode rule: Cache, Code Cache, GPUCache
        self.profile(self.L + 'Google/Chrome/User Data/Default/')
        self.make(self.L + 'Google/Chrome/User Data/Local State', 5)
        self.assertEqual(self.found(), [])

    def test_cleaning_takes_the_caches_and_nothing_else(self):
        keep, cache = self.profile(self.R + 'NoteFlow/')
        eng = self.engine()
        row = eng.scan(['app_web_cache'])[0]
        self.assertEqual((row['size'], row['count']), (sum(os.path.getsize(p) for p in cache), len(cache)))
        self.assertEqual(row['apps'], [{'name': 'NoteFlow', 'size': row['size'], 'count': len(cache)}])
        result = eng.clean(['app_web_cache'])
        self.assertEqual(result['freed'], row['size'])
        self.assertEqual(result['rules'][0]['held'], [])
        self.assertFalse(any(os.path.exists(p) for p in cache))
        self.assertTrue(all(os.path.exists(p) for p in keep))

    def test_a_running_app_keeps_all_of_its_caches(self):
        _, running = self.profile(self.R + 'NoteFlow/')
        _, idle = self.profile(self.R + 'MusicBox/')
        _, named = self.profile(self.R + 'Vendor Player/')
        # Windows says a program has NoteFlow's cache index open; only one of its three caches is touched now
        self.open[os.path.join(self.env['APPDATA'], 'NoteFlow', 'GPUCache', 'index')] = 'NoteFlow Desktop'
        result = self.engine(processes={'vendorplayer.exe', 'explorer.exe'}).clean(['app_web_cache'])
        row = result['rules'][0]
        self.assertEqual(row['held'], [{'app': 'NoteFlow', 'by': ['NoteFlow Desktop']},
                                       {'app': 'Vendor Player', 'by': ['vendorplayer.exe']}])
        self.assertTrue(all(os.path.exists(p) for p in running + named), 'not one file of a running app goes')
        self.assertFalse(any(os.path.exists(p) for p in idle))
        self.assertEqual(result['freed'], 4350)   # MusicBox's ten cache files

    def test_found_locations_are_real_folders_deep_inside_app_data(self):
        self.profile(self.R + 'NoteFlow/')
        outside = self.make('Elsewhere/App/Cache/Cache_Data/index', 10)
        with patch.dict(junk.FINDERS, {'chromium': lambda env: [
                (junk.Target(''), os.path.dirname(outside), 'App'),                       # not in app data
                (junk.Target(''), self.env['APPDATA'], 'whole'),                          # a base folder itself
                (junk.Target(''), os.path.join(self.env['APPDATA'], 'NoteFlow'), 'top')]}):  # only one level in
            self.assertEqual(junk.rule_entries(junk.RULES_BY_ID['app_web_cache'], self.env), [])


class EngineTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.env = self.posix_env()
        self.log = OperationLog(os.path.join(self.tmp, 'data', 'h.jsonl'))

    def engine(self, admin=False, processes=()):
        return junk.JunkEngine(self.env, admin=admin, processes=set(processes), oplog=self.log)

    def test_new_teams_cache_preserves_account_data_and_obeys_running_guard(self):
        base = 'Users/alice/AppData/Local/Packages/MSTeams_8wekyb3d8bbwe/LocalCache/Microsoft/MSTeams/EBWebView/Default/'
        cache = self.make(base + 'Cache/Cache_Data/data_0', 80)
        keep = [self.make(base + 'Cookies', 10), self.make(base + 'Local Storage/db', 20)]
        self.assertTrue(self.engine(processes={'ms-teams.exe'}).clean(['teams_new'])['rules'][0]['skipped'])
        self.assertTrue(os.path.exists(cache))
        self.assertEqual(self.engine().clean(['teams_new'])['freed'], 80)
        self.assertTrue(all(os.path.exists(p) for p in keep))

    def test_dism_rule_only_removes_old_rotated_log(self):
        old = self.make('Windows/Logs/DISM/dism.log.bak', 20, age_days=30)
        keep = [self.make('Windows/Logs/DISM/dism.log', 30, age_days=30),
                self.make('Windows/Logs/DISM/custom.log.bak', 40, age_days=30)]
        self.assertTrue(self.engine(admin=True, processes={'dism.exe'}).clean(['dism_logs'])['rules'][0]['skipped'])
        self.assertEqual(self.engine(admin=True).clean(['dism_logs'])['freed'], 20)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(all(os.path.exists(p) for p in keep))
        fresh = self.make('Windows/Logs/DISM/dism.log.bak', 20)
        self.assertEqual(self.engine(admin=True).clean(['dism_logs'])['freed'], 0)
        self.assertTrue(os.path.exists(fresh))

    def test_scan_and_clean_user_temp(self):
        old = self.make('Users/alice/AppData/Local/Temp/old/a.tmp', 1000, age_days=2)
        fresh = self.make('Users/alice/AppData/Local/Temp/fresh.tmp', 500)
        eng = self.engine()
        row = next(r for r in eng.scan(['user_temp']) if r['id'] == 'user_temp')
        self.assertEqual((row['size'], row['count']), (1000, 1))
        result = eng.clean(['user_temp'])
        self.assertEqual(result['freed'], 1000)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(fresh))
        self.assertTrue(os.path.isdir(self.env['TEMP']))
        self.assertEqual(self.log.entries()[0]['kind'], 'junk')

    def test_wildcard_profiles(self):
        a = self.make('Users/alice/AppData/Local/Google/Chrome/User Data/Default/Cache/Cache_Data/f1', 300)
        b = self.make('Users/alice/AppData/Local/Google/Chrome/User Data/Profile 1/Code Cache/js/f2', 200)
        keep = self.make('Users/alice/AppData/Local/Google/Chrome/User Data/Default/Bookmarks', 50)
        eng = self.engine()
        self.assertEqual(eng.scan(['chrome'])[0]['size'], 500)
        eng.clean(['chrome'])
        self.assertFalse(os.path.exists(a) or os.path.exists(b))
        self.assertTrue(os.path.exists(keep))

    def test_running_app_blocks_its_rule(self):
        cache = self.make('Users/alice/AppData/Local/Google/Chrome/User Data/Default/Cache/f', 300)
        eng = self.engine(processes={'chrome.exe'})
        self.assertEqual(eng.scan(['chrome'])[0]['running'], ['chrome.exe'])
        result = eng.clean(['chrome'])
        self.assertIn('关闭', result['rules'][0]['skipped'])
        self.assertTrue(os.path.exists(cache))

    def test_running_now_updates_the_last_scan(self):
        self.make('Users/alice/AppData/Local/Google/Chrome/User Data/Default/Cache/f', 300)
        eng = self.engine(processes={'chrome.exe'})
        eng.scan(['chrome'])
        self.assertEqual(eng.last_scan['chrome'].running, ['chrome.exe'])
        eng._processes = set()  # closed
        self.assertEqual(eng.running_now()['chrome'], [])
        self.assertEqual(eng.last_scan['chrome'].running, [])
        self.assertNotIn('user_temp', eng.running_now(), 'only rules that wait for programs')

    def test_admin_rules_skipped_without_elevation(self):
        f = self.make('Windows/Temp/x.tmp', 300, age_days=3)
        result = self.engine(admin=False).clean(['windows_temp'])
        self.assertEqual(result['rules'][0]['skipped'], '需要管理员权限')
        self.assertTrue(os.path.exists(f))
        result = self.engine(admin=True).clean(['windows_temp'])
        self.assertFalse(os.path.exists(f))

    def test_misconfigured_temp_is_never_cleaned(self):
        precious = self.make('Users/alice/Documents/thesis.docx', 100, age_days=30)
        env = dict(self.env)
        env['TEMP'] = env['USERPROFILE']  # TEMP pointing at the profile must not wipe it
        eng = junk.JunkEngine(env, admin=False, processes=set())
        eng.clean(['user_temp'])
        self.assertTrue(os.path.exists(precious))

    def test_temp_must_look_like_a_temp_folder(self):
        data = self.make('Data/report.xlsx', 100, age_days=30)
        env = dict(self.env, TEMP=os.path.join(self.tmp, 'Data'))
        junk.JunkEngine(env, admin=False, processes=set()).clean(['user_temp'])
        self.assertTrue(os.path.exists(data))
        ok = self.make('Scratch/tmp/a.bin', 100, age_days=30)
        env = dict(self.env, TEMP=os.path.join(self.tmp, 'Scratch', 'tmp'))
        junk.JunkEngine(env, admin=False, processes=set()).clean(['user_temp'])
        self.assertFalse(os.path.exists(ok))

    def test_inetcache_keeps_office_and_outlook_copies(self):
        base = 'Users/alice/AppData/Local/Microsoft/Windows/INetCache'
        outlook = self.make(f'{base}/Content.Outlook/ABC/attachment.docx', 100, age_days=5)
        word = self.make(f'{base}/Content.Word/~WRS0001.tmp', 100, age_days=5)
        ie = self.make(f'{base}/IE/X1/page.htm', 100, age_days=5)
        fresh = self.make(f'{base}/IE/X1/fresh.htm', 100)
        self.engine().clean(['inetcache'])
        self.assertTrue(os.path.exists(outlook) and os.path.exists(word) and os.path.exists(fresh))
        self.assertFalse(os.path.exists(ie))

    def test_cancel_still_logs_partial_work(self):
        import threading
        from panxu.fsops import Cancelled
        for i in range(5):
            self.make(f'Users/alice/AppData/Local/Temp/f{i}.tmp', 10, age_days=3)
        cancel = threading.Event()
        with self.assertRaises(Cancelled):
            self.engine().clean(['user_temp', 'wer'], cancel=cancel, progress=lambda *a: cancel.set())
        entry = self.log.entries()[0]
        self.assertIn('已停止', entry['title'])
        self.assertTrue(entry['details']['cancelled'])

    def test_pattern_and_non_recursive_targets(self):
        thumb = self.make('Users/alice/AppData/Local/Microsoft/Windows/Explorer/thumbcache_256.db', 100)
        other = self.make('Users/alice/AppData/Local/Microsoft/Windows/Explorer/other.db', 100)
        nested = self.make('Users/alice/AppData/Local/Microsoft/Windows/Explorer/sub/thumbcache_1.db', 100)
        self.engine().clean(['thumbcache'])
        self.assertFalse(os.path.exists(thumb))
        self.assertTrue(os.path.exists(other))
        self.assertTrue(os.path.exists(nested))


if __name__ == '__main__':
    unittest.main()
