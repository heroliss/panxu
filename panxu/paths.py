"""Windows path policy: environment bases, anonymisation and protection.

All functions here operate on Windows path *strings* via ``ntpath`` so the
policy behaves identically when unit-tested on Linux.
"""
from __future__ import annotations

import ntpath
import os
import re

from . import winapi

# Environment bases used by rules, knowledge and anonymisation.
ENV_KEYS = ('SystemRoot', 'SystemDrive', 'ProgramFiles', 'ProgramFiles(x86)', 'ProgramData',
            'USERPROFILE', 'LOCALAPPDATA', 'APPDATA', 'TEMP', 'PUBLIC')
# Per-user keys that an elevated relaunch carries over from the original user.
USER_ENV_KEYS = ('USERPROFILE', 'LOCALAPPDATA', 'APPDATA', 'TEMP')

_env_override: dict[str, str] = {}
_sim_env: dict[str, str] = {}
_sim_drives: dict[str, str] = {}   # simulated drive folder -> 'C:'


def set_env_override(values: dict[str, str]) -> None:
    """Use the original user's folders after an over-the-shoulder elevation."""
    _env_override.clear()
    _env_override.update({k: v for k, v in values.items() if k in USER_ENV_KEYS and v})


def set_simulated_environment(values: dict[str, str] | None, drives: dict[str, str] | None = None) -> None:
    """Point every base folder into the simulation (演练环境); None restores the real ones.

    ``drives`` maps the simulated drive folders to their letters, so text sent to a model
    shows 'C:\\Users' instead of the real path of the simulation (and the real user name).
    """
    _sim_env.clear()
    _sim_env.update(values or {})
    _sim_drives.clear()
    _sim_drives.update(drives or {})


def simulated_drives() -> dict[str, str]:
    return dict(_sim_drives)


def is_foreign_user() -> bool:
    """True when elevated as a *different* account than the one that started us."""
    original = _env_override.get('USERPROFILE')
    current = os.environ.get('USERPROFILE')
    return bool(original and current and key(original) != key(current))


def _norm(value: str) -> str:
    # POSIX paths only occur in simulations and tests on Linux; keep them usable there
    return os.path.normpath(value) if value.startswith('/') and os.sep == '/' else ntpath.normpath(value)


def environment() -> dict[str, str]:
    if _sim_env:
        return {k: _norm(v) for k, v in _sim_env.items()}
    env = {}
    for key in ENV_KEYS:
        value = _env_override.get(key) or os.environ.get(key)
        if value:
            env[key] = ntpath.normpath(value)
    env.setdefault('SystemDrive', 'C:')
    env.setdefault('SystemRoot', env['SystemDrive'] + '\\Windows')
    if 'USERPROFILE' in env:
        env.setdefault('LOCALAPPDATA', ntpath.join(env['USERPROFILE'], 'AppData', 'Local'))
        env.setdefault('APPDATA', ntpath.join(env['USERPROFILE'], 'AppData', 'Roaming'))
    if 'LOCALAPPDATA' in env:
        env.setdefault('TEMP', ntpath.join(env['LOCALAPPDATA'], 'Temp'))
        env['LOCALLOW'] = ntpath.join(ntpath.dirname(env['LOCALAPPDATA']), 'LocalLow')
    return env


_VAR = re.compile(r'%([A-Za-z0-9_()]+)%')


def expand(template: str, env: dict[str, str]) -> str | None:
    """Expand %VAR% tokens from ``env``. None if any token is unknown."""
    missing = False

    def sub(match):
        nonlocal missing
        value = env.get(match.group(1))
        if value is None:
            missing = True
            return ''
        return value

    result = _VAR.sub(sub, template)
    return None if missing else ntpath.normpath(result)


def native(path: str) -> str:
    """Windows-style path string -> path usable by the local OS (tests run on Linux)."""
    return path if os.sep == '\\' else path.replace('\\', os.sep)


def key(path: str) -> str:
    """Case-insensitive comparison key for a Windows path."""
    return ntpath.normcase(ntpath.normpath(path)).rstrip('\\')


def is_within(path: str, base: str) -> bool:
    p, b = key(path), key(base)
    return p == b or p.startswith(b + '\\')


def within_any(path: str, keys: set[str]) -> bool:
    """Is ``path`` one of ``keys`` (``key()`` values) or inside one of them? Looks up the path's parent folders,
    so it costs the folder depth, not the number of keys (deleting 20 000 files must not take n x n steps)."""
    k = key(path)
    while True:
        if k in keys:
            return True
        up = key(ntpath.dirname(k))
        if up == k or not up:
            return False
        k = up


def is_drive_root(path: str) -> bool:
    """C:\\, C:, \\\\server\\share and a bare \\ (root of the current drive)."""
    drive, rest = ntpath.splitdrive(ntpath.normpath(path))
    return rest in ('', '\\', '/') if drive else rest in ('\\', '/')


_ANON_ORDER = ('LOCALLOW', 'LOCALAPPDATA', 'APPDATA', 'TEMP', 'USERPROFILE', 'PUBLIC',
               'ProgramFiles(x86)', 'ProgramFiles', 'ProgramData', 'SystemRoot')


def anonymize(path: str, env: dict[str, str]) -> str:
    """Replace user-identifying bases with %VAR% before sending to a model."""
    best = None
    for name in _ANON_ORDER:
        base = env.get(name)
        if base and is_within(path, base):
            if best is None or len(base) > len(env[best]):
                best = name
    if best is None:
        for root, letter in _sim_drives.items():
            if is_within(path, root):
                return letter + (ntpath.normpath(path)[len(ntpath.normpath(root)):] or '\\')
        return path
    rest = ntpath.normpath(path)[len(ntpath.normpath(env[best])):]
    return '%' + best + '%' + rest


def deanonymize(path: str, env: dict[str, str]) -> str | None:
    return expand(path, env) if '%' in path else ntpath.normpath(path)


# A 演练环境 (sandbox.Sandbox) kept outside PanXu's data folder (``run.py --sandbox D:\\somewhere``): its
# marker file next to its simulated drives. Everything in it is 盘序's own made-up test data, so the
# personal-file tools and the duplicate finder do not offer it, and the space view says what it is.
SANDBOX_MARKER = '.panxu-sandbox'  # written by sandbox.Sandbox.build next to its simulated C drive


def sandbox_layout(file_names, dir_names) -> bool:
    """Do these names (one folder's files and sub-folders) make up a 演练环境?"""
    return SANDBOX_MARKER in {n.lower() for n in file_names} and 'c' in {n.lower() for n in dir_names}


def is_sandbox_root(path: str) -> bool:
    """The same as ``sandbox_layout``, for a folder by its path (a few metadata lookups, no listing)."""
    if not isinstance(path, str) or not path or not os.path.isabs(path):
        return False  # e.g. the check-up's 「本机磁盘」 node, which has no path
    try:
        return os.path.isfile(os.path.join(path, SANDBOX_MARKER)) and os.path.isdir(os.path.join(path, 'C'))
    except (OSError, ValueError, TypeError):
        return False


# ---------------------------------------------------------------------------
# Protection policy for user-initiated deletion (large files, duplicates,
# space-analysis selections, AI suggestions). Rule-based junk cleanup uses its
# own vetted targets and does not go through these checks.
# ---------------------------------------------------------------------------

_ROOT_SYSTEM_NAMES = {'pagefile.sys', 'hiberfil.sys', 'swapfile.sys', 'dumpstack.log', 'dumpstack.log.tmp',
                      'bootmgr', 'bootnxt', 'documents and settings', 'programdata', 'program files',
                      'program files (x86)', 'users', 'config.msi', 'msocache'}
# Top-level folders that are off-limits on *any* drive (a second Windows install,
# recovery partitions mapped to a letter, upgrade leftovers handled by cleanmgr).
_ROOT_SYSTEM_SUBTREES = {'windows', '$recycle.bin', 'system volume information', 'recovery', 'boot', 'efi',
                         '$windows.~bt', '$windows.~ws', '$winreagent', 'windows.old', '$sysreset', '$getcurrent'}


# The folders Windows gives every account. They are known folders for the current user already; for any
# other account (looked through as administrator) they must stay as well, empty or not.
_PROFILE_FOLDERS = {'desktop', 'documents', 'downloads', 'pictures', 'videos', 'music', 'appdata', 'onedrive',
                    'favorites', 'contacts', 'links', 'saved games', 'searches', '3d objects', 'application data',
                    'local settings', 'my documents', 'start menu', 'cookies', 'recent', 'sendto', 'templates',
                    'nethood', 'printhood'}


def _absolute(path: str) -> bool:
    """Absolute with a drive (or UNC share) on Windows; POSIX-absolute in Linux test runs.

    (Python 3.13 changed ``ntpath.isabs('/x')`` to False, which is right for Windows.)
    """
    if not path:
        return False
    if winapi.IS_WINDOWS:
        return bool(ntpath.splitdrive(path)[0]) and ntpath.isabs(path)
    return path.startswith('/') or (ntpath.isabs(path) and bool(ntpath.splitdrive(path)[0]))


def _real(path: str) -> str:
    """Resolve 8.3 names, junctions and trailing dots the way Windows will (Windows only)."""
    if not winapi.IS_WINDOWS:
        return path
    try:
        return os.path.realpath(path)
    except (OSError, ValueError):
        return path


class ProtectionPolicy:
    """Decides whether a user/AI-selected path may be deleted.

    * ``exact``    may not be deleted themselves (only their contents);
    * ``subtrees`` are entirely off-limits;
    * a selection that *contains* any protected or excluded folder is refused,
      so deleting a parent can never take a protected child with it.

    Every check runs on both the path as given and its resolved form.
    Built once per batch.
    """

    def __init__(self, env: dict[str, str], excluded: list[str] | None = None, app_dirs: list[str] | None = None,
                 allowed_roots: list[str] | None = None, drive_roots: list[str] | None = None):
        self.env = env
        # simulation: nothing outside these folders may be touched at all
        self.allowed_roots = [p for p in (allowed_roots or []) if p]
        # folders that play the role of a drive root (the simulated C:\ and D:\)
        self.drive_roots = [p for p in (drive_roots or []) if p]
        sysdrive = env.get('SystemDrive', '')
        if sysdrive and not ntpath.splitdrive(sysdrive)[1].strip('\\/') == '' and sysdrive not in self.drive_roots:
            self.drive_roots.append(sysdrive)
        exact = []
        for name in ('USERPROFILE', 'LOCALAPPDATA', 'APPDATA', 'LOCALLOW', 'ProgramData', 'ProgramFiles',
                     'ProgramFiles(x86)', 'PUBLIC', 'TEMP'):
            if env.get(name):
                exact.append(env[name])
        home = env.get('USERPROFILE')
        if home:
            exact += [ntpath.dirname(home), ntpath.join(home, 'AppData'), ntpath.join(home, 'OneDrive')]
        for folder in winapi.KNOWN_FOLDERS:
            try:
                exact.append(winapi.known_folder(folder))
            except OSError:
                pass
        subtrees = [env['SystemRoot']]
        if env.get('ProgramData'):
            subtrees += [ntpath.join(env['ProgramData'], 'Microsoft'), ntpath.join(env['ProgramData'], 'Package Cache')]
        if env.get('ProgramFiles'):
            subtrees += [ntpath.join(env['ProgramFiles'], 'WindowsApps'),
                         ntpath.join(env['ProgramFiles'], 'Common Files'), ntpath.join(env['ProgramFiles'], 'Windows Defender')]
        if env.get('LOCALAPPDATA'):
            local = env['LOCALAPPDATA']
            subtrees += [ntpath.join(local, 'Packages'), ntpath.join(local, 'Microsoft', 'WindowsApps'),
                         ntpath.join(local, 'Microsoft', 'Credentials'), ntpath.join(local, 'Microsoft', 'Vault'),
                         ntpath.join(local, 'Microsoft', 'Windows')]
        if env.get('APPDATA'):
            roaming = ntpath.join(env['APPDATA'], 'Microsoft')
            subtrees += [ntpath.join(roaming, n) for n in ('Protect', 'Crypto', 'Credentials', 'Vault',
                                                           'SystemCertificates', 'Windows')]
        if home:
            users = ntpath.dirname(home)
            subtrees += [ntpath.join(users, 'Default'), ntpath.join(users, 'All Users')]
        subtrees += app_dirs or []
        self.exact_paths = [p for p in exact if p]
        self.exact = {key(p) for p in self.exact_paths} | {key(_real(p)) for p in self.exact_paths}
        self.subtrees = subtrees + [_real(p) for p in subtrees if _real(p) != p]
        self.excluded = [p for p in (excluded or []) if p]
        self.excluded += [_real(p) for p in self.excluded if _real(p) != p]
        self.users_dirs = {key(ntpath.dirname(home))} if home else set()

    def reason(self, path: str) -> str | None:
        """Why ``path`` must not be deleted, or None if allowed."""
        if not isinstance(path, str) or not _absolute(path):
            return '路径无效'
        if path.startswith(('\\\\?\\', '\\\\.\\')):
            return '不支持这种路径写法'
        drive = ntpath.splitdrive(ntpath.normpath(path))[0]
        if drive.startswith('\\\\') and drive.rstrip('\\').endswith('$'):
            return '不能通过管理共享删除'
        forms = [path]
        real = _real(path)
        if key(real) != key(path):
            forms.append(real)
        if self.allowed_roots and not all(any(is_within(f, r) for r in self.allowed_roots) for f in forms):
            return '演练环境中只能处理演练目录里的文件'
        for form in forms:
            reason = self._reason(form)
            if reason:
                return reason
        return None

    def _split_root(self, path: str) -> tuple[str, list[str]]:
        """(drive, components below it); a simulated drive folder counts as a drive root."""
        normalized = ntpath.normpath(path)
        for root in self.drive_roots:
            if is_within(path, root):
                rest = key(path)[len(key(root)):]
                return ntpath.normpath(root), [p for p in rest.split('\\') if p]
        drive, rest = ntpath.splitdrive(normalized)
        return drive, [p for p in rest.split('\\') if p]

    def _reason(self, path: str) -> str | None:
        if is_drive_root(path) or any(key(path) == key(r) for r in self.drive_roots):
            return '不能删除整个磁盘'
        normalized = ntpath.normpath(path)
        drive, parts = self._split_root(path)
        if parts:
            top = parts[0].lower().rstrip('. ')
            if top in _ROOT_SYSTEM_SUBTREES:
                return '这是 Windows 系统目录或其中的内容'
            if len(parts) == 1 and top in _ROOT_SYSTEM_NAMES:
                return '这是 Windows 系统文件或目录'
        for base in self.subtrees:
            if is_within(path, base):
                return '位于受保护的系统目录中'
        for base in self.excluded:
            if is_within(path, base):
                return '位于你设置的排除目录中'
        k = key(path)
        if k in self.exact or (drive and k == key(ntpath.join(drive + ('' if drive.endswith('\\') else '\\'), 'Users'))):
            return '这是系统或用户的基础文件夹，只能清理其中的内容'
        if key(ntpath.dirname(normalized)) in self.users_dirs:
            return '这是一个 Windows 用户的个人文件夹'
        parent = ntpath.dirname(normalized)
        if key(ntpath.dirname(parent)) in self.users_dirs and ntpath.basename(normalized).lower() in _PROFILE_FOLDERS:
            return '这是 Windows 为每个账户准备的基础文件夹，只能清理其中的内容'
        # a selection must not contain anything protected
        for base in self.exact_paths + self.subtrees + self.excluded:
            if is_within(base, path) and key(base) != k:
                return f'其中包含受保护的文件夹：{base}'
        return None

    def caution(self, path: str) -> str | None:
        """Allowed, but the confirmation dialog should warn about it."""
        env = self.env
        if env.get('USERPROFILE') and is_within(path, env['USERPROFILE']):
            rest = key(path)[len(key(env['USERPROFILE'])):].lstrip('\\')
            if rest.startswith('onedrive'):
                return '位于 OneDrive 同步目录，删除会同步到云端（云端回收站可恢复）'
        for name in ('ProgramFiles', 'ProgramFiles(x86)'):
            if env.get(name) and is_within(path, env[name]):
                return '位于程序安装目录。若程序仍在使用，请改用「设置 > 应用」卸载'
        if env.get('ProgramData') and is_within(path, env['ProgramData']):
            return '位于程序共享数据目录，可能被已安装的程序使用'
        if env.get('APPDATA') and is_within(path, env['APPDATA']):
            return '位于应用配置目录，可能包含应用设置或账号数据'
        if env.get('LOCALAPPDATA') and is_within(path, env['LOCALAPPDATA']):
            return '位于本地应用数据目录，可能是程序正在使用的数据'
        drive = self._split_root(path)[0]
        drive_users = ntpath.join(drive + ('' if drive.endswith('\\') else '\\'), 'Users')
        if env.get('USERPROFILE') and is_within(path, drive_users) and not is_within(path, env['USERPROFILE']):
            return '位于其他用户的个人文件夹中'
        return None


# ---------------------------------------------------------------------------
# File type categories for the large-file and space views.
# ---------------------------------------------------------------------------

CATEGORIES = {
    'video': ('视频', {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.ts', '.mts', '.m2ts', '.rmvb', '.3gp'}),
    'image': ('图片', {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.heic', '.tif', '.tiff', '.raw', '.cr2', '.nef', '.arw', '.dng', '.psd'}),
    'audio': ('音频', {'.mp3', '.flac', '.wav', '.aac', '.m4a', '.ogg', '.wma', '.ape'}),
    'archive': ('压缩包', {'.zip', '.rar', '.7z', '.tar', '.gz', '.tgz', '.bz2', '.xz', '.zst', '.cab'}),
    'installer': ('安装包', {'.exe', '.msi', '.msix', '.msixbundle', '.appx', '.appxbundle', '.apk'}),
    'disk': ('磁盘镜像', {'.iso', '.img', '.vhd', '.vhdx', '.vmdk', '.vdi', '.wim', '.esd', '.qcow2'}),
    'document': ('文档', {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.txt', '.md', '.epub', '.csv'}),
    'data': ('数据库/日志', {'.db', '.sqlite', '.mdf', '.ldf', '.log', '.etl', '.dmp', '.bak', '.pst', '.ost'}),
}
_EXT_TO_CATEGORY = {ext: cat for cat, (_, exts) in CATEGORIES.items() for ext in exts}


def category_of(name: str) -> str:
    return _EXT_TO_CATEGORY.get(ntpath.splitext(name)[1].lower(), 'other')


def category_label(cat: str) -> str:
    return CATEGORIES.get(cat, ('其他', None))[0]
