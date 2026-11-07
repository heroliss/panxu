import os
import time
from types import SimpleNamespace
from unittest import mock

from helpers import TempDirTest
from panxu import paths, winapi
from panxu.api import Backend, UserError
from panxu.jobs import JobManager


class PersonalScopeApiTest(TempDirTest):
    def backend(self):
        env = self.posix_env()
        c = os.path.join(self.tmp, 'C')
        d = os.path.join(self.tmp, 'D')
        home = os.path.join(c, 'Users', 'alice')
        env.update(SystemDrive=c, SystemRoot=os.path.join(c, 'Windows'), USERPROFILE=home,
                   PUBLIC=os.path.join(c, 'Users', 'Public'), ProgramFiles=os.path.join(c, 'Program Files'),
                   ProgramData=os.path.join(c, 'ProgramData'),
                   LOCALAPPDATA=os.path.join(home, 'AppData', 'Local'),
                   APPDATA=os.path.join(home, 'AppData', 'Roaming'))
        for root in (c, d):
            os.makedirs(root, exist_ok=True)
        b = Backend.__new__(Backend)
        b.env, b.apps, b.insights, b.file_ai = env, None, None, {}
        b.settings = SimpleNamespace(get=lambda: {})
        b.jobs = JobManager()
        b.m_drives = lambda: [{'root': c, 'fixed': True, 'system': True},
                             {'root': d, 'fixed': True, 'system': False}]
        b.policy = lambda: paths.ProtectionPolicy(env, allowed_roots=[self.tmp], drive_roots=[c, d])
        return b, home, c, d

    def result(self, backend, job_id):
        end = time.monotonic() + 10
        while time.monotonic() < end:
            state = backend.jobs.status(job_id)
            if state['status'] != 'running':
                self.assertEqual(state['status'], 'done', state)
                return state['result']
            time.sleep(0.01)
        self.fail('personal scan did not finish')

    def test_default_scan_reaches_custom_system_folder_and_second_drive(self):
        b, home, c, d = self.backend()
        for rel in ('C/Users/alice/Documents/home.part', 'C/custom/system.part', 'D/data/other.part',
                    'C/Users/bob/Downloads/private.part', 'C/Windows/system.part',
                    'D/ProgramData/app/cache.part'):
            self.make(rel, 100, age_days=10)
        with mock.patch.object(winapi, 'known_folder', side_effect=lambda name: os.path.join(home, name.title())):
            roots = b.m_insights_roots()
            self.assertIn(paths.key(c), {paths.key(p) for p in roots})
            self.assertIn(paths.key(d), {paths.key(p) for p in roots})
            result = self.result(b, b.m_insights_scan())
        found = [f for group in result['findings'] for f in group['files'] if f['name'].endswith('.part')]
        self.assertEqual({os.path.basename(f['path']) for f in found}, {'home.part', 'system.part', 'other.part'})
        self.assertEqual(len(found), 3, 'overlapping preferred folders and disk roots must not duplicate files')
        self.assertTrue(all(not f.get('selected') for f in found))

    def test_explicit_scan_does_not_expand_and_empty_roots_are_rejected(self):
        b, home, _, d = self.backend()
        self.make('D/only/picked.part', 100, age_days=10)
        self.make('D/outside/not-picked.part', 100, age_days=10)
        folder = os.path.join(d, 'only')
        with mock.patch.object(winapi, 'known_folder', side_effect=lambda name: os.path.join(home, name.title())):
            result = self.result(b, b.m_insights_scan([folder]))
            with self.assertRaises(UserError):
                b.m_insights_scan([])
        found = [f['name'] for group in result['findings'] for f in group['files']]
        self.assertEqual(found, ['picked.part'])
        self.assertEqual(result['roots'], [folder])

    def test_drive_metadata_distinguishes_local_fixed_from_remote_and_removable(self):
        b = Backend.__new__(Backend)
        b.sandbox = None
        b.env = {'SystemDrive': 'C:'}
        parts = [SimpleNamespace(mountpoint=x + ':\\', fstype='NTFS', opts='rw') for x in 'CDE']
        kinds = dict(zip((p.mountpoint for p in parts),
                         (winapi.DRIVE_FIXED, winapi.DRIVE_REMOTE, winapi.DRIVE_REMOVABLE)))
        with mock.patch('psutil.disk_partitions', return_value=parts), \
                mock.patch('panxu.api.shutil.disk_usage', return_value=SimpleNamespace(total=10, used=1, free=9)), \
                mock.patch.object(winapi, 'drive_type', side_effect=kinds.get), \
                mock.patch.object(winapi, 'volume_label', return_value='fixture'):
            rows = b.m_drives()
        self.assertEqual([r['fixed'] for r in rows], [True, False, False])
        self.assertEqual([r['removable'] for r in rows], [False, False, True])
