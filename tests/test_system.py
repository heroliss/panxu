import os
import struct
import unittest

from helpers import WIN_ENV, TempDirTest

from panxu import startup
from panxu.oplog import OperationLog
from panxu.services import ServiceManager


class FakeRegistry:
    def __init__(self):
        self.data = {}

    def values(self, hive, path):
        return list(self.data.get((hive, path), {}).items())

    def get(self, hive, path, name):
        return self.data.get((hive, path), {}).get(name)

    def set_binary(self, hive, path, name, value):
        self.data.setdefault((hive, path), {})[name] = value


class ApprovalTest(unittest.TestCase):
    def test_encode_decode(self):
        self.assertTrue(startup.approval_enabled(None))
        self.assertTrue(startup.approval_enabled(startup.approval_value(True)))
        off = startup.approval_value(False, now=0)
        self.assertFalse(startup.approval_enabled(off))
        self.assertEqual(len(off), 12)
        self.assertEqual(struct.unpack('<Q', off[4:])[0], 11644473600 * 10_000_000)
        self.assertTrue(startup.approval_enabled(bytes([6]) + bytes(11)))
        self.assertFalse(startup.approval_enabled(bytes([7]) + bytes(11)))


class CommandTest(unittest.TestCase):
    def test_split(self):
        self.assertEqual(startup.split_command('"C:\\Program Files\\A\\a.exe" /background', WIN_ENV),
                         ('C:\\Program Files\\A\\a.exe', '/background'))
        self.assertEqual(startup.split_command('C:\\Tools\\b.exe -x', WIN_ENV), ('C:\\Tools\\b.exe', '-x'))
        exe, args = startup.split_command('%windir%\\system32\\SecurityHealthSystray.exe', WIN_ENV)
        self.assertEqual(exe, 'C:\\Windows\\system32\\SecurityHealthSystray.exe')
        self.assertEqual(startup.split_command('ctfmon', WIN_ENV), ('ctfmon', ''))

    def test_parse_lnk(self):
        target = 'C:\\Tools\\Snip\\snip.exe'
        info_body = target.encode('latin-1') + b'\0'
        header_size = 0x1C
        info = struct.pack('<7I', header_size + len(info_body) + 1, header_size, 1, 0, header_size, 0, header_size + len(info_body))
        info += info_body + b'\0'
        args = '--tray'
        string_data = struct.pack('<H', len(args)) + args.encode('utf-16-le')
        flags = 0x2 | 0x20 | 0x80
        header = struct.pack('<I', 0x4C) + bytes(16) + struct.pack('<I', flags) + bytes(0x4C - 24)
        parsed = startup.parse_lnk(header + info + string_data)
        self.assertEqual(parsed['target'], target)
        self.assertEqual(parsed['arguments'], args)
        self.assertEqual(startup.parse_lnk(b'garbage')['target'], '')


class StartupManagerTest(TempDirTest):
    def test_list_toggle_and_admin(self):
        env = self.posix_env()
        reg = FakeRegistry()
        reg.data[('HKCU', startup.RUN)] = {'Foo': '"C:\\Foo\\foo.exe" -a'}
        reg.data[('HKLM', startup.RUN)] = {'Bar': 'C:\\Bar\\bar.exe'}
        folder = os.path.join(env['APPDATA'], 'Microsoft', 'Windows', 'Start Menu', 'Programs', 'Startup')
        os.makedirs(folder)
        open(os.path.join(folder, 'Thing.lnk'), 'wb').close()
        open(os.path.join(folder, 'desktop.ini'), 'wb').close()
        log = OperationLog(os.path.join(self.tmp, 'h.jsonl'))
        mgr = startup.StartupManager(reg, env, admin=False, oplog=log)
        items = {i['id']: i for i in mgr.list()}
        self.assertEqual(set(items), {'hkcu_run:Foo', 'hklm_run:Bar', 'user_folder:Thing.lnk'})
        self.assertTrue(all(i['enabled'] for i in items.values()))
        self.assertTrue(items['hklm_run:Bar']['needs_admin'])
        mgr.set_enabled('hkcu_run:Foo', False)
        self.assertFalse(startup.approval_enabled(reg.get('HKCU', startup.APPROVED + r'\Run', 'Foo')))
        self.assertEqual(reg.data[('HKCU', startup.RUN)]['Foo'], '"C:\\Foo\\foo.exe" -a', 'Run entry untouched')
        entry = log.entries()[0]
        self.assertEqual(entry['revert'], {'action': 'startup', 'id': 'hkcu_run:Foo', 'enabled': True})
        with self.assertRaises(PermissionError):
            mgr.set_enabled('hklm_run:Bar', False)
        mgr.set_enabled('user_folder:Thing.lnk', False)
        self.assertIn('Thing.lnk', dict(reg.values('HKCU', startup.APPROVED + r'\StartupFolder')))
        with self.assertRaises(ValueError):
            mgr.set_enabled('hkcu_run:Missing', False)


class ServicesTest(TempDirTest):
    def rows(self):
        return [
            {'name': 'ThirdSvc', 'display_name': 'Third', 'status': 'running', 'start_type': 'automatic',
             'binpath': '"C:\\Program Files\\T\\t.exe" -service', 'description': 'x'},
            {'name': 'wuauserv', 'display_name': 'Windows Update', 'status': 'running', 'start_type': 'manual',
             'binpath': 'C:\\Windows\\system32\\svchost.exe -k netsvcs', 'description': ''},
        ]

    def test_classification_and_changes(self):
        calls = []
        log = OperationLog(os.path.join(self.tmp, 'h.jsonl'))
        mgr = ServiceManager(WIN_ENV, admin=True, oplog=log, source=self.rows,
                             runner=lambda argv: (calls.append(argv) or (0, 'ok')))
        svcs = {s['name']: s for s in mgr.list()}
        self.assertEqual(svcs['ThirdSvc']['kind'], 'third_party')
        self.assertEqual(svcs['wuauserv']['kind'], 'windows')
        self.assertFalse(svcs['wuauserv']['editable'])
        mgr.set_start_type('ThirdSvc', 'disabled', stop_now=True)
        self.assertEqual(calls[0][1:], ['config', 'ThirdSvc', 'start=', 'disabled'])
        self.assertEqual(calls[1][1:], ['stop', 'ThirdSvc'])
        self.assertEqual(log.entries()[0]['revert']['start_type'], 'auto')
        with self.assertRaises(PermissionError):
            mgr.set_start_type('wuauserv', 'disabled')
        with self.assertRaises(ValueError):
            mgr.set_start_type('ThirdSvc', 'bogus')
        with self.assertRaises(PermissionError):
            ServiceManager(WIN_ENV, admin=False, source=self.rows, runner=lambda a: (0, '')).set_start_type('ThirdSvc', 'manual')

    def test_sc_failure_is_reported(self):
        mgr = ServiceManager(WIN_ENV, admin=True, source=self.rows, runner=lambda a: (5, '[SC] OpenService 失败 5:\n拒绝访问。'))
        with self.assertRaises(RuntimeError) as ctx:
            mgr.set_start_type('ThirdSvc', 'manual')
        self.assertIn('拒绝访问', str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
