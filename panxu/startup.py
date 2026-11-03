"""Startup items: list and enable/disable programs that run at sign-in.

Enable/disable uses the same per-item approval flags as Task Manager's
Startup page, so both tools always agree and the original Run entry or
shortcut is never deleted (fully reversible).
"""
from __future__ import annotations

import ntpath
import os
import re
import struct
import time

from . import paths, winapi

RUN = r'Software\Microsoft\Windows\CurrentVersion\Run'
RUN32 = r'Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run'
APPROVED = r'Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved'
HKCU, HKLM = 'HKCU', 'HKLM'

# (source id, hive, Run key or folder template, approval key, label, needs admin)
SOURCES = [
    ('hkcu_run', HKCU, RUN, APPROVED + r'\Run', '注册表（当前用户）', False),
    ('hklm_run', HKLM, RUN, APPROVED + r'\Run', '注册表（所有用户）', True),
    ('hklm_run32', HKLM, RUN32, APPROVED + r'\Run32', '注册表（所有用户，32 位）', True),
    ('user_folder', HKCU, r'%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup', APPROVED + r'\StartupFolder',
     '启动文件夹（当前用户）', False),
    ('common_folder', HKLM, r'%ProgramData%\Microsoft\Windows\Start Menu\Programs\StartUp', APPROVED + r'\StartupFolder',
     '启动文件夹（所有用户）', True),
]
SOURCE_BY_ID = {s[0]: s for s in SOURCES}

_EPOCH_DIFF = 11644473600  # seconds between 1601-01-01 and 1970-01-01


def approval_enabled(value: bytes | None) -> bool:
    """Missing flag = enabled; otherwise the low bit of the first byte marks 'disabled'."""
    if not value:
        return True
    return not (value[0] & 1)


def approval_value(enabled: bool, now: float | None = None) -> bytes:
    if enabled:
        return bytes([2]) + bytes(11)
    filetime = int(((time.time() if now is None else now) + _EPOCH_DIFF) * 10_000_000)
    return bytes([3, 0, 0, 0]) + struct.pack('<Q', filetime)


class WinRegistry:
    """Minimal registry facade (replaced by a fake in tests)."""

    def __init__(self):
        import winreg
        self.winreg = winreg
        self.hives = {HKCU: winreg.HKEY_CURRENT_USER, HKLM: winreg.HKEY_LOCAL_MACHINE}

    def values(self, hive: str, path: str) -> list[tuple[str, object]]:
        wr = self.winreg
        try:
            with wr.OpenKey(self.hives[hive], path, 0, wr.KEY_READ | wr.KEY_WOW64_64KEY) as key:
                out, i = [], 0
                while True:
                    try:
                        name, value, _ = wr.EnumValue(key, i)
                    except OSError:
                        return out
                    out.append((name, value))
                    i += 1
        except OSError:
            return []

    def get(self, hive: str, path: str, name: str):
        wr = self.winreg
        try:
            with wr.OpenKey(self.hives[hive], path, 0, wr.KEY_READ | wr.KEY_WOW64_64KEY) as key:
                return wr.QueryValueEx(key, name)[0]
        except OSError:
            return None

    def set_binary(self, hive: str, path: str, name: str, data: bytes):
        wr = self.winreg
        with wr.CreateKeyEx(self.hives[hive], path, 0, wr.KEY_SET_VALUE | wr.KEY_WOW64_64KEY) as key:
            wr.SetValueEx(key, name, 0, wr.REG_BINARY, data)

    def subkeys(self, hive: str, path: str) -> list[str]:
        wr = self.winreg
        try:
            with wr.OpenKey(self.hives[hive], path, 0, wr.KEY_READ | wr.KEY_WOW64_64KEY) as key:
                out, i = [], 0
                while True:
                    try:
                        out.append(wr.EnumKey(key, i))
                    except OSError:
                        return out
                    i += 1
        except OSError:
            return []


_VAR = re.compile(r'%([^%\s]+)%')


def expand_vars(text: str, env: dict) -> str:
    """Expand %VAR% (case-insensitive) without touching spacing or slashes."""
    lookup = {k.lower(): v for k, v in os.environ.items()}
    lookup.update({k.lower(): v for k, v in env.items()})
    if env.get('SystemRoot'):
        lookup['windir'] = env['SystemRoot']
    return _VAR.sub(lambda m: lookup.get(m.group(1).lower(), m.group(0)), text)


def split_command(command: str, env: dict | None = None) -> tuple[str, str]:
    """(executable, arguments) from a Run-key command line."""
    text = expand_vars((command or '').strip(), env or paths.environment())
    if text.startswith('"'):
        end = text.find('"', 1)
        if end > 0:
            return text[1:end], text[end + 1:].strip()
        return text.strip('"'), ''
    parts = text.split(' ')
    exts = ('.exe', '.cmd', '.bat', '.com', '.vbs', '.lnk', '.scr')
    for i in range(len(parts), 0, -1):  # longest existing path wins ("C:\Program Files\X\x.exe -a")
        candidate = ' '.join(parts[:i])
        if candidate.lower().endswith(exts) and os.path.isfile(candidate):
            return candidate, ' '.join(parts[i:])
    for i in range(1, len(parts) + 1):
        candidate = ' '.join(parts[:i])
        if candidate.lower().endswith(exts):
            return candidate, ' '.join(parts[i:])
    return parts[0], ' '.join(parts[1:])


def resolve_executable(exe: str, env: dict) -> str:
    if not exe or ntpath.isabs(exe):
        return exe
    system32 = ntpath.join(env.get('SystemRoot', 'C:\\Windows'), 'System32', exe)
    return system32 if os.path.isfile(system32) else exe


def parse_lnk(data: bytes) -> dict:
    """Extract target path and arguments from a .lnk (MS-SHLLINK), best effort."""
    out = {'target': '', 'arguments': ''}
    if len(data) < 0x4C or struct.unpack_from('<I', data, 0)[0] != 0x4C:
        return out
    flags = struct.unpack_from('<I', data, 20)[0]
    unicode = bool(flags & 0x80)
    pos = 0x4C
    try:
        if flags & 0x1:  # HasLinkTargetIDList
            pos += 2 + struct.unpack_from('<H', data, pos)[0]
        if flags & 0x2:  # HasLinkInfo
            info = pos
            size, header, info_flags, _vol, base_off, _net, suffix_off = struct.unpack_from('<7I', data, info)
            target = ''
            if info_flags & 0x1:
                if header >= 0x24:
                    base_u = struct.unpack_from('<I', data, info + 28)[0]
                    if base_u:
                        target = _wstr(data, info + base_u)
                if not target:
                    target = _astr(data, info + base_off)
                suffix = _astr(data, info + suffix_off) if suffix_off else ''
                if suffix:
                    target = ntpath.join(target, suffix)
            out['target'] = target
            pos += size
        for bit, key in ((0x4, 'name'), (0x8, 'relative'), (0x10, 'workdir'), (0x20, 'arguments'), (0x40, 'icon')):
            if flags & bit:
                count = struct.unpack_from('<H', data, pos)[0]
                pos += 2
                width = 2 if unicode else 1
                raw = data[pos:pos + count * width]
                pos += count * width
                out[key] = raw.decode('utf-16-le' if unicode else 'mbcs' if winapi.IS_WINDOWS else 'latin-1', 'replace')
    except (struct.error, IndexError):
        pass
    return out


def _wstr(data: bytes, pos: int) -> str:
    end = pos
    while end + 1 < len(data) and data[end:end + 2] != b'\0\0':
        end += 2
    return data[pos:end].decode('utf-16-le', 'replace')


def _astr(data: bytes, pos: int) -> str:
    end = data.find(b'\0', pos)
    raw = data[pos:end if end >= 0 else len(data)]
    for enc in (('mbcs',) if winapi.IS_WINDOWS else ()) + ('utf-8', 'gbk', 'latin-1'):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return ''


class StartupManager:
    def __init__(self, registry=None, env: dict | None = None, admin: bool | None = None, oplog=None,
                 hkcu_ok: bool = True):
        self.registry = registry
        self.env = env or paths.environment()
        self.admin = winapi.is_admin() if admin is None else admin
        self.oplog = oplog
        # False when elevated as another account: HKCU would be the wrong user's hive
        self.hkcu_ok = hkcu_ok

    def _reg(self):
        if self.registry is None:
            self.registry = WinRegistry()
        return self.registry

    def list(self) -> list[dict]:
        reg = self._reg()
        items = []
        for source_id, hive, location, approved, label, needs_admin in SOURCES:
            flags = {name: value for name, value in reg.values(hive, approved)}
            if location.startswith('%'):
                folder = paths.expand(location, self.env)
                folder = paths.native(folder) if folder else None
                entries = []
                if folder and os.path.isdir(folder):
                    for name in sorted(os.listdir(folder)):
                        if name.lower() == 'desktop.ini':
                            continue
                        full = os.path.join(folder, name)
                        if os.path.isfile(full):
                            entries.append((name, full))
                for name, full in entries:
                    target, args = full, ''
                    if name.lower().endswith('.lnk'):
                        try:
                            with open(full, 'rb') as fh:
                                lnk = parse_lnk(fh.read(65536))
                            target, args = lnk['target'] or full, lnk.get('arguments', '')
                        except OSError:
                            pass
                    items.append(self._item(source_id, label, needs_admin, name, f'"{target}" {args}'.strip(),
                                            target, flags.get(name), location=full))
            else:
                for name, value in reg.values(hive, location):
                    if not isinstance(value, str) or not name:
                        continue
                    exe, _ = split_command(value, self.env)
                    exe = resolve_executable(exe, self.env)
                    items.append(self._item(source_id, label, needs_admin, name, value, exe, flags.get(name)))
        return items

    def _locked(self, source_id: str) -> bool:
        source = SOURCE_BY_ID[source_id]
        return (source[5] and not self.admin) or (source[1] == HKCU and not self.hkcu_ok)

    def _item(self, source_id, label, needs_admin, name, command, exe, flag, location=None) -> dict:
        info = winapi.file_version_info(exe) if exe and os.path.isfile(exe) else {}
        display = info.get('FileDescription') or ntpath.splitext(name)[0]
        return {
            'id': f'{source_id}:{name}', 'source': source_id, 'source_label': label, 'name': name,
            'display_name': display, 'command': command, 'exe': exe, 'exists': bool(exe and os.path.exists(exe)),
            'publisher': info.get('CompanyName', ''), 'enabled': approval_enabled(flag if isinstance(flag, bytes) else None),
            'needs_admin': self._locked(source_id), 'location': location or exe,
        }

    def set_enabled(self, item_id: str, enabled: bool, log: bool = True) -> dict:
        source_id, _, name = item_id.partition(':')
        source = SOURCE_BY_ID.get(source_id)
        if source is None or not name:
            raise ValueError('未知的启动项')
        _, hive, _, approved, _, needs_admin = source
        current = next((i for i in self.list() if i['id'] == item_id), None)
        if current is None:
            raise ValueError('启动项已不存在，请刷新列表')
        if needs_admin and not self.admin:
            raise PermissionError('修改「所有用户」的启动项需要管理员权限')
        if hive == HKCU and not self.hkcu_ok:
            raise PermissionError('现在是以另一个管理员账户运行，无法修改原账户的启动项。请用原账户直接以管理员身份运行盘序')
        self._reg().set_binary(hive, approved, name, approval_value(enabled))
        if log and self.oplog is not None and current['enabled'] != enabled:
            self.oplog.add('startup', ('启用' if enabled else '禁用') + '开机启动：' + current['display_name'],
                           items=[{'id': item_id, 'name': current['display_name'], 'command': current['command']}],
                           revert={'action': 'startup', 'id': item_id, 'enabled': current['enabled']})
        return {**current, 'enabled': enabled}
