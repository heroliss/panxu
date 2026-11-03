import os
import unittest

from helpers import WIN_ENV, TempDirTest

from panxu import junk
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
                self.assertTrue(r.targets, r.id)

    def test_runtime_guard_rejects_misconfigured_roots(self):
        env = dict(WIN_ENV)
        self.assertIsNotNone(junk.unsafe_root_reason('C:\\', env))
        self.assertIsNotNone(junk.unsafe_root_reason('C:\\Windows', env))
        self.assertIsNone(junk.unsafe_root_reason('C:\\Windows.old', env))
        self.assertIsNotNone(junk.unsafe_root_reason('C:\\Users', env))          # contains USERPROFILE
        self.assertIsNotNone(junk.unsafe_root_reason('C:\\Users\\alice', env))
        self.assertIsNone(junk.unsafe_root_reason('C:\\Users\\alice\\AppData\\Local\\Temp', env))
        self.assertIsNone(junk.unsafe_root_reason('C:\\Windows\\Temp', env))


class EngineTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.env = self.posix_env()
        self.log = OperationLog(os.path.join(self.tmp, 'data', 'h.jsonl'))

    def engine(self, admin=False, processes=()):
        return junk.JunkEngine(self.env, admin=admin, processes=set(processes), oplog=self.log)

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
