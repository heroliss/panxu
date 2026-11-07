import base64
import gzip
import hashlib
import json
import os
import time
from unittest.mock import patch

from helpers import TempDirTest
from panxu import app, handoff, sandbox
from panxu.api import Backend, Bridge
from panxu.scanner import SpaceScan
from panxu.settings import Settings


class HandoffTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.sb = sandbox.Sandbox(os.path.join(self.tmp, 'sb'))
        self.b = Backend(os.path.join(self.tmp, 'data'), sandbox=self.sb)
        self.addCleanup(sandbox.deactivate)
        self.addCleanup(self.b.jobs.stop)

    def wait(self, b, job_id):
        end = time.time() + 30
        while (s := b.jobs.status(job_id))['status'] == 'running' and time.time() < end:
            time.sleep(.02)
        self.assertEqual(s['status'], 'done', s)
        return s['result']

    def test_round_trip_preserves_scan_ids_removed_nodes_and_shared_results(self):
        plan = self.wait(self.b, self.b.m_checkup({'use_ai': False}))
        scan = self.b.scan
        victim = next(n for n in scan.nodes if n.parent and n.children)
        gone_path = scan.path_of(victim)
        scan.remove_path(gone_path, victim.size, victim.files, victim.logical_size)
        self.b.ai_results = {2: {'reason': 'kept'}}
        self.b.dup_ai = {'a': {'rank': 'keep'}}
        self.b.app_ai = {'a': {'reason': 'advice'}}
        self.b.restart_ui = handoff.ui_state({'rules': ['user_temp'], 'paths': ['x'], 'items': ['y']}, 'space')
        ticket = handoff.save(self.b)
        replacement = Backend(self.b.data_dir, sandbox=self.sb)
        with patch.object(SpaceScan, 'run', side_effect=AssertionError('no disk rescan')):
            handoff.consume(replacement, ticket)
        self.assertFalse(os.path.exists(handoff._file(self.b.data_dir, ticket)))
        self.assertEqual(replacement.plan, {**plan, 'resumed': True})
        self.assertEqual(replacement.ai_results, self.b.ai_results)
        self.assertEqual(replacement.app_ai, self.b.app_ai)
        self.assertEqual(replacement.dup_ai, self.b.dup_ai)
        self.assertEqual(replacement.resume['page'], 'space')
        self.assertEqual(replacement.resume['selection'], self.b.restart_ui['selection'])
        self.assertIsNone(replacement.scan.find(gone_path), 'removed children must not come back')
        self.assertEqual(len(replacement.scan.nodes), len(scan.nodes))
        for expected, actual in zip(scan.nodes, replacement.scan.nodes):
            self.assertEqual((actual.id, actual.size, replacement.scan.path_of(actual)),
                             (expected.id, expected.size, scan.path_of(expected)))
        if replacement.app_residues['items']:
            it = replacement.app_residues['items'][0]
            self.assertIs(it, next(i for i in replacement.plan['items'] if i['id'] == it['id']))
        self.assertEqual(replacement.jobs.running(), [])

    def test_invalid_checksum_expired_identity_version_and_oversize_are_rejected(self):
        for mode in ('checksum', 'created', 'identity', 'version', 'large'):
            ticket = handoff.save(self.b)
            file = handoff._file(self.b.data_dir, ticket)
            if mode in ('created', 'identity', 'version'):
                with gzip.open(file, 'rt', encoding='utf-8') as fh:
                    data = json.load(fh)
                data[mode] = {'created': 0, 'identity': ['other', None], 'version': 'old'}[mode]
                with gzip.open(file, 'wt', encoding='utf-8') as fh:
                    json.dump(data, fh)
                with open(file, 'rb') as fh:
                    ticket = ticket[:33] + hashlib.file_digest(fh, 'sha256').hexdigest()
            elif mode == 'checksum':
                with open(file, 'ab') as fh:
                    fh.write(b'changed')
            with patch.object(handoff, 'MAX_BYTES', 1 if mode == 'large' else 256 * 1024 ** 2):
                with self.assertRaises(ValueError, msg=mode):
                    handoff.consume(self.b, ticket)
            self.assertFalse(os.path.exists(file))
            self.assertIsNone(self.b.plan)
        with self.assertRaises(ValueError):
            handoff.consume(self.b, '../settings.json')

    def test_restart_is_blocked_while_any_scan_or_change_runs_and_decline_keeps_results(self):
        self.b.admin = False
        self.b.plan = {'id': 'keep'}
        launches = []
        self.b._relaunch = lambda: launches.append(True) or False
        bridge = Bridge(self.b)
        self.b.jobs.start('space_scan', lambda job: job.cancel.wait(5))
        self.assertFalse(bridge.call('restart_as_admin')['ok'])
        self.assertEqual(launches, [])
        self.b.jobs.stop()
        r = bridge.call('restart_as_admin', {'page': 'dups', 'selection': {'paths': ['copy']}})
        self.assertFalse(r['ok'])
        self.assertIn('没有获得管理员权限', r['error'])
        self.assertEqual(self.b.plan, {'id': 'keep'})
        self.assertEqual(self.b.restart_ui['selection']['paths'], ['copy'])

    def test_supplement_only_checks_privileged_rules_no_ai_or_disk_scan_and_keeps_choices(self):
        self.wait(self.b, self.b.m_checkup({'use_ai': False}))
        self.b.junk_rows[0]['needs_admin'] = True
        old = self.b.junk_rows[0]['id']
        self.b.resume = {'refresh_pending': True, 'selection': {'rules': []}}
        scan, dups, files = self.b.scan, self.b.dups, self.b.insights
        original = self.b.junk.scan
        with patch.object(self.b.junk, 'scan', wraps=original) as scan_rules, \
                patch.object(SpaceScan, 'run', side_effect=AssertionError('no rescan')), \
                patch.object(self.b, '_client', side_effect=AssertionError('no AI')):
            plan = self.wait(self.b, self.b.m_admin_refresh())
        self.assertIn(old, scan_rules.call_args.kwargs['rule_ids'])
        self.assertIs(self.b.scan, scan)
        self.assertIs(self.b.dups, dups)
        self.assertIs(self.b.insights, files)
        self.assertFalse(self.b.resume['refresh_pending'])
        self.assertFalse(any(i['selected'] for i in plan['items'] if i.get('rule') == old))

    def test_supplement_does_not_expand_disabled_app_scope(self):
        self.wait(self.b, self.b.m_checkup({'use_ai': False, 'apps_services': False}))
        self.b.resume = {'refresh_pending': True, 'selection': {'rules': []}}
        with patch.object(self.b.apps, 'list', side_effect=AssertionError('scope is off')):
            self.wait(self.b, self.b.m_admin_refresh())

    def test_supplement_preserves_handled_rule_even_when_it_is_now_empty(self):
        self.wait(self.b, self.b.m_checkup({'use_ai': False, 'apps_services': False}))
        item = next(i for i in self.b.plan['items'] if i['kind'] == 'rule')
        item.update(status='done', result='已清理', freed=12, selected=False)
        row = next(r for r in self.b.junk_rows if r['id'] == item['rule'])
        row['needs_admin'] = True
        self.b.resume = {'refresh_pending': True, 'selection': {'rules': []}}
        with patch.object(self.b.junk, 'scan', return_value=[{**row, 'size': 0, 'count': 0, 'available': False}]):
            plan = self.wait(self.b, self.b.m_admin_refresh())
        restored = next(i for i in plan['items'] if i['id'] == item['id'])
        self.assertEqual((restored['status'], restored['result'], restored['freed']), ('done', '已清理', 12))


class ElevationStartupTest(TempDirTest):
    def test_startup_decline_opens_normal_window_and_acceptance_exits_parent_cleanly(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        for accepted in (False, True):
            instance = MagicMock()
            instance.acquire.return_value = True
            webview = SimpleNamespace(start=MagicMock())
            with patch.dict('sys.modules', {'webview': webview}), \
                    patch.object(app, 'setup_logging'), \
                    patch.object(app.winapi, 'SingleInstance', return_value=instance), \
                    patch.object(app, 'auto_elevate', return_value=accepted), \
                    patch.object(app, 'Backend') as backend, patch.object(app, 'create_app_window'):
                self.assertEqual(app.main(['--data-dir', self.tmp]), 0)
                self.assertEqual(webview.start.called, not accepted)
                self.assertEqual(backend.called, not accepted)
                instance.release.assert_called_once()

    def test_default_opt_out_cancellation_sandbox_and_attempted_no_loop(self):
        with patch.object(app.winapi, 'IS_WINDOWS', True), patch.object(app.winapi, 'is_admin', return_value=False), \
                patch.object(app, 'launch_elevated', return_value=True) as launch:
            self.assertTrue(app.auto_elevate(self.tmp))
            self.assertEqual(launch.call_count, 1)
            self.assertFalse(app.auto_elevate(self.tmp, attempted=True))
            self.assertFalse(app.auto_elevate(self.tmp, sandbox=object()))
            self.assertEqual(launch.call_count, 1)
            Settings(self.tmp).update({'admin_on_start': False})
            self.assertFalse(app.auto_elevate(self.tmp))
            Settings(self.tmp).update({'admin_on_start': True})
            launch.return_value = False
            self.assertFalse(app.auto_elevate(self.tmp), 'decline opens the normal window; no retry')

    def test_launch_carries_original_data_folder_env_debug_and_ticket_for_source_and_exe(self):
        from types import SimpleNamespace
        env = {k: self.tmp for k in app.paths.USER_ENV_KEYS}
        for frozen in (False, True):
            with patch.object(app.sys, 'frozen', frozen, create=True), \
                    patch.object(app.paths, 'environment', return_value=env), \
                    patch.object(app.winapi, 'relaunch_as_admin', return_value=True) as launch:
                self.assertTrue(app.launch_elevated(self.tmp, SimpleNamespace(root=self.tmp), True, 'ticket'))
                exe, params, cwd = launch.call_args.args
                self.assertEqual(exe, app.sys.executable)
                import subprocess
                self.assertIn('--data-dir ' + subprocess.list2cmdline([os.path.abspath(self.tmp)]), params)
                self.assertIn('--resume ticket', params)
                self.assertIn('--debug', params)
                token = params.split('--user-env ', 1)[1].split(' ', 1)[0]
                self.assertEqual(json.loads(base64.b64decode(token)), env)
                self.assertEqual('run.py' in params, not frozen)
