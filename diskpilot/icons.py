"""Private, read-only, bounded Windows icon rendering for known inventory IDs.

No SHGetFileInfo, thumbnail provider, shortcut resolution, shell extension,
process launch or executable import is used. PE files are mapped only as data
and image resources; returned PNGs stay in a bounded in-memory local cache.
Native calls have cooperative (not preemptive) deadlines. Two daemon workers
and a bounded queue keep slow local I/O away from the HTTP/list rendering path.

Microsoft API references checked 2026-11-02:
https://learn.microsoft.com/en-us/windows/win32/api/libloaderapi/nf-libloaderapi-loadlibraryexw
https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shgetstockiconinfo
https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-createiconfromresourceex
https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-drawiconex
"""
from __future__ import annotations

from collections import OrderedDict
import ctypes
from ctypes import wintypes as wt
import hashlib
import json
import ntpath
import os
import queue
import re
import stat
import struct
import threading
import time
import zlib

from .scanner import _checked_root, _is_reparse, _metadata_signature
from .windows import digest

SIZE = 32
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_ICON_BYTES = 1024 * 1024
MAX_PNG_BYTES = 32768
FALLBACK_SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32"><rect x="4" y="4" width="24" height="24" rx="6" fill="#eef2f6"/><rect x="9" y="9" width="14" height="14" rx="3" fill="none" stroke="#68788d" stroke-width="1.5"/><path d="M13 13h6M13 17h6M13 21h3" stroke="#68788d" stroke-width="1.5" stroke-linecap="round"/></svg>'
_RUN_KEYS = (r'Software\Microsoft\Windows\CurrentVersion\Run', r'Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run')
_UNINSTALL_KEYS = (r'Software\Microsoft\Windows\CurrentVersion\Uninstall', r'Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall')


class IconNotFound(KeyError):
    pass


class _Later(Exception):
    pass


def _deadline(deadline):
    if time.monotonic() > deadline:
        raise TimeoutError('icon_budget')


def _location(value):
    if not isinstance(value, str) or not value or len(value) > 4096 or any(ord(c) < 32 for c in value):
        raise ValueError('icon_location')
    allowed = {'systemroot': 'WINDIR', 'windir': 'WINDIR', 'systemdrive': 'SystemDrive',
               'programfiles': 'ProgramFiles', 'programfiles(x86)': 'ProgramFiles(x86)',
               'programw6432': 'ProgramW6432', 'localappdata': 'LOCALAPPDATA',
               'appdata': 'APPDATA', 'programdata': 'PROGRAMDATA', 'userprofile': 'USERPROFILE'}

    def replace(match):
        name = allowed.get(match[1].casefold())
        if not name or not os.environ.get(name):
            raise ValueError('icon_environment')
        return os.environ[name]

    value = re.sub(r'%([^%]+)%', replace, value.strip())
    if value.casefold().startswith('\\systemroot\\'):
        value = os.path.join(os.environ.get('WINDIR', r'C:\Windows'), value[12:])
    if '%' in value or value.startswith(('\\\\', '//', '\\??\\')):
        raise ValueError('icon_network_or_device')
    drive, tail = ntpath.splitdrive(value)
    if os.name == 'nt':
        if not re.fullmatch(r'[A-Za-z]:', drive) or not tail.startswith(('\\', '/')):
            raise ValueError('icon_absolute_local_path')
        if ':' in tail or any(part in ('.', '..') or part.endswith((' ', '.')) for part in re.split(r'[\\/]', tail) if part):
            raise ValueError('icon_path_component')
    elif not os.path.isabs(value):
        raise ValueError('icon_absolute_local_path')
    return os.path.abspath(value)


def parse_icon_location(value):
    """DisplayIcon/default icon location; never a command or URI."""
    if not isinstance(value, str) or len(value) > 4096:
        raise ValueError('icon_location')
    match = re.fullmatch(r'\s*("[^"\r\n]+"|[^"\r\n]+?)(?:\s*,\s*(-?\d+))?\s*', value)
    if not match:
        raise ValueError('icon_location')
    path = match[1].strip().strip('"')
    index = int(match[2] or 0)
    if not -65535 <= index <= 127:
        raise ValueError('icon_index')
    path = _location(path)
    if ntpath.splitext(path)[1].casefold() not in ('.exe', '.dll', '.ico', '.icl', '.cpl'):
        raise ValueError('icon_resource_type')
    return dict(type='resource', path=path, index=index, source='program_resource')


def executable_from_command(value):
    """Read only an explicit local executable token; no PATH/argument search."""
    if not isinstance(value, str) or len(value) > 8192 or any(ord(c) < 32 for c in value):
        raise ValueError('icon_command')
    value = value.strip()
    if value.startswith('"'):
        match = re.match(r'^"([^"\r\n]+\.exe)"(?:\s|$)', value, re.I)
    else:
        match = re.match(r'^([^"\r\n]*?\.exe)(?:\s|$)', value, re.I)
    if not match:
        raise ValueError('icon_executable')
    return parse_icon_location('"' + match[1] + '"')


def _safe_file(path):
    path = _location(path)
    if os.name == 'nt':
        # This check precedes lstat; mapped drives must not initiate network I/O.
        api = _apis()
        if api.kernel.GetDriveTypeW(ntpath.splitdrive(path)[0] + '\\') != 3:
            raise ValueError('icon_nonfixed_drive')
    _checked_root(os.path.dirname(path))
    info = os.lstat(path)
    if _is_reparse(info) or not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_FILE_BYTES:
        raise ValueError('icon_file_boundary')
    return path, _metadata_signature(path, info)


class _Sources:
    """Private registry hints; never put raw commands/paths into item payloads."""
    def __init__(self):
        self._lock = threading.Lock()
        self._software = {}
        self._positions = [0] * 4
        self._finished = set()
        self._expires = 0

    def _software_source(self, item, deadline):
        import winreg
        roots = [(winreg.HKEY_CURRENT_USER, '当前用户'), (winreg.HKEY_LOCAL_MACHINE, '整机')]
        with self._lock:
            if time.monotonic() > self._expires:
                self._software.clear()
                self._positions = [0] * 4
                self._finished.clear()
                self._expires = time.monotonic() + 300
            found = self._software.get(item['id'])
            for group, (hive, label) in enumerate(roots):
                for subindex, parent in enumerate(_UNINSTALL_KEYS):
                    slot = group * 2 + subindex
                    if found or slot in self._finished:
                        continue
                    try:
                        with winreg.OpenKey(hive, parent, 0, winreg.KEY_READ) as key:
                            while len(self._software) < 2000:
                                _deadline(deadline)
                                try:
                                    sub = winreg.EnumKey(key, self._positions[slot])
                                except OSError:
                                    self._finished.add(slot)
                                    break
                                self._positions[slot] += 1
                                ident = 'software-' + digest(json.dumps([label, parent, sub]))[:24]
                                self._software[ident] = (hive, parent + '\\' + sub)
                                if ident == item['id']:
                                    found = self._software[ident]
                                    break
                    except OSError:
                        self._finished.add(slot)
            if not found:
                if len(self._finished) < 4 and len(self._software) < 2000:
                    raise _Later()
                return None
            with winreg.OpenKey(found[0], found[1], 0, winreg.KEY_READ) as key:
                value = winreg.QueryValueEx(key, 'DisplayIcon')[0]
            return parse_icon_location(value)

    def __call__(self, item, deadline):
        if os.name != 'nt' or item.get('details', {}).get('synthetic'):
            return None
        import winreg
        kind, path, details = item.get('kind'), item.get('path', ''), item.get('details', {})
        if kind == 'software':
            return self._software_source(item, deadline)
        if kind == 'process':
            return parse_icon_location('"' + path + '"')
        if kind == 'startup':
            location = details.get('registry_location')
            if not location:
                # No ShellLink COM resolution, MSI advertised target or network lookup.
                return None if path.casefold().endswith('.lnk') else executable_from_command('"' + path + '"')
            hive_name, separator, key_name = location.partition('\\')
            if not separator or hive_name not in ('HKCU', 'HKLM') or key_name not in _RUN_KEYS:
                return None
            name = item.get('name', '')
            if not isinstance(name, str) or len(name) > 256:
                return None
            hive = winreg.HKEY_CURRENT_USER if hive_name == 'HKCU' else winreg.HKEY_LOCAL_MACHINE
            with winreg.OpenKey(hive, key_name, 0, winreg.KEY_READ) as key:
                value = winreg.QueryValueEx(key, name)[0]
            if digest(str(value)) != details.get('command_digest'):
                return None
            return executable_from_command(value)
        if kind == 'service':
            name = details.get('service_name', '')
            if not isinstance(name, str) or not name or len(name) > 256 or any(c in name for c in '\\/\x00'):
                return None
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, 'SYSTEM\\CurrentControlSet\\Services\\' + name, 0, winreg.KEY_READ) as key:
                value = winreg.QueryValueEx(key, 'ImagePath')[0]
            if digest(str(value)) != details.get('image_digest'):
                return None
            return executable_from_command(value)
        if kind == 'directory':
            return dict(type='stock', stock=3, source='system_folder')
        if kind == 'file':
            extension = ntpath.splitext(path)[1].casefold()
            if extension == '.lnk':
                return None
            if extension in ('.exe', '.dll', '.ico', '.icl', '.cpl'):
                return parse_icon_location('"' + path + '"')
            if re.fullmatch(r'\.[a-z0-9_-]{1,16}', extension):
                # Read the static DefaultIcon value only, never shellex handlers.
                with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, extension, 0, winreg.KEY_READ) as key:
                    progid = winreg.QueryValueEx(key, '')[0]
                if isinstance(progid, str) and re.fullmatch(r'[\w .+-]{1,180}', progid):
                    with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid + '\\DefaultIcon', 0, winreg.KEY_READ) as key:
                        value = winreg.QueryValueEx(key, '')[0]
                    result = parse_icon_location(value)
                    result['source'] = 'file_type'
                    return result
            return dict(type='stock', stock=0, source='file_type')
        return None


_API = None
_API_LOCK = threading.Lock()


def _apis():
    global _API
    with _API_LOCK:
        if _API is not None:
            return _API
        if os.name != 'nt':
            raise OSError('Windows only')
        class Api:
            pass
        api = Api()
        api.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        api.user = ctypes.WinDLL('user32', use_last_error=True)
        api.gdi = ctypes.WinDLL('gdi32', use_last_error=True)
        api.shell = ctypes.WinDLL('shell32', use_last_error=True)
        pointer = ctypes.c_void_p
        signatures = [
            (api.kernel, 'GetDriveTypeW', [wt.LPCWSTR], wt.UINT),
            (api.kernel, 'LoadLibraryExW', [wt.LPCWSTR, pointer, wt.DWORD], pointer),
            (api.kernel, 'FreeLibrary', [pointer], wt.BOOL),
            (api.kernel, 'FindResourceW', [pointer, pointer, pointer], pointer),
            (api.kernel, 'SizeofResource', [pointer, pointer], wt.DWORD),
            (api.kernel, 'LoadResource', [pointer, pointer], pointer),
            (api.kernel, 'LockResource', [pointer], pointer),
            (api.kernel, 'EnumResourceNamesW', [pointer, pointer, pointer, ctypes.c_ssize_t], wt.BOOL),
            (api.user, 'LookupIconIdFromDirectoryEx', [pointer, wt.BOOL, ctypes.c_int, ctypes.c_int, wt.UINT], ctypes.c_int),
            (api.user, 'CreateIconFromResourceEx', [pointer, wt.DWORD, wt.BOOL, wt.DWORD, ctypes.c_int, ctypes.c_int, wt.UINT], pointer),
            (api.user, 'DestroyIcon', [pointer], wt.BOOL),
            (api.user, 'DrawIconEx', [pointer, ctypes.c_int, ctypes.c_int, pointer, ctypes.c_int, ctypes.c_int, wt.UINT, pointer, wt.UINT], wt.BOOL),
            (api.gdi, 'CreateCompatibleDC', [pointer], pointer),
            (api.gdi, 'CreateDIBSection', [pointer, pointer, wt.UINT, ctypes.POINTER(pointer), pointer, wt.DWORD], pointer),
            (api.gdi, 'SelectObject', [pointer, pointer], pointer),
            (api.gdi, 'DeleteObject', [pointer], wt.BOOL),
            (api.gdi, 'DeleteDC', [pointer], wt.BOOL),
            (api.shell, 'SHGetStockIconInfo', [ctypes.c_int, wt.UINT, pointer], wt.LONG),
        ]
        for library, name, args, result in signatures:
            method = getattr(library, name)
            method.argtypes, method.restype = args, result
        _API = api
        return api


def _png(rgba):
    if len(rgba) != SIZE * SIZE * 4:
        raise ValueError('icon_dimensions')
    def chunk(name, data):
        return struct.pack('>I', len(data)) + name + data + struct.pack('>I', zlib.crc32(name + data) & 0xffffffff)
    pixels = b''.join(b'\0' + rgba[y * SIZE * 4:(y + 1) * SIZE * 4] for y in range(SIZE))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', SIZE, SIZE, 8, 6, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(pixels)) + chunk(b'IEND', b'')


def _draw(hicon):
    api = _apis()
    class Header(ctypes.Structure):
        _fields_ = [('size', wt.DWORD), ('width', wt.LONG), ('height', wt.LONG), ('planes', wt.WORD),
                    ('bits', wt.WORD), ('compression', wt.DWORD), ('image_size', wt.DWORD),
                    ('x', wt.LONG), ('y', wt.LONG), ('used', wt.DWORD), ('important', wt.DWORD)]
    header = Header(ctypes.sizeof(Header), SIZE, -SIZE, 1, 32, 0, SIZE * SIZE * 4, 0, 0, 0, 0)
    dc = api.gdi.CreateCompatibleDC(None)
    bitmap, previous = None, None
    if not dc:
        raise OSError('icon_dc')
    try:
        address = ctypes.c_void_p()
        bitmap = api.gdi.CreateDIBSection(dc, ctypes.byref(header), 0, ctypes.byref(address), None, 0)
        if not bitmap or not address.value:
            raise OSError('icon_bitmap')
        previous = api.gdi.SelectObject(dc, bitmap)
        passes = []
        for background in (0, 255):
            ctypes.memset(address, background, SIZE * SIZE * 4)
            if not api.user.DrawIconEx(dc, 0, 0, hicon, SIZE, SIZE, 0, None, 3):
                raise OSError('icon_draw')
            passes.append(ctypes.string_at(address, SIZE * SIZE * 4))
        black, white = passes
        rgba = bytearray(SIZE * SIZE * 4)
        for offset in range(0, len(rgba), 4):
            alpha = max(0, min(255, 255 - max(white[offset + c] - black[offset + c] for c in range(3))))
            rgba[offset:offset + 4] = bytes((min(255, round(black[offset + 2] * 255 / alpha)) if alpha else 0,
                                                   min(255, round(black[offset + 1] * 255 / alpha)) if alpha else 0,
                                                   min(255, round(black[offset] * 255 / alpha)) if alpha else 0, alpha))
        return _png(rgba)
    finally:
        if previous:
            api.gdi.SelectObject(dc, previous)
        if bitmap:
            api.gdi.DeleteObject(bitmap)
        api.gdi.DeleteDC(dc)


def _resource_bytes(api, module, name, kind, maximum):
    name_buffer = ctypes.create_unicode_buffer(name) if isinstance(name, str) else None
    name_pointer = ctypes.cast(name_buffer, ctypes.c_void_p) if name_buffer is not None else ctypes.c_void_p(name)
    resource = api.kernel.FindResourceW(module, name_pointer, ctypes.c_void_p(kind))
    size = api.kernel.SizeofResource(module, resource) if resource else 0
    if not 0 < size <= maximum:
        raise ValueError('icon_resource_size')
    data = api.kernel.LockResource(api.kernel.LoadResource(module, resource))
    if not data:
        raise OSError('icon_resource')
    return ctypes.string_at(data, size)


def _extract(source, deadline):
    if os.name != 'nt':
        return None
    api = _apis()
    hicon, module = None, None
    try:
        _deadline(deadline)
        if source['type'] == 'stock':
            class Stock(ctypes.Structure):
                _fields_ = [('cbSize', wt.DWORD), ('hIcon', ctypes.c_void_p), ('system_index', ctypes.c_int),
                            ('icon_index', ctypes.c_int), ('path', wt.WCHAR * 260)]
            stock = Stock()
            stock.cbSize = ctypes.sizeof(Stock)
            if api.shell.SHGetStockIconInfo(source['stock'], 0x100, ctypes.byref(stock)) < 0:
                return None
            hicon = stock.hIcon
        else:
            path, signature = _safe_file(source['path'])
            _deadline(deadline)
            if path.casefold().endswith('.ico'):
                with open(path, 'rb') as handle:
                    data = handle.read(MAX_ICON_BYTES + 1)
                if len(data) > MAX_ICON_BYTES or len(data) < 6:
                    return None
                reserved, kind, count = struct.unpack_from('<HHH', data)
                if reserved or kind != 1 or not 0 < count <= 64 or len(data) < 6 + count * 16:
                    return None
                entries = []
                for number in range(count):
                    width, height, _, _, _, bits, size, offset = struct.unpack_from('<BBBBHHII', data, 6 + number * 16)
                    if 0 < size <= MAX_ICON_BYTES and offset >= 6 + count * 16 and offset + size <= len(data):
                        entries.append((abs((width or 256) - SIZE) + abs((height or 256) - SIZE), -bits, data[offset:offset + size]))
                if not entries:
                    return None
                image = min(entries, key=lambda entry: entry[:2])[2]
            else:
                # DATAFILE_EXCLUSIVE | IMAGE_RESOURCE: no DllMain or imports.
                module = api.kernel.LoadLibraryExW(path, None, 0x40 | 0x20)
                if not module:
                    return None
                index = source.get('index', 0)
                if index < 0:
                    group = -index
                else:
                    names = []
                    callback_type = ctypes.WINFUNCTYPE(wt.BOOL, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ssize_t)
                    def visit(_module, _kind, name, _parameter):
                        if len(names) > index or len(names) >= 128:
                            return False
                        names.append(name if name <= 65535 else ctypes.wstring_at(name, min(256, len(ctypes.wstring_at(name)))))
                        return len(names) <= index
                    callback = callback_type(visit)
                    api.kernel.EnumResourceNamesW(module, ctypes.c_void_p(14), callback, 0)
                    if len(names) <= index:
                        return None
                    group = names[index]
                raw = _resource_bytes(api, module, group, 14, 65536)
                if len(raw) < 6:
                    return None
                reserved, kind, count = struct.unpack_from('<HHH', raw)
                if reserved or kind != 1 or not 0 < count <= 128 or len(raw) < 6 + count * 14:
                    return None
                choices = {struct.unpack_from('<H', raw, 6 + number * 14 + 12)[0] for number in range(count)}
                buffer = ctypes.create_string_buffer(raw)
                ident = api.user.LookupIconIdFromDirectoryEx(buffer, True, SIZE, SIZE, 0)
                if ident not in choices:
                    return None
                image = _resource_bytes(api, module, ident, 3, MAX_ICON_BYTES)
            _deadline(deadline)
            buffer = ctypes.create_string_buffer(image)
            hicon = api.user.CreateIconFromResourceEx(buffer, len(image), True, 0x30000, SIZE, SIZE, 0)
            if _safe_file(path)[1] != signature:
                return None
        if not hicon:
            return None
        _deadline(deadline)
        png = _draw(hicon)
        _deadline(deadline)
        return png if len(png) <= MAX_PNG_BYTES else None
    finally:
        if hicon:
            api.user.DestroyIcon(hicon)
        if module:
            api.kernel.FreeLibrary(module)


def _response(body=FALLBACK_SVG, status='fallback', source='generic'):
    return dict(body=body, mime='image/png' if body.startswith(b'\x89PNG\r\n\x1a\n') else 'image/svg+xml',
                etag='"' + hashlib.sha256(body).hexdigest()[:32] + '"', status=status, source=source,
                retry_after=1 if status == 'pending' else 0)


class IconService:
    def __init__(self, resolver, *, max_cache=256, max_cache_bytes=2097152, workers=2,
                 max_pending=32, wait_ms=40, ttl=300, backend=None, source_resolver=None):
        if not callable(resolver) or not 1 <= max_cache <= 1024 or not 32768 <= max_cache_bytes <= 8 * 1024 * 1024:
            raise ValueError('icon_cache_limits')
        if not 1 <= workers <= 4 or not 1 <= max_pending <= 64 or not 0 <= wait_ms <= 80 or not 1 <= ttl <= 600:
            raise ValueError('icon_work_limits')
        self._resolver, self._backend = resolver, backend or _extract
        self._sources = source_resolver or _Sources()
        self._max_cache, self._max_bytes, self._wait, self._ttl = max_cache, max_cache_bytes, wait_ms / 1000, ttl
        self._cache, self._bytes, self._pending = OrderedDict(), 0, {}
        self._queue = queue.Queue(max_pending)
        self._lock, self._stop = threading.Lock(), threading.Event()
        self._workers, self._threads = workers, []

    def get(self, item_id):
        if not isinstance(item_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,127}', item_id):
            raise IconNotFound('unknown_item')
        item = self._resolver(item_id)
        if not isinstance(item, dict) or item.get('id') != item_id:
            raise IconNotFound('unknown_item')
        if self._stop.is_set():
            return _response()
        # Keep only the private resolver fields actually needed by icon lookup.
        details = item.get('details') or {}
        safe = {key: item.get(key, '') for key in ('id', 'kind', 'path', 'signature', 'name')}
        safe['details'] = {key: details[key] for key in ('synthetic', 'registry_location', 'command_digest', 'service_name', 'image_digest') if key in details}
        key = hashlib.sha256(json.dumps(safe, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self._lock:
            cached = self._cache.get(key)
            if cached and cached[0] > time.monotonic():
                self._cache.move_to_end(key)
                return dict(cached[1])
            event = self._pending.get(key)
            if event is None:
                if self._queue.full():
                    return _response()
                if not self._threads:
                    self._threads = [threading.Thread(target=self._worker, daemon=True, name='PanXu icon') for _ in range(self._workers)]
                    for thread in self._threads:
                        thread.start()
                event = threading.Event()
                self._pending[key] = event
                self._queue.put_nowait((key, safe, event))
        event.wait(self._wait)
        with self._lock:
            cached = self._cache.get(key)
            return dict(cached[1]) if cached else _response(status='pending')

    def _worker(self):
        while not self._stop.is_set():
            try:
                key, item, event = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            response, retry = _response(), False
            try:
                deadline = time.monotonic() + 0.35
                source = self._sources(item, deadline)
                if source:
                    _deadline(deadline)
                    body = self._backend(source, deadline)
                    if body and len(body) <= MAX_PNG_BYTES and body[:8] == b'\x89PNG\r\n\x1a\n' and len(body) >= 24 and struct.unpack_from('>II', body, 16) == (SIZE, SIZE):
                        response = _response(body, 'ready', source.get('source', 'generic'))
            except (_Later, TimeoutError):
                retry = True
            except Exception:
                # No commands, paths, icon bytes or provider/user data are logged.
                pass
            finally:
                with self._lock:
                    if not retry and not self._stop.is_set():
                        old = self._cache.pop(key, None)
                        self._bytes -= len(old[1]['body']) if old else 0
                        self._cache[key] = (time.monotonic() + (self._ttl if response['status'] == 'ready' else min(self._ttl, 30)), response)
                        self._bytes += len(response['body'])
                        while len(self._cache) > self._max_cache or self._bytes > self._max_bytes:
                            _, removed = self._cache.popitem(last=False)
                            self._bytes -= len(removed[1]['body'])
                    self._pending.pop(key, None)
                    event.set()
                self._queue.task_done()

    def close(self):
        self._stop.set()
        with self._lock:
            for event in self._pending.values():
                event.set()
            self._pending.clear()
            self._cache.clear()
            self._bytes = 0
        for thread in self._threads:
            thread.join(timeout=0.1)

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass
