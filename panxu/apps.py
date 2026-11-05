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
from .startup import HKCU, HKLM, WinRegistry, expand_vars, split_command

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
# programs that run without the user opening the program (updaters, crash reporters, tray and sync helpers,
# services, embedded browsers): their Prefetch time says nothing about when the program was last used
_HELPER_START = re.compile(r'(unins|uninst|setup|update|crash|helper|install)')
_HELPER_ANY = re.compile(r'helper|updater|crash|report|broker|sniffer|collabsync|notification|elevat|cefsharp|webview|'
                         r'setup|install|卸载|安装|更新|升级')
_HELPER_END = ('tray', 'svc', 'service', 'agent', 'daemon')
_VERSION_DIR = re.compile(r'^\[?v?\d+(\.\d+)+\]?$', re.I)
# interpreters and runtimes many programs ship: by name alone they could be anyone's
_SHARED_EXES = {'node', 'python', 'pythonw', 'java', 'javaw', 'git', 'bash', 'sh', 'cmd', 'powershell', 'pwsh', 'electron',
                'qtwebengineprocess', 'crashpad_handler', 'conhost', 'rundll32', 'msiexec'}


def _helper_exe(filename: str) -> bool:
    stem = os.path.splitext(filename)[0]
    low = stem.lower()
    if _HELPER_START.match(low) or _HELPER_ANY.search(low):
        return True
    bare = re.sub(r'[\s_.-]*(x64|x86|64|32)$', '', stem)  # HuaweiHiSuiteService64
    m = re.search('(' + '|'.join(_HELPER_END) + ')$', bare.lower())
    if not m:
        return False
    # a word of its own: AcroTray, FooSoftSvc, Hi-Suite_Service, or steamservice -- but not the game Stray
    i = m.start()
    return i == 0 or bare[i - 1] in ' _.-' or bare[i].isupper() or (bare.islower() and m.group(1) != 'tray')


def _resembles(stem: str, name: str, publisher: str = '') -> bool:
    """Is an exe named like the program? ("Adobe Premiere Pro.exe" / "Adobe Premiere Pro 2023", AfterFX / After Effects)"""
    s, n = compact(stem), compact(name)
    if len(s) >= 3 and n and (s in n or n in s):
        return True
    vendor = set(tokens(publisher))
    return any(len(t) >= 4 and t not in vendor and s.startswith(t) for t in tokens(name))


def _named_inside(folder: str, names: set[str], depth: int = 3) -> str | None:
    """The first sub-folder (a few levels down) whose name is one of ``names``, e.g. 'Saves'."""
    stack = [(folder, 0)]
    while stack:
        current, d = stack.pop()
        try:
            entries = [e for e in os.scandir(current) if e.is_dir(follow_symlinks=False)]
        except OSError:
            continue
        for e in entries:
            if e.name.lower() in names:
                return e.name
            if d + 1 < depth:
                stack.append((e.path, d + 1))
    return None


def _words(text: str) -> set[str]:
    return {re.sub(r'\d+$', '', t) for t in tokens(text)} - {''}


def _word_fit(stem: str, name: str, publisher: str = '') -> tuple[int, int] | None:
    """How well an exe's words fit the program name: (shared words, -extra words), or None.
    ACDSeeUltimate2019 fits "ACDSee Photo Studio Ultimate 2019" better than ACDSeeIndexerUltimate12."""
    vendor = _words(publisher)
    want, have = _words(name) - vendor, _words(stem)
    shared = want & have
    if not any(len(t) >= 4 for t in shared):
        return None
    return len(shared), -len(have - want - vendor)


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
        self._measured: dict[str, int] = {}
        self._removed: dict[str, dict] = {}  # what a program looked like when its uninstaller was started

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
        entries, seen = [], set()
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
                entries.append((hive, base, scope, sub, values, name))
        # several programs may register one parent folder (Adobe: C:\Program Files\Adobe for eight of them)
        registered: dict[str, int] = {}
        for *_, values, _ in entries:
            loc = str(values.get('InstallLocation') or '').strip().strip('"')
            if loc:
                registered[paths.key(loc)] = registered.get(paths.key(loc), 0) + 1
        out = []
        for hive, base, scope, sub, values, name in entries:
            location, own = self._own_folder(values, name, registered)
            uninstall =str(values.get('QuietUninstallString') and values.get('UninstallString') or
                            values.get('UninstallString') or '').strip()
            est = values.get('EstimatedSize')
            try:
                size = int(est) * 1024 if est not in (None, '') else None
            except (TypeError, ValueError):
                size = None
            exists = bool(location) and os.path.isdir(location)
            last_used = self._last_used(own, values, prefetch, name)
            app_id = f'{hive}|{base}|{sub}'
            if app_id in self._measured:  # 「计算大小」 survives 「刷新」
                size = self._measured[app_id]
            icon, icon_index = self._icon_source({**values, '_key': sub}, own, name)
            kind = 'runtime' if _RUNTIME.search(name) else 'driver' if _DRIVER.search(name) else \
                'game' if sub.startswith('Steam App') or 'steamapps' in location.lower() else 'app'
            out.append({
                'id': app_id, 'name': name, 'version': str(values.get('DisplayVersion') or ''),
                'publisher': str(values.get('Publisher') or ''), 'installed': _date(values.get('InstallDate')),
                'location': location, 'location_exists': exists, 'size': size,
                'size_source': 'measured' if app_id in self._measured else 'estimated' if size is not None else '',
                'scope': scope, 'kind': kind, 'icon': icon, 'icon_index': icon_index,
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

    def _icon_source(self, values: dict, location: str, name: str) -> tuple[str, int]:
        """(file, index) of the program's icon: DisplayIcon ('C:\\x\\app.exe,0', 'icon.ico'), else its main exe."""
        text = expand_vars(str(values.get('DisplayIcon') or '').strip(), self.env)
        index = 0
        m = re.match(r'^"?(.+?)"?\s*,\s*(-?\d+)\s*$', text)
        if m:
            text, index = m.group(1), int(m.group(2))
        text = text.strip().strip('"')
        if text and os.path.isfile(text):
            return text, index  # even "uninstallerIcon.ico" / uninstall.exe usually carry the product icon
        # MSI packages keep their icon next to the cached package: %SystemRoot%\Installer\{ProductCode}
        sub = str(values.get('_key') or '')
        if re.fullmatch(r'\{[0-9A-Fa-f-]{36}\}', sub) and self.env.get('SystemRoot'):
            cached = os.path.join(self.env['SystemRoot'], 'Installer', sub)
            try:
                files = sorted(f for f in os.listdir(cached) if f.lower().endswith(('.ico', '.exe')))
            except OSError:
                files = []
            if files:
                files.sort(key=lambda f: not f.lower().endswith('.ico'))
                return os.path.join(cached, files[0]), 0
        if not location:
            return '', 0
        # the main program: an exe named like the program, at most two folders down
        want = compact(name)
        best, best_score = '', -1
        top = location.rstrip('\\/').count(os.sep)
        seen = 0
        for dirpath, dirnames, filenames in os.walk(location):
            depth = dirpath.rstrip('\\/').count(os.sep) - top
            seen += 1
            if depth >= 2 or seen > 80:
                dirnames[:] = []
            for f in filenames:
                low = f.lower()
                if not low.endswith('.exe') or re.match(r'(unins|uninst|setup|update|crash|helper|install)', low):
                    continue
                stem = compact(os.path.splitext(f)[0])
                score = (2 if stem and (stem in want or want in stem) else 1) * 10 - depth
                if score > best_score:
                    best, best_score = os.path.join(dirpath, f), score
        return best, 0

    def _own_folder(self, values: dict, name: str, registered: dict[str, int]) -> tuple[str, str]:
        """(location to show and measure, the program's own folder for finding its exes; '' if unknown).

        InstallLocation is often missing, and several programs may register one parent folder; then the
        program's own folder is a sub-folder named like it, or the folder of its icon or uninstaller."""
        location = str(values.get('InstallLocation') or '').strip().strip('"')
        if location and os.path.isdir(location):
            if registered.get(paths.key(location), 0) <= 1:
                return location, location
            # shared: C:\Program Files\Adobe -> C:\Program Files\Adobe\Adobe Premiere Pro 2023
            child = self._child_named(location, name, values)
            return (child, child) if child else (location, '')
        icon = expand_vars(str(values.get('DisplayIcon') or '').split(',')[0].strip().strip('"'), self.env)
        uninstaller = split_command(str(values.get('UninstallString') or ''), self.env)[0]
        for source in (icon if icon.lower().endswith('.exe') else '', uninstaller):
            folder = self._program_folder(source)
            if folder:
                return folder, folder
        return location, ''

    def _child_named(self, parent: str, name: str, values: dict) -> str:
        try:
            children = [e.name for e in os.scandir(parent) if e.is_dir(follow_symlinks=False)]
        except OSError:
            return ''
        exact = [c for c in children if c.lower() == name.lower()]
        if exact:
            return os.path.join(parent, exact[0])
        icon = expand_vars(str(values.get('DisplayIcon') or '').split(',')[0].strip().strip('"'), self.env)
        if icon and paths.is_within(icon, parent) and paths.key(icon) != paths.key(parent):
            first = paths.key(icon)[len(paths.key(parent)):].strip('\\').split('\\')[0]
            match = [c for c in children if c.lower() == first]
            if match and os.path.isdir(os.path.join(parent, match[0])):
                return os.path.join(parent, match[0])
        named = [c for c in children if _resembles(c, name) and compact(c) not in _GENERIC]
        return os.path.join(parent, named[0]) if len(named) == 1 else ''

    def _program_folder(self, file: str) -> str:
        """The folder of a program's icon or uninstaller, if it is a program's own folder (not System32, the
        installer caches, Common Files or a top-level folder such as Program Files)."""
        if not file or not os.path.isfile(file):
            return ''
        folder = os.path.dirname(file)
        if _VERSION_DIR.match(os.path.basename(folder)):  # OneDrive\23.071.0402.0001\OneDriveSetup.exe
            folder = os.path.dirname(folder)
        k = paths.key(folder)
        if any(w in k for w in ('\\package cache', '\\installer', '\\common files', '\\windowsapps')):
            return ''
        system = paths.expand('%SystemRoot%', self.env)
        if system and paths.is_within(folder, system):
            return ''
        tops = {paths.key(v) for v in (paths.expand(f'%{n}%', self.env) for n in (
            'ProgramFiles', 'ProgramFiles(x86)', 'ProgramData', 'LOCALAPPDATA', 'APPDATA', 'USERPROFILE', 'SystemDrive'))
            if v}
        if k in tops or k in {paths.key(t + '\\Programs') for t in tops} or len(k.rstrip('\\')) <= 3:
            return ''
        return folder

    def _last_used(self, folder: str, values: dict, prefetch: dict | None, name: str = '') -> float | None:
        """When the program last ran: Prefetch times of its main exes. Helpers that run on their own (updaters,
        tray and sync helpers, services) and runtimes shipped by many programs (node.exe) do not count."""
        if not prefetch:
            return None
        exes = set()
        icon = expand_vars(str(values.get('DisplayIcon') or '').split(',')[0].strip().strip('"'), self.env)
        if icon.lower().endswith('.exe') and not _helper_exe(ntpath.basename(icon)):
            exes.add(ntpath.basename(icon).upper())
        found: list[tuple[int, str]] = []
        count = 0
        top = folder.rstrip('\\/').count(os.sep)
        # three folders down: Illustrator keeps its exe in Support Files\Contents\Windows
        for dirpath, dirnames, filenames in (os.walk(folder) if folder else ()):
            depth = dirpath.rstrip('\\/').count(os.sep) - top
            found += [(depth, f) for f in filenames if f.lower().endswith('.exe') and not _helper_exe(f)]
            count += 1
            if count > 150 or depth >= 3:
                dirnames[:] = []
        publisher = str(values.get('Publisher') or '')
        named = [f for _, f in found if _resembles(os.path.splitext(f)[0], name, publisher)]
        if not named:  # no exe named like the program: the ones whose words fit its name best
            fits = [(fit, f) for fit, f in ((_word_fit(os.path.splitext(f)[0], name, publisher), f) for _, f in found) if fit]
            named = [f for fit, f in fits if fit == max(x for x, _ in fits)] if fits else []
        # programs with an exe named like them: only that one; otherwise the exes near the top of their folder
        exes.update(f.upper() for f in named or [f for d, f in found
                                                 if d <= 1 and os.path.splitext(f.lower())[0] not in _SHARED_EXES])
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
        self._measured[app_id] = size
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
        # kept to look for leftovers once it is gone (by then the registry no longer knows it)
        self._removed[app_id] = {'name': app['name'], 'publisher': app['publisher'], 'registry_key': sub,
                                 'location': app['location'] if app['location_exists'] else '', 'time': time.time()}
        sim = winapi.simulation()
        if sim is not None:
            result = sim.uninstall(hive, base + '\\' + sub, app['location'] if app['location_exists'] else '')
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
            self._removed.pop(app_id, None)
            raise ValueError('没能启动卸载程序（可能在系统提示中选择了「否」）')
        self._cache = None
        self._log(app, '已启动卸载程序')
        return {'started': True, 'simulated': False}

    def installed(self, app_id: str) -> bool:
        """Is the program still registered? Its Uninstall entry goes when the uninstaller has finished --
        the same signal 「设置 > 应用」 uses (the uninstaller's own process often exits early)."""
        try:
            hive, base, sub = str(app_id).split('|', 2)
        except ValueError:
            return False
        return bool(self._reg().values(hive, base + '\\' + sub))

    # folders uninstallers usually leave behind, and what is typically in them
    _LEFTOVER_BASES = (('', '安装文件夹', ''), ('LOCALAPPDATA', '本地应用数据', ''),
                       ('APPDATA', '漫游应用数据', '通常是设置和账号数据：确认以后不会再装它'),
                       ('LOCALLOW', 'LocalLow', '游戏常把存档放在这里'), ('ProgramData', '所有用户的应用数据', ''))

    def leftovers(self, app_id: str) -> list[dict]:
        """Folders an uninstalled program left behind: its install folder if still there, and folders named
        like it in AppData, LocalLow and ProgramData -- directly, or inside its publisher's folder
        (AppData\\Roaming\\Tencent\\WeChat, never all of Tencent). Folders another installed program still
        claims are left out. Nothing is deleted here."""
        from .checkup import VALUABLE_DIRS
        from .fsops import path_size
        gone = self._removed.get(app_id)
        if gone is None:
            raise ValueError('没有这个程序的卸载记录，请先在这里卸载它')
        vendor_words = set(tokens(gone['publisher']))
        keys = {compact(gone['name'])}
        if not re.match(r'^(\{|steam app)', gone['registry_key'], re.I):
            keys.add(compact(gone['registry_key']))
        if gone['location']:
            keys.add(compact(ntpath.basename(gone['location'].rstrip('\\/'))))
        words = [t for t in tokens(gone['name']) if t not in vendor_words]
        if words and len(words[0]) >= 4:
            keys.add(words[0])
        keys = {k for k in keys if len(k) >= 3 and k not in _GENERIC}
        vendors = {compact(gone['publisher'])} | {t for t in vendor_words if len(t) >= 4}
        if gone['location']:
            vendors.add(compact(ntpath.basename(ntpath.dirname(gone['location'].rstrip('\\/')))))
        vendors = {v for v in vendors if len(v) >= 3 and v not in _GENERIC}
        still = [row for row in self.match_index() if row['name'] != gone['name']]

        def subdirs(folder):
            try:
                return [e.name for e in os.scandir(folder) if e.is_dir(follow_symlinks=False)]
            except OSError:
                return []

        found: list[tuple[str, str, str]] = []
        for var, where, note in self._LEFTOVER_BASES:
            if not var:
                if gone['location'] and os.path.isdir(gone['location']):
                    found.append((gone['location'], where, note))
                continue
            base = paths.expand(f'%{var}%', self.env)
            if not base:
                continue
            for child in subdirs(base):
                c = compact(child)
                if c in keys:
                    found.append((os.path.join(base, child), where, note))
                elif c in vendors:
                    for grand in subdirs(os.path.join(base, child)):
                        if compact(grand) in keys:
                            found.append((os.path.join(base, child, grand), where, note))
        out = []
        for path, where, note in found:
            name = ntpath.basename(path)
            if where != '安装文件夹' and self.match_folder(name, still):
                continue  # another installed program goes by that name
            if any(paths.is_within(path, o['path']) for o in out):
                continue
            size, count = path_size(path)
            valuable = _named_inside(path, VALUABLE_DIRS)
            try:
                mtime = os.stat(path).st_mtime
            except OSError:
                mtime = None
            out.append({'path': path, 'where': where, 'size': size, 'files': count, 'mtime': mtime,
                        'caution': f'里面有「{valuable}」文件夹，可能是存档、个人资料或备份' if valuable else note or None})
        return out

    def _log(self, app: dict, note: str):
        if self.oplog is not None:
            self.oplog.add('uninstall', f'卸载：{app["name"]}', items=[{'name': app['name'], 'version': app['version'],
                                                                         'publisher': app['publisher'], 'note': note,
                                                                         'location': app['location']}])
