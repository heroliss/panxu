import ctypes
import json
import os
from pathlib import Path
import struct
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zlib

from diskpilot import icons
from diskpilot.icons import IconNotFound, IconService


class IconTests(unittest.TestCase):
    def setUp(self):
        self.services = []
        self.png = icons._png(bytes([20, 100, 180, 255]) * 32 * 32)

    def tearDown(self):
        for service in self.services:
            service.close()

    def service(self, items, **options):
        value = IconService(items.get, **options)
        self.services.append(value)
        return value

    def await_ready(self, service, ident):
        for _ in range(100):
            result = service.get(ident)
            if result['status'] != 'pending':
                return result
            time.sleep(.005)
        self.fail('icon failed to finish')

    def test_only_known_inventory_ids_are_resolved_and_no_path_is_returned(self):
        item = dict(id='software-123', kind='software', name='Fixture', path='private-local-path', signature='v1')
        service = self.service({item['id']: item}, source_resolver=lambda row, deadline: {'type': 'fixture', 'source': 'program_resource'}, backend=lambda source, deadline: self.png)
        self.assertFalse(service._threads)
        result = self.await_ready(service, item['id'])
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['mime'], 'image/png')
        self.assertNotIn('private-local-path', json.dumps({k:v for k,v in result.items() if k != 'body'}))
        for ident in ('unknown', '../private', 'C:\\secret.exe', 'https://example.com', None):
            with self.assertRaises(IconNotFound):
                service.get(ident)

    def test_cache_deduplicates_concurrent_requests_and_revises_with_item_signature(self):
        item = dict(id='process-abc', kind='process', signature='first', path='fixture')
        calls, started, release = [], threading.Event(), threading.Event()
        def backend(source, deadline):
            calls.append(True);started.set();release.wait(1);return self.png
        service = self.service({item['id']: item}, wait_ms=0, source_resolver=lambda row, deadline: {'type':'fixture'}, backend=backend)
        self.assertEqual(service.get(item['id'])['status'], 'pending')
        self.assertTrue(started.wait(1))
        for _ in range(50):
            self.assertEqual(service.get(item['id'])['status'], 'pending')
        release.set()
        self.assertEqual(self.await_ready(service,item['id'])['status'], 'ready')
        self.assertEqual(len(calls), 1)
        for _ in range(20):self.assertEqual(service.get(item['id'])['status'],'ready')
        self.assertEqual(len(calls), 1)
        item['signature'] = 'second'
        self.assertEqual(self.await_ready(service,item['id'])['status'],'ready')
        self.assertEqual(len(calls), 2)

    def test_slow_native_work_cannot_block_caller_queue_or_close_indefinitely(self):
        items={f'file-{n}':dict(id=f'file-{n}',kind='file',signature='1') for n in range(20)}
        release, started = threading.Event(), threading.Event()
        def backend(source, deadline):started.set();release.wait(2);return self.png
        service=self.service(items,workers=1,max_pending=2,wait_ms=0,source_resolver=lambda row, deadline:{'type':'fixture'},backend=backend)
        service.get('file-0');self.assertTrue(started.wait(1))
        begin=time.monotonic()
        results=[service.get(f'file-{n}') for n in range(1,20)]
        self.assertLess(time.monotonic()-begin,.3)
        self.assertLessEqual(service._queue.qsize(),2)
        self.assertLessEqual(len(service._pending),3)
        self.assertTrue(any(result['status']=='fallback' for result in results))
        begin=time.monotonic();service.close();self.assertLess(time.monotonic()-begin,.4)
        release.set()

    def test_lru_cache_and_png_dimensions_are_bounded(self):
        items={f'file-{n}':dict(id=f'file-{n}',kind='file',signature='1') for n in range(9)}
        service=self.service(items,max_cache=3,source_resolver=lambda row, deadline:{'type':'fixture'},backend=lambda source, deadline:self.png)
        for ident in items:self.await_ready(service,ident)
        self.assertLessEqual(len(service._cache),3)
        self.assertLessEqual(service._bytes,2097152)
        invalid=self.service({'file-bad':dict(id='file-bad')},source_resolver=lambda row, deadline:{'type':'fixture'},backend=lambda source, deadline:b'\x89PNG\r\n\x1a\n'+b'x'*30)
        self.assertEqual(self.await_ready(invalid,'file-bad')['status'],'fallback')

    def test_display_icon_and_commands_do_not_resolve_or_execute_arguments(self):
        if os.name != 'nt':self.skipTest('Windows path syntax')
        value=icons.parse_icon_location(r'"C:\Program Files\Fixture\app.exe",-14')
        self.assertEqual(value['index'],-14)
        self.assertEqual(value['path'],r'C:\Program Files\Fixture\app.exe')
        command=icons.executable_from_command(r'"C:\Program Files\Fixture\app.exe" --secret-do-not-use "E:\ignored.dll"')
        self.assertEqual(command['path'],value['path'])
        for value in [r'\\server\share\app.exe,0',r'\\?\C:\app.exe,0',r'C:\a.exe:stream,0',r'C:\a\..\b.exe',
                      'https://example.com/icon.ico',r'C:\x.exe,128',r'C:\x.exe,-999999','relative.exe',r'C:\a.lnk',r'C:\a.exe -argument']:
            with self.subTest(value=value), self.assertRaises(ValueError):icons.parse_icon_location(value)
        for command in ['app.exe --anything','cmd /c launch.exe',r'"\\server\app.exe" -x','https://example.com/run.exe']:
            with self.assertRaises(ValueError):icons.executable_from_command(command)

    def test_local_file_guards_identity_size_and_reparse_before_native_loading(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);path=root/'fixture.exe';path.write_bytes(b'MZfixture')
            absolute,first=icons._safe_file(str(path))
            path.write_bytes(b'MZchanged-fixture-size')
            self.assertNotEqual(first,icons._safe_file(str(path))[1])
            with patch('diskpilot.icons.MAX_FILE_BYTES',2):
                with self.assertRaises(ValueError):icons._safe_file(str(path))
            link=root/'alias.exe'
            try:os.symlink(path,link)
            except (OSError,NotImplementedError):return
            with self.assertRaises(ValueError):icons._safe_file(str(link))

    def test_fixed_drive_check_precedes_any_file_access(self):
        if os.name!='nt':self.skipTest('Windows drive checks')
        class Kernel:
            def GetDriveTypeW(self,path):return 4
        class Api:kernel=Kernel()
        with patch('diskpilot.icons._apis',return_value=Api()),patch('diskpilot.icons.os.lstat',side_effect=AssertionError('network path accessed')):
            with self.assertRaises(ValueError):icons._safe_file(r'Z:\mapped-network.exe')

    def test_private_startup_and_service_commands_require_matching_digests(self):
        if os.name!='nt':self.skipTest('Windows registry sources')
        import winreg
        class Key:
            def __enter__(self):return self
            def __exit__(self,*args):pass
        raw=r'"C:\Fixture\app.exe" --private'
        row=dict(id='startup-test',kind='startup',name='Fixture',details={'registry_location':r'HKCU\Software\Microsoft\Windows\CurrentVersion\Run','command_digest':'outdated'})
        source=icons._Sources()
        with patch.object(winreg,'OpenKey',return_value=Key()),patch.object(winreg,'QueryValueEx',return_value=(raw,winreg.REG_SZ)):
            self.assertIsNone(source(row,time.monotonic()+1))
            row['details']['command_digest']=icons.digest(raw)
            self.assertEqual(source(row,time.monotonic()+1)['path'],r'C:\Fixture\app.exe')
        shortcut=dict(id='startup-shortcut',kind='startup',path=r'C:\Fixture\app.lnk',details={})
        with patch.object(winreg,'OpenKey',side_effect=AssertionError('shortcut resolved')):
            self.assertIsNone(source(shortcut,time.monotonic()+1))

    @unittest.skipUnless(os.name=='nt','Windows resource rendering')
    def test_real_windows_stock_and_executable_png_and_gdi_resources(self):
        source=icons.parse_icon_location('"'+sys.executable+'"')
        raw=icons._extract(source,time.monotonic()+2)
        self.assertIsNotNone(raw)
        self.assertEqual(struct.unpack_from('>II',raw,16),(32,32))
        rgba=[];offset=8
        while offset<len(raw):
            length=struct.unpack_from('>I',raw,offset)[0];kind=raw[offset+4:offset+8]
            if kind==b'IDAT':rgba.append(raw[offset+8:offset+8+length])
            offset+=length+12
        pixels=zlib.decompress(b''.join(rgba))
        self.assertEqual(len(pixels),32*(1+32*4))
        self.assertGreater(len(set(pixels)),5)
        api=icons._apis()
        api.kernel.GetCurrentProcess.restype=ctypes.c_void_p
        api.user.GetGuiResources.argtypes=[ctypes.c_void_p,ctypes.c_ulong]
        api.user.GetGuiResources.restype=ctypes.c_ulong
        process=api.kernel.GetCurrentProcess()
        stock=dict(type='stock',stock=3,source='system_folder')
        icons._extract(stock,time.monotonic()+2)
        before=[api.user.GetGuiResources(process,flag) for flag in (0,1)]
        for _ in range(30):self.assertIsNotNone(icons._extract(stock,time.monotonic()+2))
        after=[api.user.GetGuiResources(process,flag) for flag in (0,1)]
        self.assertTrue(all(end<=start+2 for start,end in zip(before,after)),(before,after))
        with patch('diskpilot.icons._draw',side_effect=RuntimeError('fixture render failure')):
            with self.assertRaises(RuntimeError):icons._extract(source,time.monotonic()+2)
        final=[api.user.GetGuiResources(process,flag) for flag in (0,1)]
        self.assertTrue(all(end<=start+2 for start,end in zip(before,final)),(before,final))


if __name__=='__main__':unittest.main()
