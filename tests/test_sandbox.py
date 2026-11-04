import os
import time
import unittest

from helpers import TempDirTest

from panxu import paths, sandbox, winapi
from panxu.apps import AppManager
from panxu.services import ServiceManager
from panxu.startup import StartupManager


class SandboxTestCase(TempDirTest):
    """A freshly built simulation, active for the duration of each test."""

    def setUp(self):
        super().setUp()
        self.sb = sandbox.Sandbox(os.path.join(self.tmp, 'sb'))
        sandbox.activate(self.sb)
        self.env = paths.environment()

    def tearDown(self):
        sandbox.deactivate()
        super().tearDown()

    def policy(self):
        return paths.ProtectionPolicy(self.env, allowed_roots=[self.sb.root], drive_roots=self.sb.drive_roots)


class BuildTest(SandboxTestCase):
    def test_environment_points_into_the_sandbox(self):
        for key in ('SystemRoot', 'USERPROFILE', 'LOCALAPPDATA', 'ProgramData', 'TEMP'):
            self.assertTrue(paths.is_within(self.env[key], self.sb.root), key)
        self.assertTrue(paths.is_within(winapi.known_folder('downloads'), self.sb.root))
        self.assertIs(winapi.simulation(), self.sb)
        sandbox.deactivate()
        self.assertIsNone(winapi.simulation())
        self.assertFalse(paths.is_within(paths.environment().get('USERPROFILE', '/nowhere'), self.sb.root))
        sandbox.activate(self.sb)  # already built: loads the saved state instead of building again

    def test_large_files_are_sparse(self):
        big = self.sb.native('D/Games/Genshin Impact/GenshinImpact_Data/blocks.pak')
        self.assertEqual(os.path.getsize(big), 64000 * 1024 * 1024)
        if winapi.IS_WINDOWS:
            import ctypes
            k32 = ctypes.WinDLL('kernel32')
            k32.GetCompressedFileSizeW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)]
            hi = ctypes.c_ulong(0)
            lo = k32.GetCompressedFileSizeW(big, ctypes.byref(hi))
            self.assertLess(hi.value * 2 ** 32 + lo, 4 * 1024 * 1024, 'the simulation must not use real disk space')
        else:
            self.assertLess(os.stat(big).st_blocks * 512, 4 * 1024 * 1024)

    def test_drives_and_disk_usage(self):
        drives = self.sb.drives()
        self.assertEqual([d['letter'] for d in drives], ['C:', 'D:'])
        c = drives[0]
        self.assertTrue(c['system'])
        self.assertEqual(c['total'], c['used'] + c['free'])
        self.assertGreater(c['used'], sandbox.DRIVES['C'][2])  # base usage plus the simulated files

    def test_reset_refuses_foreign_folders(self):
        other = os.path.join(self.tmp, 'precious')
        os.makedirs(other)
        with open(os.path.join(other, 'a.txt'), 'w') as fh:
            fh.write('x')
        with self.assertRaises(RuntimeError):
            sandbox.Sandbox(other).reset()
        self.assertTrue(os.path.exists(os.path.join(other, 'a.txt')))


class PolicyTest(SandboxTestCase):
    def test_only_the_sandbox_can_be_touched(self):
        policy = self.policy()
        outside = os.path.join(self.tmp, 'outside.txt')
        self.assertIn('演练', policy.reason(outside))
        self.assertIsNone(policy.reason(self.sb.native('C/Users/demo/Downloads/SteamSetup.exe')))

    def test_simulated_drives_are_protected_like_real_ones(self):
        policy = self.policy()
        for rel in ('C', 'D', 'C/Windows', 'C/Windows/System32/sc.exe', 'C/$Recycle.Bin', 'C/Users', 'C/Users/demo',
                    'C/Program Files', 'C/Users/demo/AppData/Local'):
            self.assertIsNotNone(policy.reason(self.sb.native(rel)), rel)


class RecycleTest(SandboxTestCase):
    def test_recycle_and_restore(self):
        target = self.sb.native('C/Users/demo/Downloads/SteamSetup.exe')
        size = os.path.getsize(target)
        self.assertEqual(winapi.recycle_paths([target]), (0, False))
        self.assertFalse(os.path.exists(target))
        self.assertEqual(winapi.recycle_bin_info(), (size, 1))
        item = self.sb.recycle_items()[0]
        self.assertEqual(item['original'], target)
        self.assertEqual(self.sb.restore(item['id']), target)
        self.assertTrue(os.path.exists(target))
        self.assertEqual(winapi.recycle_bin_info(), (0, 0))

    def test_paths_outside_are_never_moved(self):
        outside = self.make('outside/keep.txt', 10)
        winapi.recycle_paths([outside])
        self.assertTrue(os.path.exists(outside))
        self.assertEqual(self.sb.recycle_items(), [])

    def test_empty_recycle_bin(self):
        winapi.recycle_paths([self.sb.native('C/Users/demo/Downloads/ChromeSetup.exe')])
        self.assertTrue(winapi.empty_recycle_bin())
        self.assertEqual(winapi.recycle_bin_info(), (0, 0))


class SystemStateTest(SandboxTestCase):
    def test_startup_items_come_from_the_fake_registry(self):
        items = StartupManager(registry=self.sb.registry, admin=True).list()
        names = {i['name']: i for i in items}
        self.assertIn('QuickViewHelper', names)
        self.assertFalse(names['Steam']['enabled'], 'StartupApproved marks Steam as disabled')
        self.assertEqual(names['QuickViewHelper']['publisher'], 'Fastview Network Technology Co., Ltd.')
        sm = StartupManager(registry=self.sb.registry, admin=True)
        sm.set_enabled(names['QuickViewHelper']['id'], False)
        again = {i['name']: i for i in StartupManager(registry=self.sb.registry, admin=True).list()}
        self.assertFalse(again['QuickViewHelper']['enabled'])

    def test_services_through_simulated_sc(self):
        sm = ServiceManager(admin=True, source=self.sb.services, runner=self.sb.sc)
        svc = {s['name']: s for s in sm.list()}
        self.assertEqual(svc['WSearch']['start_type'], 'delayed')
        self.assertEqual(svc['QVUpdateSvc']['kind'], 'third_party')
        sm.set_start_type('QVUpdateSvc', 'disabled', stop_now=True)
        svc = {s['name']: s for s in sm.list()}
        self.assertEqual((svc['QVUpdateSvc']['start_type'], svc['QVUpdateSvc']['status']), ('disabled', 'stopped'))
        self.assertNotEqual(self.sb.sc(['sc', 'config', 'nope', 'start=', 'demand'])[0], 0)

    def test_simulated_uninstall_leaves_app_data_behind(self):
        apps = AppManager(registry=self.sb.registry, admin=True)
        qv = next(a for a in apps.list() if a['name'] == '快看图大师')
        result = apps.uninstall(qv['id'])
        self.assertTrue(result['simulated'])
        self.assertFalse(os.path.exists(self.sb.native('C/Program Files/QuickView')))
        self.assertTrue(os.path.exists(self.sb.native('C/ProgramData/QuickView')), 'like real uninstallers')
        self.assertNotIn('快看图大师', [a['name'] for a in apps.list(fresh=True)])
        run = dict(self.sb.registry.values('HKCU', sandbox.RUN))
        self.assertNotIn('QuickViewHelper', run)
        self.assertNotIn('QVUpdateSvc', [s['name'] for s in self.sb.services()])

    def test_leftovers_after_uninstalling(self):
        apps = AppManager(registry=self.sb.registry, admin=True)
        rows = {a['name']: a for a in apps.list()}
        qv, game, wechat = rows['快看图大师'], rows['Hollow Knight'], rows['微信']
        with self.assertRaises(ValueError):
            apps.leftovers(qv['id'])  # not uninstalled here
        for app in (qv, game, wechat):
            self.assertTrue(apps.installed(app['id']))
            apps.uninstall(app['id'])
            self.assertFalse(apps.installed(app['id']), 'the entry is gone: done')
        found = {os.path.relpath(r['path'], self.sb.root): r for r in apps.leftovers(qv['id'])}
        self.assertEqual(sorted(found), sorted(os.path.normpath(p) for p in (
            'C/Users/demo/AppData/Local/QuickView', 'C/Users/demo/AppData/Roaming/QuickView', 'C/ProgramData/QuickView')))
        self.assertGreater(found[os.path.normpath('C/ProgramData/QuickView')]['size'], 600 * 1024 * 1024)
        self.assertIn('设置', found[os.path.normpath('C/Users/demo/AppData/Roaming/QuickView')]['caution'])
        saves = apps.leftovers(game['id'])
        self.assertEqual([os.path.relpath(r['path'], self.sb.root) for r in saves],
                         [os.path.normpath('C/Users/demo/AppData/LocalLow/Team Cherry/Hollow Knight')])
        self.assertIn('存档', saves[0]['caution'])
        tencent = [os.path.relpath(r['path'], self.sb.root) for r in apps.leftovers(wechat['id'])]
        self.assertIn(os.path.normpath('C/Users/demo/AppData/Roaming/Tencent/WeChat'), tencent)
        self.assertNotIn(os.path.normpath('C/Users/demo/AppData/Roaming/Tencent'), tencent, 'never the publisher folder')

    def test_programs_in_a_shared_folder(self):
        apps = AppManager(registry=self.sb.registry, admin=True)
        rows = {a['name']: a for a in apps.list()}
        premiere, acrobat = rows['Adobe Premiere Pro 2023'], rows['Adobe Acrobat (64-bit)']
        self.assertTrue(premiere['location'].endswith('Adobe Premiere Pro 2023'))
        self.assertGreater(time.time() - premiere['last_used'], 300 * 86400, "Acrobat's daily helper is not Premiere")
        self.assertGreater(time.time() - acrobat['last_used'], 20 * 86400, 'nor is it Acrobat itself')
        self.assertLess(time.time() - rows['网易云音乐']['last_used'], 86400, 'found without an InstallLocation')
        apps.uninstall(premiere['id'])
        self.assertFalse(os.path.exists(self.sb.native('C/Program Files/Adobe/Adobe Premiere Pro 2023')))
        self.assertTrue(os.path.exists(self.sb.native('C/Program Files/Adobe/Acrobat DC')), 'only its own folder')
        self.assertTrue(os.path.exists(self.sb.native('C/Program Files/Adobe/Adobe Media Encoder 2023')))

    def test_cleanup_commands_are_simulated(self):
        folder = self.sb.native('C/Windows/WinSxS/Temp/PendingDeletes')
        self.assertTrue(os.listdir(folder))
        self.sb.run_cleanup_command('component_store')
        self.assertEqual(os.listdir(folder), [])
        self.sb.run_cleanup_command('windows_old')
        self.assertFalse(os.path.exists(self.sb.native('C/Windows.old')))


if __name__ == '__main__':
    unittest.main()
