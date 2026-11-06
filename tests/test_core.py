import json
import os
import unittest

from helpers import WIN_ENV, TempDirTest

from panxu import fsops, paths
from panxu.oplog import OperationLog
from panxu.settings import Settings


class PathPolicyTest(unittest.TestCase):
    def test_expand_and_unknown_vars(self):
        self.assertEqual(paths.expand('%LOCALAPPDATA%\\Temp', WIN_ENV), 'C:\\Users\\alice\\AppData\\Local\\Temp')
        self.assertIsNone(paths.expand('%NOPE%\\x', WIN_ENV))

    def test_is_within_is_case_insensitive_and_component_based(self):
        self.assertTrue(paths.is_within('c:\\users\\ALICE\\x', 'C:\\Users\\alice'))
        self.assertFalse(paths.is_within('C:\\Users\\alice2', 'C:\\Users\\alice'))
        self.assertTrue(paths.is_within('C:\\Users\\alice', 'C:\\Users\\alice\\'))

    def test_within_any_looks_at_the_parents(self):
        import time
        keys = {paths.key('C:\\Users\\alice\\Downloads\\old'), paths.key('D:\\Temp')}
        self.assertTrue(paths.within_any('c:\\users\\ALICE\\downloads\\old', keys))
        self.assertTrue(paths.within_any('C:\\Users\\alice\\Downloads\\old\\a\\b.zip', keys))
        self.assertFalse(paths.within_any('C:\\Users\\alice\\Downloads\\older', keys))
        self.assertFalse(paths.within_any('C:\\Users\\alice', keys))
        self.assertTrue(paths.within_any('D:\\Temp\\x', keys))
        # found while kill-testing: 20 000 files to delete took n x n steps before the first one went
        many = {paths.key(f'C:\\data\\f{i:05d}.tmp') for i in range(20000)}
        started = time.time()
        self.assertFalse(any(paths.within_any(f'C:\\other\\f{i:05d}.tmp', many) for i in range(20000)))
        self.assertLess(time.time() - started, 2)

    def test_anonymize_uses_the_most_specific_base(self):
        self.assertEqual(paths.anonymize('C:\\Users\\alice\\AppData\\Local\\JetBrains', WIN_ENV), '%LOCALAPPDATA%\\JetBrains')
        self.assertEqual(paths.anonymize('C:\\Users\\alice\\.gradle', WIN_ENV), '%USERPROFILE%\\.gradle')
        self.assertEqual(paths.anonymize('D:\\Games', WIN_ENV), 'D:\\Games')

    def test_protection_policy(self):
        policy = paths.ProtectionPolicy(WIN_ENV, excluded=['D:\\Keep'])
        blocked = ['C:\\', 'D:\\', 'C:\\Windows\\System32\\x.dll', 'C:\\pagefile.sys', 'C:\\Users', 'C:\\Users\\alice',
                   'C:\\Users\\alice\\AppData\\Local', 'C:\\Program Files', 'C:\\ProgramData\\Microsoft\\x',
                   'D:\\Keep\\a.iso', 'relative\\path', 'C:\\System Volume Information', '\\', '/', 'C:']
        for p in blocked:
            self.assertIsNotNone(policy.reason(p), p)
        allowed = ['C:\\Users\\alice\\Downloads\\a.iso', 'C:\\Users\\alice\\AppData\\Local\\Unity', 'D:\\Old\\x.zip',
                   'C:\\Program Files\\Leftover']
        for p in allowed:
            self.assertIsNone(policy.reason(p), p)
        self.assertIsNotNone(policy.caution('C:\\Program Files\\Leftover'))
        self.assertIsNotNone(policy.caution('C:\\Users\\alice\\OneDrive\\doc.txt'))
        self.assertIsNone(policy.caution('D:\\Old\\x.zip'))

    def test_selection_containing_protected_folders_is_refused(self):
        policy = paths.ProtectionPolicy(WIN_ENV, excluded=['D:\\Photos\\Keep'])
        refused = [
            'D:\\Photos',                                          # contains an exclusion
            'C:\\Users\\alice\\AppData\\Local\\Microsoft',          # contains WindowsApps, Credentials...
            'C:\\Users\\alice\\AppData\\Local\\Packages\\Microsoft.Windows.StartMenuExperienceHost_cw5n1h2txyewy',
            'C:\\Users\\alice\\AppData\\Roaming\\Microsoft\\Protect',
            'C:\\Users\\alice\\AppData\\Roaming\\Microsoft',        # contains Protect
            'C:\\Users\\bob', 'C:\\Users\\Default\\NTUSER.DAT',
            'D:\\Windows\\System32', 'E:\\$Recycle.Bin\\S-1-5', 'C:\\Windows.old\\Users',
            '\\\\?\\C:\\Windows\\System32', '\\\\.\\C:\\x', '\\\\localhost\\C$\\Windows',
            'C:\\Windows.\\System32',
        ]
        for p in refused:
            self.assertIsNotNone(policy.reason(p), p)
        self.assertIsNone(policy.reason('D:\\Photos\\2019'))
        self.assertIsNone(policy.reason('\\\\nas\\share\\old.iso'))
        self.assertIn('本地应用数据', policy.caution('C:\\Users\\alice\\AppData\\Local\\Unity'))
        self.assertIn('其他用户', policy.caution('C:\\Users\\bob\\Downloads\\x.zip'))

    def test_other_accounts_never_reach_the_model(self):
        from panxu.knowledge import Knowledge
        k = Knowledge(WIN_ENV)
        for p in ('C:\\Users\\bob', 'C:\\Users\\bob\\AppData\\Local\\Big', 'C:\\Users\\Public'):
            self.assertEqual(k.ai_role(p), 'skip', p)
        self.assertEqual(k.ai_role('C:\\Users\\alice'), 'descend', 'your own folder is still looked into')
        self.assertEqual(k.ai_role('C:\\Users\\alice\\AppData\\Local\\Unity'), 'ask')

    def test_categories(self):
        self.assertEqual(paths.category_of('movie.MKV'), 'video')
        self.assertEqual(paths.category_of('setup.exe'), 'installer')
        self.assertEqual(paths.category_of('noext'), 'other')


class FsOpsTest(TempDirTest):
    def test_delete_matching_respects_age_pattern_and_keeps_root(self):
        old = self.make('root/a/old.tmp', 100, age_days=3)
        new = self.make('root/a/new.tmp', 100)
        other = self.make('root/keep.txt', 100, age_days=3)
        root = os.path.join(self.tmp, 'root')
        stats = fsops.delete_matching(root, lambda n: n.endswith('.tmp'), min_age_seconds=86400)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(new))
        self.assertTrue(os.path.exists(other))
        self.assertTrue(os.path.isdir(root))
        self.assertEqual((stats.files, stats.bytes), (1, 100))

    def test_links_are_never_followed(self):
        outside = self.make('outside/precious.txt', 50)
        tree = os.path.join(self.tmp, 'tree')
        os.makedirs(tree)
        os.symlink(os.path.dirname(outside), os.path.join(tree, 'link'))
        self.make('tree/sub/junk.bin', 10)
        stats = fsops.delete_path(tree)
        self.assertTrue(os.path.exists(outside), 'link target must survive')
        self.assertFalse(os.path.exists(tree))
        self.assertEqual(stats.failed, 0)
        # matching delete skips links entirely
        os.makedirs(tree)
        os.symlink(os.path.dirname(outside), os.path.join(tree, 'link'))
        fsops.delete_matching(tree, lambda n: True)
        self.assertTrue(os.path.exists(outside))

    def test_delete_selected_symlink_removes_only_the_link(self):
        outside = self.make('outside/precious.txt', 50)
        link = os.path.join(self.tmp, 'link')
        os.symlink(os.path.dirname(outside), link)
        fsops.delete_path(link)
        self.assertFalse(os.path.lexists(link))
        self.assertTrue(os.path.exists(outside))

    def test_path_size(self):
        self.make('d/a', 10)
        self.make('d/b/c', 20)
        self.assertEqual(fsops.path_size(os.path.join(self.tmp, 'd')), (30, 2))

    @unittest.skipIf(os.name == 'nt', 'would use the real Recycle Bin')
    def test_recycle_reports_failure_off_windows(self):
        f = self.make('x.bin', 10)
        stats, done = fsops.recycle([f])
        self.assertEqual(done, [])
        self.assertEqual(stats.failed, 1)
        self.assertTrue(os.path.exists(f))


class JobStopTest(unittest.TestCase):
    def test_closing_stops_changes_after_the_current_step(self):
        import time
        from panxu.jobs import JobManager
        jobs = JobManager()
        steps, recorded = [], []

        def cleanup(job):
            try:
                while not job.cancel.is_set():
                    steps.append(1)
                    time.sleep(0.01)
            finally:
                recorded.append(len(steps))  # what a real cleanup does: write its record on the way out

        jobs.start('delete', cleanup, group='modify')
        jobs.start('space_scan', lambda job: job.cancel.wait(5), group='space')
        time.sleep(0.1)
        self.assertEqual(len(jobs.running('modify')), 1)
        self.assertTrue(jobs.stop('modify', timeout=3))
        self.assertEqual(recorded, [len(steps)], 'it stopped and wrote its record')
        self.assertEqual(len(jobs.running('space')), 1, 'only the changes are stopped')
        jobs.stop()


class CloseWindowTest(unittest.TestCase):
    def test_closing_during_a_cleanup_asks_and_stops_it_cleanly(self):
        from panxu.app import confirm_close

        class FakeBackend:
            def __init__(self, busy):
                self.busy, self.stopped = busy, False

            def changes_running(self):
                return self.busy

            def stop_changes(self, timeout):
                self.stopped = True
                return True
        asked = []
        idle = FakeBackend([])
        self.assertTrue(confirm_close(idle, lambda text: asked.append(text)))
        self.assertEqual(asked, [], 'nothing running: closes without a question')
        busy = FakeBackend([{'progress': {'done': 12}}])
        self.assertFalse(confirm_close(busy, lambda text: asked.append(text) or False))
        self.assertIn('已处理 12 项', asked[-1])
        self.assertFalse(busy.stopped, 'the user kept it open: the cleanup goes on')
        self.assertTrue(confirm_close(busy, lambda text: True))
        self.assertTrue(busy.stopped, 'stopped cleanly before the window closes')


class OpLogTest(TempDirTest):
    def test_a_line_cut_off_by_a_crash_does_not_take_the_next_one_along(self):
        path = os.path.join(self.tmp, 'h.jsonl')
        log = OperationLog(path)
        log.add('junk', '第一次')
        with open(path, 'ab') as fh:  # killed while writing: half a line, cut inside a Chinese character
            fh.write('{"id": "x", "title": "移到回收站'.encode('utf-8')[:-1])
        again = OperationLog(path)  # the next run
        again.add('recycle', '第二次')
        self.assertEqual([r['title'] for r in again.entries()], ['第二次', '第一次'])

    def test_a_change_cut_off_half_way_stays_on_record(self):
        path = os.path.join(self.tmp, 'h.jsonl')
        log = OperationLog(path)
        log.PROGRESS_EVERY = 0
        begun = log.begin('delete', '永久删除 300 项', items=['C:\\a', 'C:\\b'])
        log.progress(begun, freed=4096, done=120)
        self.assertEqual(log.entries()[0]['state'], 'running', 'still going in this run')
        after_restart = OperationLog(path).entries()[0]  # PanXu was killed; the next run reads the log
        self.assertEqual((after_restart['state'], after_restart['freed'], after_restart['done_count']), ('interrupted', 4096, 120))
        self.assertEqual(after_restart['items'], ['C:\\a', 'C:\\b'])
        log.add('delete', '永久删除 300 项', freed=9000, replaces=begun)  # finished normally instead
        rows = log.entries()
        self.assertEqual(len(rows), 1)
        self.assertNotIn('state', rows[0])
        dropped = log.begin('delete', '永久删除 1 项')
        log.drop(dropped)  # stopped before the first file
        self.assertEqual(len(log.entries()), 1)

    def test_killed_while_writing(self):
        import subprocess
        import sys
        import time
        path = os.path.join(self.tmp, 'h.jsonl')
        script = ('import sys; sys.path.insert(0, sys.argv[2]); from panxu.oplog import OperationLog\n'
                  'log = OperationLog(sys.argv[1]); log.PROGRESS_EVERY = 0; i = 0\n'
                  'while True:\n'
                  '    b = log.begin("delete", "永久删除 " + "很长的标题" * 50, items=["C:\\\\文件%d" % k for k in range(200)])\n'
                  '    log.progress(b, freed=i, done=i); log.add("delete", "完成", replaces=b); i += 1\n')
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        proc = subprocess.Popen([sys.executable, '-c', script, path, root])
        try:
            end = time.time() + 10
            while time.time() < end and (not os.path.exists(path) or os.path.getsize(path) < 200_000):
                time.sleep(0.02)
        finally:
            proc.kill()  # as Task Manager would
            proc.wait()
        log = OperationLog(path)
        rows = log.entries(limit=100000)  # whatever the last line looks like, reading works
        self.assertTrue(rows)
        log.add('junk', '重新打开后的第一条')
        self.assertEqual(log.entries()[0]['title'], '重新打开后的第一条')

    def test_add_mark_and_read_newest_first(self):
        log = OperationLog(os.path.join(self.tmp, 'h.jsonl'))
        a = log.add('junk', 'first', freed=5)
        b = log.add('startup', 'second', revert={'action': 'startup'})
        log.mark(b['id'], reverted=True)
        rows = log.entries()
        self.assertEqual([r['title'] for r in rows], ['second', 'first'])
        self.assertTrue(rows[0]['followup']['reverted'])
        self.assertEqual(log.get(a['id'])['freed'], 5)

    def test_corrupt_lines_are_ignored(self):
        path = os.path.join(self.tmp, 'h.jsonl')
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write('not json\n' + json.dumps({'id': 'x', 'time': 1, 'kind': 'ai', 'title': 't'}) + '\n')
        self.assertEqual(len(OperationLog(path).entries()), 1)


class SettingsTest(TempDirTest):
    def test_home_checkup_choices_default_to_full_scope_and_persist(self):
        s = Settings(self.tmp)
        self.assertTrue(s.get()['checkup']['other_drives'])
        choices = {'other_drives': False, 'selected_drives': [], 'duplicates': False,
                   'cross_drive_duplicates': False, 'use_ai': False}
        s.update({'checkup': choices})
        s.update({'checkup': {'ai_scopes': {'files': False, 'startup': False}}})
        saved = Settings(self.tmp).get()['checkup']
        self.assertTrue(saved['space_scan'])
        for key, value in choices.items():
            self.assertEqual(saved[key], value)
        self.assertEqual(saved['ai_scopes'], {'space': True, 'files': False, 'duplicates': True, 'startup': False})
        s.update({'checkup': {'selected_drives': ['D:\\']}})
        self.assertEqual(Settings(self.tmp).get()['checkup']['selected_drives'], ['D:\\'])

    def test_merge_ignores_unknown_keys_and_persists(self):
        s = Settings(self.tmp)
        s.update({'ai': {'model': 'm1'}, 'bogus': 1})
        again = Settings(self.tmp).get()
        self.assertEqual(again['ai']['model'], 'm1')
        self.assertNotIn('bogus', again)
        self.assertEqual(again['ai']['preset'], 'deepseek')

    def test_thinking_defaults_to_low_once(self):
        with open(os.path.join(self.tmp, 'settings.json'), 'w', encoding='utf-8') as fh:
            json.dump({'ai': {'thinking': 'off'}}, fh)  # written by an earlier build, where off was the default
        s = Settings(self.tmp)
        self.assertEqual(s.get()['ai']['thinking'], 'low')
        s.update({'ai': {'thinking': 'off'}})  # now a real choice: kept
        self.assertEqual(Settings(self.tmp).get()['ai']['thinking'], 'off')

    def test_old_default_min_folder_size_is_lifted_once(self):
        # the default of the build that wrote the file is lifted; a value the user picked there is kept
        for version, old, now in ((2, 500, 0), (3, 200, 0), (3, 500, 500), (2, 200, 200), (3, 2048, 2048)):
            with open(os.path.join(self.tmp, 'settings.json'), 'w', encoding='utf-8') as fh:
                json.dump({'version': version, 'ai': {'min_dir_mb': old, 'thinking': 'off'}}, fh)
            s = Settings(self.tmp)
            self.assertEqual(s.get()['ai']['min_dir_mb'], now, (version, old))
            self.assertEqual(s.get()['ai']['thinking'], 'off', 'chosen after version 2: kept')
        s.update({'ai': {'min_dir_mb': 200}})
        self.assertEqual(Settings(self.tmp).get()['ai']['min_dir_mb'], 200, 'a later choice is kept')
        with open(os.path.join(self.tmp, 'settings.json'), 'w', encoding='utf-8') as fh:
            json.dump({'version': 3, 'ai': {'min_dir_mb': 1024}}, fh)
        self.assertEqual(Settings(self.tmp).get()['ai']['min_dir_mb'], 1024)

    def test_old_auto_select_becomes_a_threshold(self):
        with open(os.path.join(self.tmp, 'settings.json'), 'w', encoding='utf-8') as fh:
            json.dump({'checkup': {'auto_select': 'careful'}}, fh)
        self.assertEqual(Settings(self.tmp).get()['checkup']['auto_threshold'], 8)
        self.assertEqual(Settings(os.path.join(self.tmp, 'fresh')).get()['checkup']['auto_threshold'], 5)

    def test_retired_models_are_renamed(self):
        with open(os.path.join(self.tmp, 'settings.json'), 'w', encoding='utf-8') as fh:
            json.dump({'ai': {'preset': 'deepseek', 'model': 'deepseek-chat'}}, fh)
        self.assertEqual(Settings(self.tmp).get()['ai']['model'], 'deepseek-flash')

    @unittest.skipUnless(os.name == 'nt', 'the 0.x key is protected with DPAPI')
    def test_import_legacy_key(self):
        import base64
        from panxu import winapi
        blob, _ = winapi.protect_secret(b'sk-legacy')
        old = os.path.join(self.tmp, 'old', 'model_settings.json')
        os.makedirs(os.path.dirname(old))
        with open(old, 'w', encoding='utf-8') as fh:
            json.dump({'provider': 'deepseek', 'model': 'deepseek-reasoner', 'base_url': 'https://api.deepseek.com',
                       'key_dpapi': base64.b64encode(blob).decode('ascii')}, fh)
        s = Settings(os.path.join(self.tmp, 'new'))
        self.assertEqual(s.import_legacy_key([os.path.join(self.tmp, 'missing.json'), old]), 'deepseek')
        self.assertEqual(s.get_secret('ai_key:deepseek'), 'sk-legacy')
        self.assertEqual(s.get()['ai']['model'], 'deepseek-flash')
        self.assertIsNone(s.import_legacy_key([old]), 'only when no key is configured yet')

    def test_secret_roundtrip(self):
        s = Settings(self.tmp)
        s.set_secret('ai_key:deepseek', 'sk-123')
        self.assertEqual(Settings(self.tmp).get_secret('ai_key:deepseek'), 'sk-123')
        s.set_secret('ai_key:deepseek', '')
        self.assertFalse(s.has_secret('ai_key:deepseek'))


class CrashReportTest(TempDirTest):
    def test_an_error_is_logged_and_shown_without_a_console(self):
        import io
        import logging
        from contextlib import redirect_stderr
        from unittest import mock
        from panxu import app
        root = logging.getLogger()
        before = list(root.handlers)
        shown = []
        try:
            with mock.patch.object(app.winapi, 'message_box', lambda text, title: shown.append(text)), \
                    redirect_stderr(io.StringIO()):
                try:
                    raise RuntimeError('WebView2 不可用')
                except RuntimeError:
                    app.report_crash(self.tmp)
        finally:
            for handler in [h for h in root.handlers if h not in before]:
                root.removeHandler(handler)
                handler.close()
        self.assertEqual(len(shown), 1)
        self.assertIn('WebView2 不可用', shown[0])
        log_path = os.path.join(self.tmp, 'panxu.log')
        self.assertIn(log_path, shown[0], 'says where the details are')
        with open(log_path, encoding='utf-8') as fh:
            self.assertIn('RuntimeError: WebView2 不可用', fh.read())


if __name__ == '__main__':
    unittest.main()
