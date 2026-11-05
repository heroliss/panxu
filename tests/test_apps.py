import os
import unittest
from unittest import mock

from helpers import TempDirTest, age

from panxu import apps, winapi
from panxu.apps import AppManager
from panxu.oplog import OperationLog

BASE = r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'


class FakeRegistry:
    def __init__(self, keys):
        self.keys = keys  # {(hive, path): {name: value}}

    def values(self, hive, path):
        return list(self.keys.get((hive, path), {}).items())

    def subkeys(self, hive, path):
        prefix = path + '\\'
        return [p[len(prefix):] for h, p in self.keys if h == hive and p.startswith(prefix) and '\\' not in p[len(prefix):]]


class AppsTest(TempDirTest):
    def setUp(self):
        super().setUp()
        self.windows = os.path.join(self.tmp, 'Windows')
        self.pf = os.path.join(self.tmp, 'Program Files')
        os.makedirs(os.path.join(self.pf, 'FooSoft'))
        self.make('Program Files/FooSoft/FooSoft.exe', 10)
        self.make('Program Files/FooSoft/unins000.exe', 10)
        self.env = {'SystemRoot': self.windows}

        def entry(**values):
            return values
        self.reg = FakeRegistry({
            ('HKLM', BASE + '\\FooSoft'): entry(DisplayName='FooSoft 设计工具', DisplayVersion='2.3.0', Publisher='FooSoft Inc.',
                                               InstallDate='20250508', InstallLocation=self.pf + '\\FooSoft',
                                               UninstallString='MsiExec.exe /I{A1B2C3D4-1111-4222-8333-0123456789AB}',
                                               WindowsInstaller=1, EstimatedSize=2048),
            ('HKLM', BASE + '\\VCRedist'): entry(DisplayName='Microsoft Visual C++ 2015-2022 Redistributable (x64)',
                                                DisplayVersion='14.40', Publisher='Microsoft Corporation', UninstallString='x.exe'),
            ('HKLM', BASE + '\\Driver'): entry(DisplayName='NVIDIA 图形驱动程序 566.36', Publisher='NVIDIA', UninstallString='y.exe'),
            ('HKLM', BASE + '\\Steam App 1'): entry(DisplayName='Some Game', UninstallString='"steam://uninstall/1"'),
            ('HKLM', BASE + '\\Hidden'): entry(DisplayName='Office Licensing Component', SystemComponent=1),
            ('HKLM', BASE + '\\KB1'): entry(DisplayName='Update for Windows (KB1)', ParentKeyName='OperatingSystem'),
            ('HKLM', BASE + '\\Hotfix'): entry(DisplayName='Security Update', ReleaseType='Security Update'),
            ('HKLM', BASE + '\\NoName'): entry(Publisher='nobody'),
        })
        self.log = OperationLog(os.path.join(self.tmp, 'h.jsonl'))
        self.apps = AppManager(registry=self.reg, env=self.env, admin=True, oplog=self.log)

    def test_list_hides_components_and_updates_and_classifies(self):
        rows = {a['name']: a for a in self.apps.list()}
        self.assertEqual(set(rows), {'FooSoft 设计工具', 'Microsoft Visual C++ 2015-2022 Redistributable (x64)',
                                     'NVIDIA 图形驱动程序 566.36', 'Some Game'})
        self.assertEqual(rows['FooSoft 设计工具']['kind'], 'app')
        self.assertEqual(rows['Microsoft Visual C++ 2015-2022 Redistributable (x64)']['kind'], 'runtime')
        self.assertEqual(rows['NVIDIA 图形驱动程序 566.36']['kind'], 'driver')
        self.assertEqual(rows['Some Game']['kind'], 'game')
        foo = rows['FooSoft 设计工具']
        self.assertEqual(foo['size'], 2048 * 1024)
        self.assertTrue(foo['location_exists'])
        self.assertTrue(foo['msi'])

    def test_last_run_from_prefetch(self):
        self.assertFalse(self.apps.list()[0]['usage_known'], 'no Prefetch folder: usage unknown')
        pf = self.make('Windows/Prefetch/FOOSOFT.EXE-1A2B3C4D.pf', 10)
        age(pf, 3)
        self.make('Windows/Prefetch/UNINS000.EXE-00000000.pf', 10)
        foo = next(a for a in AppManager(registry=self.reg, env=self.env, admin=True).list() if a['name'].startswith('FooSoft'))
        self.assertTrue(foo['usage_known'])
        self.assertAlmostEqual(foo['last_used'], os.stat(pf).st_mtime, delta=1)

    def prefetch(self, exe, days):
        pf = self.make(f'Windows/Prefetch/{exe}-{abs(hash(exe)) % 0xFFFFFFFF:08X}.pf', 10)
        age(pf, days)
        return os.stat(pf).st_mtime

    def listed(self):
        return {a['name']: a for a in AppManager(registry=self.reg, env=self.env, admin=True).list()}

    def test_programs_sharing_one_registered_folder(self):
        # found on a real PC: Creative Cloud registers C:\Program Files\Adobe for eight programs, so each was
        # credited with every Adobe exe -- and Acrobat's sync helper starts with Windows every day
        adobe = os.path.join(self.pf, 'Adobe')
        self.make('Program Files/Adobe/Adobe Premiere Pro 2023/Adobe Premiere Pro.exe', 10)
        self.make('Program Files/Adobe/Adobe Media Encoder 2023/Adobe Media Encoder.exe', 10)
        self.make('Program Files/Adobe/Acrobat DC/Acrobat/AdobeCollabSync.exe', 10)
        for key, name in (('PPRO', 'Adobe Premiere Pro 2023'), ('AME', 'Adobe Media Encoder 2023')):
            self.reg.keys[('HKLM', BASE + '\\' + key)] = {'DisplayName': name, 'Publisher': 'Adobe Inc.', 'InstallLocation': adobe,
                                                          'UninstallString': 'x.exe'}
        self.prefetch('ADOBECOLLABSYNC.EXE', 0)
        premiere = self.prefetch('ADOBE PREMIERE PRO.EXE', 400)
        rows = self.listed()
        self.assertEqual(rows['Adobe Premiere Pro 2023']['location'], os.path.join(adobe, 'Adobe Premiere Pro 2023'))
        self.assertAlmostEqual(rows['Adobe Premiere Pro 2023']['last_used'], premiere, delta=1)
        self.assertIsNone(rows['Adobe Media Encoder 2023']['last_used'], 'never ran: not "today"')

    def test_a_program_without_install_location(self):
        # found on a real PC: 46 of 97 programs (网易云音乐, 哔哩哔哩, Steam...) have no InstallLocation
        uninst = self.make('Program Files/Netease/CloudMusic/uninst.exe', 10)
        self.make('Program Files/Netease/CloudMusic/cloudmusic.exe', 10)
        self.reg.keys[('HKLM', BASE + '\\cloudmusic')] = {'DisplayName': '网易云音乐', 'UninstallString': f'"{uninst}"'}
        self.reg.keys[('HKLM', BASE + '\\MsiOnly')] = {'DisplayName': 'Msi Only', 'UninstallString': 'MsiExec.exe /I{1}'}
        used = self.prefetch('CLOUDMUSIC.EXE', 2)
        self.prefetch('UNINST.EXE', 0)
        rows = self.listed()
        music = rows['网易云音乐']
        self.assertEqual(music['location'], os.path.dirname(uninst))
        self.assertTrue(music['location_exists'])
        self.assertAlmostEqual(music['last_used'], used, delta=1)
        self.assertFalse(rows['Msi Only']['location_exists'])
        self.assertIsNone(rows['Msi Only']['last_used'])

    def test_helpers_that_run_on_their_own_do_not_count(self):
        for exe in ('FooSoftTray.exe', 'FooUpdate.exe', 'FooSoftService64.exe', 'node.exe'):
            self.make(f'Program Files/FooSoft/{exe}', 10)
            self.prefetch(exe.upper(), 0)
        used = self.prefetch('FOOSOFT.EXE', 30)
        self.assertAlmostEqual(self.listed()['FooSoft 设计工具']['last_used'], used, delta=1)
        self.assertTrue(apps._helper_exe('卸载哔哩哔哩.exe'))
        self.assertTrue(apps._helper_exe('OneDriveSetup.exe'))
        self.assertFalse(apps._helper_exe('Stray.exe'))
        best = apps._word_fit('ACDSeeUltimate2019', 'ACDSee Photo Studio Ultimate 2019', 'ACD Systems')
        self.assertGreater(best, apps._word_fit('ACDSeeIndexerUltimate12', 'ACDSee Photo Studio Ultimate 2019', 'ACD Systems'))

    def test_folder_matching(self):
        index = self.apps.match_index()
        self.assertEqual(self.apps.match_folder('FooSoft', index)['name'], 'FooSoft 设计工具')
        self.assertEqual(self.apps.match_folder('foosoft', index)['name'], 'FooSoft 设计工具')
        for generic in ('Cache', 'Temp', 'Microsoft', 'Programs', 'x'):
            self.assertIsNone(self.apps.match_folder(generic, index), generic)
        self.assertIsNone(self.apps.match_folder('OldGameStudio', index))

    def test_msi_uninstall_uses_remove_switch(self):
        foo = next(a for a in self.apps.list() if a['name'].startswith('FooSoft'))
        with mock.patch.object(winapi, 'shell_execute', return_value=True) as run:
            result = self.apps.uninstall(foo['id'])
        exe, args = run.call_args[0]
        self.assertEqual(os.path.basename(exe).lower(), 'msiexec.exe')
        self.assertEqual(args, '/X{A1B2C3D4-1111-4222-8333-0123456789AB}')
        self.assertFalse(result['simulated'])
        self.assertEqual(self.log.entries()[0]['kind'], 'uninstall')

    def test_steam_uninstall_opens_steam(self):
        game = next(a for a in self.apps.list() if a['kind'] == 'game')
        with mock.patch.object(winapi, 'open_uri') as uri, mock.patch.object(winapi, 'shell_execute') as run:
            self.apps.uninstall(game['id'])
        uri.assert_called_once_with('steam://uninstall/1')
        run.assert_not_called()

    def test_refused_uninstaller_is_reported(self):
        foo = next(a for a in self.apps.list() if a['name'].startswith('FooSoft'))
        with mock.patch.object(winapi, 'shell_execute', return_value=False):
            with self.assertRaises(ValueError):
                self.apps.uninstall(foo['id'])
        self.assertEqual(self.log.entries(), [])

    def test_icon_sources(self):
        rows = {a['name']: a for a in self.apps.list()}
        self.assertEqual(os.path.basename(rows['FooSoft 设计工具']['icon']), 'FooSoft.exe', 'main exe, not the uninstaller')
        # DisplayIcon with quotes, an environment variable and a resource index
        ico = self.make('Program Files/Bar/bar.ico', 10)
        self.reg.keys[('HKLM', BASE + '\\Bar')] = {'DisplayName': 'Bar', 'DisplayIcon': '"%SystemRoot%\\..\\Program Files\\Bar\\bar.ico",-101'}
        # an MSI package without DisplayIcon: its icon is cached under %SystemRoot%\Installer\{ProductCode}
        code = '{11111111-2222-3333-4444-555555555555}'
        cached = self.make(f'Windows/Installer/{code}/ARPPRODUCTICON.ico', 10)
        self.reg.keys[('HKLM', BASE + '\\' + code)] = {'DisplayName': 'MsiThing', 'WindowsInstaller': 1}
        # the exe one folder down, as with Adobe Acrobat
        self.make('Program Files/Deep/App/deep.exe', 10)
        self.reg.keys[('HKLM', BASE + '\\Deep')] = {'DisplayName': 'Deep', 'InstallLocation': os.path.join(self.pf, 'Deep')}
        rows = {a['name']: a for a in self.apps.list(fresh=True)}
        self.assertEqual((os.path.normcase(os.path.normpath(rows['Bar']['icon'])), rows['Bar']['icon_index']),
                         (os.path.normcase(os.path.normpath(ico)), -101))
        self.assertEqual(rows['MsiThing']['icon'], cached)
        self.assertEqual(os.path.basename(rows['Deep']['icon']), 'deep.exe')

    def test_measured_size_survives_refresh(self):
        foo = next(a for a in self.apps.list() if a['name'].startswith('FooSoft'))
        measured = self.apps.measure(foo['id'])['size']
        again = next(a for a in self.apps.list(fresh=True) if a['id'] == foo['id'])
        self.assertEqual((again['size'], again['size_source']), (measured, 'measured'))

    def test_kind_labels_cover_every_kind(self):
        for a in self.apps.list():
            self.assertEqual(a['kind_label'], apps.KIND_LABELS[a['kind']])


if __name__ == '__main__':
    unittest.main()
