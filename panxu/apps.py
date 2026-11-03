"""Installed programs (the same list as 「设置 > 应用 > 已安装的应用」 for desktop programs).

Read from the Uninstall registry keys. Besides showing the list, it answers
two questions other modules need:

* does an AppData / ProgramData folder belong to a program that is still
  installed? (``match_folder`` — leftovers of uninstalled programs are a common
  source of wasted space), and
* is an installer in Downloads for a program that is already installed?
  (``match_index``, used by insights).

Uninstalling always runs the program's *own* uninstaller (it shows its own
window and asks for elevation itself); PanXu never deletes program folders.
"""
from __future__ import annotations

import ntpath
import os
import re
import time

from . import paths, winapi
from .insights import compact, tokens
from .startup import HKCU, HKLM, WinRegistry, split_command

UNINSTALL_KEYS = [
    (HKLM, r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall', '所有用户'),
    (HKLM, r'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall', '所有用户（32 位）'),
    (HKCU, r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall', '当前用户'),
]
_RUNTIME = re.compile(r'redistributable|runtime|\.net |\.net$|sdk\b|运行库|webview2|directx|visual c\+\+|vc\+\+|framework', re.I)
KIND_LABELS = {'app': '应用', 'runtime': '运行库', 'driver': '驱动', 'game': '游戏'}
_DRIVER = re.compile(r'driver|驱动|chipset|芯片组', re.I)
# generic folder names that must never be matched to a program by name alone
_GENERIC = {'cache', 'caches', 'temp', 'tmp', 'logs', 'log', 'data', 'config', 'common', 'shared', 'microsoft',
            'windows', 'programs', 'packages', 'local', 'roaming', 'user', 'users', 'default', 'crashdumps', 'updater'}


def _date(text) -> float | None:
    text = str(text or '').strip()
    if re.fullmatch(r'\d{8}', text):
        try:
            return time.mktime(time.strptime(text, '%Y%m%d'))
        except (ValueError, OverflowError):
            return None
    return None


class AppManager:
    def __init__(self, registry=None, env: dict | None = None, admin: bool | None = None, oplog=None):
        self.registry = registry
        self.env = env or paths.environment()
        self.admin = winapi.is_admin() if admin is None else admin
        self.oplog = oplog
        self._cache: tuple[float, list] | None = None

    def _reg(self):
        if self.registry is None:
            self.registry = WinRegistry()
        return self.registry

    # -- listing -------------------------------------------------------------------
    def list(self, fresh: bool = False) -> list[dict]:
        if self._cache and not fresh and time.time() - self._cache[0] < 30:
            return [dict(a) for a in self._cache[1]]
        reg = self._reg()
        prefetch = self._prefetch()
        out, seen = [], set()
        for hive, base, scope in UNINSTALL_KEYS:
            for sub in reg.subkeys(hive, base):
                values = dict(reg.values(hive, base + '\\' + sub))
                name = str(values.get('DisplayName') or '').strip()
                if not name or values.get('SystemComponent') in (1, '1') or values.get('ParentKeyName') \
                        or str(values.get('ReleaseType') or '').lower() in ('update', 'hotfix', 'security update'):
                    continue
                dedupe = (name.lower(), str(values.get('DisplayVersion') or ''))
                if dedupe in seen:
                    continue
                seen.add(dedupe)
                location = str(values.get('InstallLocation') or '').strip().strip('"')
                uninstall = str(values.get('QuietUninstallString') and values.get('UninstallString') or
                                values.get('UninstallString') or '').strip()
                est = values.get('EstimatedSize')
                try:
                    size = int(est) * 1024 if est not in (None, '') else None
                except (TypeError, ValueError):
                    size = None
                exists = bool(location) and os.path.isdir(location)
                last_used = self._last_used(location, values, prefetch) if exists else None
                kind = 'runtime' if _RUNTIME.search(name) else 'driver' if _DRIVER.search(name) else \
                    'game' if sub.startswith('Steam App') or 'steamapps' in location.lower() else 'app'
                out.append({
                    'id': f'{hive}|{base}|{sub}', 'name': name, 'version': str(values.get('DisplayVersion') or ''),
                    'publisher': str(values.get('Publisher') or ''), 'installed': _date(values.get('InstallDate')),
                    'location': location, 'location_exists': exists, 'size': size,
                    'size_source': 'estimated' if size is not None else '', 'scope': scope, 'kind': kind,
                    'kind_label': KIND_LABELS[kind],
                    'can_uninstall': bool(uninstall), 'msi': bool(values.get('WindowsInstaller')),
                    'last_used': last_used, 'usage_known': prefetch is not None,
                    'needs_admin': hive == HKLM and not self.admin, 'registry_key': sub,
                })
        out.sort(key=lambda a: a['name'].lower())
        self._cache = (time.time(), out)
        return [dict(a) for a in out]

    def _prefetch(self) -> dict[str, float] | None:
        """EXE name (upper case) -> last run time, from Windows Prefetch (readable by administrators)."""
        folder = paths.expand('%SystemRoot%\\Prefetch', self.env)
        folder = paths.native(folder) if folder else None
        if not folder:
            return None
        try:
            names = os.listdir(folder)
        except OSError:
            return None
        out: dict[str, float] = {}
        for name in names:
            if not name.lower().endswith('.pf') or '-' not in name:
                continue
            exe = name.rsplit('-', 1)[0].upper()
            try:
                t = os.stat(os.path.join(folder, name)).st_mtime
            except OSError:
                continue
            out[exe] = max(out.get(exe, 0), t)
        return out

    def _last_used(self, location: str, values: dict, prefetch: dict | None) -> float | None:
        if not prefetch:
            return None
        exes = set()
        icon = str(values.get('DisplayIcon') or '').split(',')[0].strip('"')
        if icon.lower().endswith('.exe'):
            exes.add(ntpath.basename(icon).upper())
        count = 0
        for dirpath, dirnames, filenames in os.walk(location):
            for f in filenames:
                if f.lower().endswith('.exe') and not re.match(r'(unins|uninst|setup|update|crash|helper)', f.lower()):
                    exes.add(f.upper())
            count += 1
            if count > 60 or dirpath.count(os.sep) - location.count(os.sep) >= 2:
                dirnames[:] = []
        times = [prefetch[e] for e in exes if e in prefetch]
        return max(times) if times else None

    def get(self, app_id: str) -> dict | None:
        return next((a for a in self.list() if a['id'] == app_id), None)

    def measure(self, app_id: str) -> dict:
        from .fsops import path_size
        app = self.get(app_id)
        if app is None:
            raise ValueError('程序已不存在，请刷新列表')
        if not app['location_exists']:
            raise ValueError('没有找到这个程序的安装文件夹')
        size, count = path_size(app['location'])
        if self._cache:
            for a in self._cache[1]:
                if a['id'] == app_id:
                    a['size'], a['size_source'] = size, 'measured'
        return {'id': app_id, 'size': size, 'files': count}

    # -- matching ------------------------------------------------------------------
    def match_index(self) -> list[dict]:
        """[{'name', 'version', 'publisher', 'keys': [compact names]}] for fuzzy name matching."""
        rows = []
        for a in self.list():
            keys = {compact(a['name']), compact(a['registry_key'])}
            words = tokens(a['name'])
            if words:
                keys.add(words[0])
            if a['location']:
                parts = [p for p in re.split(r'[\\/]', a['location']) if p]
                for p in parts[-3:]:
                    c = compact(p)
                    if c not in _GENERIC and c not in ('application', 'bin', 'programfiles', 'programfilesx86'):
                        keys.add(c)
            keys = {k for k in keys if k and len(k) >= 2 and k not in _GENERIC}
            rows.append({'name': a['name'], 'version': a['version'], 'publisher': a['publisher'], 'keys': sorted(keys),
                         'publisher_key': compact(a['publisher'])})
        return rows

    def match_folder(self, folder_name: str, index: list[dict] | None = None) -> dict | None:
        """The installed program an AppData/ProgramData folder most likely belongs to."""
        name = compact(folder_name)
        if len(name) < 3 or name in _GENERIC:
            return None
        index = index if index is not None else self.match_index()
        for app in index:
            if name in app['keys'] or (app['publisher_key'] and name == app['publisher_key']):
                return app
        for app in index:
            for k in app['keys']:
                if len(k) >= 5 and len(name) >= 5 and (k.startswith(name) or name.startswith(k)):
                    return app
        return None

    # -- uninstall -----------------------------------------------------------------
    def uninstall(self, app_id: str) -> dict:
        app = self.get(app_id)
        if app is None:
            raise ValueError('程序已不存在，请刷新列表')
        hive, base, sub = app_id.split('|', 2)
        sim = winapi.simulation()
        if sim is not None:
            result = sim.uninstall(hive, base + '\\' + sub)
            self._cache = None
            self._log(app, '模拟卸载完成（程序文件夹已删除，应用数据保留）')
            return {'started': True, 'simulated': True, **result}
        values = dict(self._reg().values(hive, base + '\\' + sub))
        command = str(values.get('UninstallString') or '').strip()
        if not command:
            raise ValueError('这个程序没有提供卸载程序，请在「设置 > 应用」中卸载')
        if command.lower().startswith('"steam://') or command.lower().startswith('steam://'):
            winapi.open_uri(command.strip('"'))
            started = True
        else:
            exe, args = split_command(command, self.env)
            if ntpath.basename(exe).lower() == 'msiexec.exe':
                args = re.sub(r'/I(\{[0-9A-Fa-f-]+\})', r'/X\1', args)  # /I opens "modify", /X uninstalls
            started = winapi.shell_execute(exe, args)
        if not started:
            raise ValueError('没能启动卸载程序（可能在系统提示中选择了「否」）')
        self._cache = None
        self._log(app, '已启动卸载程序')
        return {'started': True, 'simulated': False}

    def _log(self, app: dict, note: str):
        if self.oplog is not None:
            self.oplog.add('uninstall', f'卸载：{app["name"]}', items=[{'name': app['name'], 'version': app['version'],
                                                                         'publisher': app['publisher'], 'note': note,
                                                                         'location': app['location']}])
