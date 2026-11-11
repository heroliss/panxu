"""Rule-based junk cleanup: a vetted catalogue of caches and temporary data.

Files matched by these rules are *permanently* deleted (they are rebuilt by
Windows or the owning app), which is why every target here must be a
well-known location whose contents are disposable. Anything not covered by a
rule goes through the Recycle Bin path instead (see ``fsops.recycle``).
"""
from __future__ import annotations

import fnmatch
import glob
import ntpath
import os
import threading
import time
from dataclasses import dataclass, field

from . import fsops, paths, winapi

HOUR = 3600.0
DAY = 24 * HOUR


@dataclass(frozen=True)
class Target:
    path: str                 # %VAR% template; '*' allowed inside components
    pattern: str = '*'        # file-name globs separated by '|'
    min_age_hours: float = 0  # skip files modified more recently than this
    recursive: bool = True
    admin: bool = False       # needs elevation to read/delete
    single_file: bool = False
    skip_dirs: tuple = ()     # lower-case sub-folder names never touched

    def accepts(self, name: str) -> bool:
        lowered = name.lower()
        return any(fnmatch.fnmatchcase(lowered, p.strip().lower()) for p in self.pattern.split('|'))


@dataclass(frozen=True)
class Rule:
    id: str
    name: str
    group: str
    description: str
    targets: tuple = ()
    default: bool = True
    admin: bool = False        # whole rule needs elevation
    processes: tuple = ()      # must be closed before cleaning
    mode: str = 'files'        # files | recycle_bin | cleanmgr | command
    handlers: tuple = ()       # cleanmgr VolumeCaches handler names
    command: tuple = ()        # argv template for mode=command (%VAR% expanded)
    note: str = ''             # shown as a caution in the UI
    slow: bool = False
    finder: str = ''           # FINDERS key: locations found by their structure instead of a fixed path


GROUPS = [
    ('system', '系统垃圾'),
    ('browser', '浏览器缓存'),
    ('app', '应用缓存'),
    ('dev', '开发工具缓存'),
    ('advanced', '系统深度清理'),
]


def _chromium(rule_id, name, user_data, exe, default=True):
    subdirs = ('Cache', 'Code Cache', 'GPUCache', 'DawnCache', 'DawnGraphiteCache', 'DawnWebGPUCache')
    targets = tuple(Target(f'{user_data}\\*\\{d}') for d in subdirs)
    targets += (Target(f'{user_data}\\ShaderCache'), Target(f'{user_data}\\GrShaderCache'),
                Target(f'{user_data}\\GraphiteDawnCache'))
    return Rule(rule_id, name, 'browser', f'{name} 的网页缓存和图形缓存。不影响书签、密码、历史记录和登录状态。',
                targets, default=default, processes=(exe,))


def _electron_cache(rule_id, name, folder, exe):
    # Only Chromium's disposable rendering caches, never User/History, IndexedDB,
    # Local Storage, Service Worker, vaults or app-specific data in the same profile.
    return Rule(rule_id, name, 'app', f'{name} 的网页、代码和图形缓存，关闭程序后可清理，下次启动会重新生成。',
                tuple(Target(f'%APPDATA%\\{folder}\\{d}') for d in ('Cache', 'Code Cache', 'GPUCache')),
                processes=(exe,))


# ---------------------------------------------------------------------------
# Caches found by what they are instead of where one product keeps them.
#
# Every Electron app, WebView2 host and CEF client (Steam, bilibili, Cursor, Unity Hub ...) keeps a Chromium
# profile in its app data, with Chromium's own caches in it under fixed names and in fixed on-disk formats. A
# rule per product never keeps up: one PC had fifty of them. 「Any folder named Cache」 would be a guess, so a
# location is taken only when BOTH hold:
#   1. its parent is a Chromium profile (Local State / Preferences, or its Network + storage folders), and
#   2. the folder itself has the cache's own layout -- the HTTP cache's Cache_Data, the block-file cache's
#      index + data_N, the code cache's js / wasm stores.
# Only those files are taken: Cookies, Local Storage, IndexedDB, Service Worker, extensions and whatever the
# app itself keeps next to them are never listed. Names per https://www.electronjs.org/docs/latest/api/app and
# Chromium's net/disk_cache (blockfile: index, data_0..3, f_xxxxxx; simple: index-dir + hashed entries).
# ---------------------------------------------------------------------------

CHROMIUM_BLOCK_CACHES = ('GPUCache', 'DawnCache', 'DawnGraphiteCache', 'DawnWebGPUCache', 'GrShaderCache',
                         'ShaderCache', 'GraphiteDawnCache')
BLOCKFILE_PATTERN = 'index|data_?|f_??????'
_CODE_CACHE_STORES = ('js', 'wasm', 'webui_js')
_PROFILE_FILES = {'local state', 'preferences'}
_PROFILE_DIRS = {'network', 'local storage', 'session storage'}
# never entered while looking for profiles: stores of their own, and places with rules or protection of their own
_NO_DESCENT = {'cache', 'code cache', 'indexeddb', 'local storage', 'session storage', 'service worker',
               'blob_storage', 'extensions', 'crashpad', 'network', 'node_modules', '.git', 'temp', 'tmp',
               'logs', 'databases', 'file system', 'shared_proto_db'} | {n.lower() for n in CHROMIUM_BLOCK_CACHES}
_VENDOR_FOLDERS = {'microsoft', 'google', 'tencent', 'netease', 'baidu', 'alibaba', 'bytedance', 'adobe',
                   'jetbrains', 'mozilla', 'yodao', 'kingsoft'}
FINDER_DEPTH = 4          # below the app-data folder: App \ [Vendor \] [User Data | EBWebView | Partitions] \ profile
FINDER_MAX_DIRS = 30000


def _listing(path: str) -> tuple[dict, set] | None:
    """({lower-case sub-folder name: path}, {lower-case file names}) of a folder, links left out."""
    dirs, files = {}, set()
    try:
        with os.scandir(fsops.long_path(path)) as entries:
            for e in entries:
                try:
                    if fsops.is_link(e):
                        continue
                    if e.is_dir(follow_symlinks=False):
                        dirs[e.name.lower()] = os.path.join(path, e.name)
                    else:
                        files.add(e.name.lower())
                except OSError:
                    continue
    except OSError:
        return None
    return dirs, files


def _is_blockfile(files: set) -> bool:
    return 'index' in files and 'data_0' in files and 'data_1' in files


def _chromium_cache_targets(profile_dirs: dict) -> list[tuple[Target, str]]:
    """The caches of one Chromium profile that have their own layout: (what to take, where)."""
    found = []
    cache = profile_dirs.get('cache')
    if cache:
        inner = _listing(cache)
        if inner:
            data = inner[0].get('cache_data')
            data_inner = _listing(data) if data else None
            if data_inner and (_is_blockfile(data_inner[1]) or 'index' in data_inner[1] or 'index-dir' in data_inner[0]):
                found.append((Target(''), data))                       # the HTTP cache proper
            elif not data and _is_blockfile(inner[1]):
                found.append((Target('', pattern=BLOCKFILE_PATTERN, recursive=False), cache))   # older layout
    code = profile_dirs.get('code cache')
    if code:
        inner = _listing(code)
        for store in _CODE_CACHE_STORES:
            path = inner[0].get(store) if inner else None
            store_inner = _listing(path) if path else None
            if store_inner and ('index' in store_inner[1] or 'index-dir' in store_inner[0]):
                found.append((Target(''), path))
    for name in CHROMIUM_BLOCK_CACHES:
        path = profile_dirs.get(name.lower())
        inner = _listing(path) if path else None
        if inner and _is_blockfile(inner[1]):
            found.append((Target('', pattern=BLOCKFILE_PATTERN, recursive=False), path))
    return found


def find_chromium_caches(env: dict) -> list[tuple[Target, str, str]]:
    """(what to take, where, the app it belongs to) for every Chromium profile in the user's app data."""
    out = []
    budget = FINDER_MAX_DIRS
    for var in ('APPDATA', 'LOCALAPPDATA'):
        base = env.get(var)
        if not base:
            continue
        base = native(base)
        protected = {paths.key(os.path.join(base, *rel)) for rel in (
            ('Packages',), ('Temp',), ('Programs',), ('Microsoft', 'Windows'), ('Microsoft', 'WindowsApps'),
            ('Microsoft', 'Credentials'), ('Microsoft', 'Vault'), ('Microsoft', 'Protect'), ('Microsoft', 'Crypto'))}
        stack = [(base, 0)]
        while stack and budget > 0:
            folder, depth = stack.pop()
            budget -= 1
            listing = _listing(folder)
            if listing is None:
                continue
            dirs, files = listing
            if depth >= 1 and (files & _PROFILE_FILES or len(_PROFILE_DIRS & set(dirs)) >= 2):
                parts = os.path.relpath(folder, base).split(os.sep)
                vendor = parts[0].lower() in _VENDOR_FOLDERS or parts[0].startswith('@')
                app = '\\'.join(parts[:2]) if vendor and len(parts) > 1 else parts[0]
                out.extend((target, root, app) for target, root in _chromium_cache_targets(dirs))
            if depth < FINDER_DEPTH:
                for name, path in dirs.items():
                    if name not in _NO_DESCENT and paths.key(path) not in protected:
                        stack.append((path, depth + 1))
    return out


FINDERS = {'chromium': find_chromium_caches}


RULES: list[Rule] = [
    # -- system --------------------------------------------------------------
    Rule('user_temp', '用户临时文件', 'system',
         '程序运行和安装时留下的临时文件。只删除 24 小时前的文件，正在使用的文件会自动跳过。',
         (Target('%TEMP%', min_age_hours=24),)),
    Rule('windows_temp', 'Windows 临时文件', 'system',
         '系统服务和安装程序留下的临时文件。只删除 24 小时前的文件。',
         (Target('%SystemRoot%\\Temp', min_age_hours=24, admin=True),), admin=True),
    Rule('wu_download', 'Windows 更新下载缓存', 'system',
         '已下载的更新安装包，安装完成后不再需要。若有未装完的更新，Windows 会重新下载。',
         (Target('%SystemRoot%\\SoftwareDistribution\\Download', min_age_hours=24, admin=True),), admin=True),
    Rule('delivery_opt', '传递优化缓存', 'system',
         '用于在局域网内共享更新的缓存。通过系统自带命令清理。',
         (Target('%SystemRoot%\\ServiceProfiles\\NetworkService\\AppData\\Local\\Microsoft\\Windows\\DeliveryOptimization\\Cache', admin=True),),
         admin=True, mode='command',
         command=('%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe', '-NoProfile', '-NonInteractive',
                  '-Command', 'Delete-DeliveryOptimizationCache -Force')),
    Rule('wer', 'Windows 错误报告', 'system',
         '程序出错时生成的报告，已发送或无需发送。',
         (Target('%LOCALAPPDATA%\\Microsoft\\Windows\\WER\\ReportArchive'),
          Target('%LOCALAPPDATA%\\Microsoft\\Windows\\WER\\ReportQueue'),
          Target('%ProgramData%\\Microsoft\\Windows\\WER\\ReportArchive', admin=True),
          Target('%ProgramData%\\Microsoft\\Windows\\WER\\ReportQueue', admin=True),
          Target('%ProgramData%\\Microsoft\\Windows\\WER\\Temp', admin=True))),
    Rule('crash_dumps', '崩溃转储文件', 'system',
         '程序或系统崩溃时保存的内存快照，只在排查故障时有用。',
         (Target('%LOCALAPPDATA%\\CrashDumps'),
          Target('%SystemRoot%\\Minidump', admin=True),
          Target('%SystemRoot%\\LiveKernelReports', pattern='*.dmp', admin=True),
          Target('%SystemRoot%\\MEMORY.DMP', admin=True, single_file=True))),
    Rule('cbs_logs', '系统组件旧日志', 'system',
         'Windows 组件服务轮换下来的旧日志（CbsPersist），只删除 7 天前的。',
         (Target('%SystemRoot%\\Logs\\CBS', pattern='CbsPersist_*', min_age_hours=7 * 24, recursive=False, admin=True),),
         admin=True),
    Rule('dism_logs', 'DISM 旧维护日志', 'system',
         '系统映像维护工具轮换下来的旧日志，只列出 7 天前的 dism.log.bak。当前日志保留。',
         (Target('%SystemRoot%\\Logs\\DISM', pattern='dism.log.bak', min_age_hours=7 * 24, recursive=False, admin=True),),
         admin=True, default=False, processes=('dism.exe', 'dismhost.exe'),
         note='删除后不能再用这份旧日志排查系统维护或更新故障'),
    Rule('inetcache', 'Windows 网页缓存', 'system',
         '系统网络组件和部分旧程序使用的网页缓存。只删除 24 小时前的文件；Outlook 附件和 Office 的临时副本不会动。',
         (Target('%LOCALAPPDATA%\\Microsoft\\Windows\\INetCache', min_age_hours=24,
                 skip_dirs=('content.outlook', 'content.word', 'content.mso', 'content.excel', 'content.powerpoint')),)),
    Rule('thumbcache', '缩略图缓存', 'system',
         '资源管理器的图片缩略图缓存，删除后会重新生成。资源管理器正在使用的部分会跳过。',
         (Target('%LOCALAPPDATA%\\Microsoft\\Windows\\Explorer', pattern='thumbcache_*.db', recursive=False),),
         default=False),
    Rule('rdp_cache', '远程桌面缓存', 'system',
         '远程桌面连接保存的屏幕图像缓存，下次连接时会重新生成。',
         (Target('%LOCALAPPDATA%\\Microsoft\\Terminal Server Client\\Cache'),), processes=('mstsc.exe',)),
    Rule('recycle_bin', '回收站', 'system',
         '清空所有磁盘的回收站。清空后无法再从回收站恢复。',
         mode='recycle_bin', default=False, note='清空后无法从回收站恢复文件'),

    # -- browsers -------------------------------------------------------------
    _chromium('chrome', 'Google Chrome', '%LOCALAPPDATA%\\Google\\Chrome\\User Data', 'chrome.exe'),
    _chromium('chrome_beta', 'Google Chrome Beta', '%LOCALAPPDATA%\\Google\\Chrome Beta\\User Data', 'chrome.exe'),
    _chromium('chrome_dev', 'Google Chrome Dev', '%LOCALAPPDATA%\\Google\\Chrome Dev\\User Data', 'chrome.exe'),
    _chromium('chrome_canary', 'Google Chrome Canary', '%LOCALAPPDATA%\\Google\\Chrome SxS\\User Data', 'chrome.exe'),
    _chromium('edge', 'Microsoft Edge', '%LOCALAPPDATA%\\Microsoft\\Edge\\User Data', 'msedge.exe'),
    _chromium('brave', 'Brave', '%LOCALAPPDATA%\\BraveSoftware\\Brave-Browser\\User Data', 'brave.exe'),
    _chromium('chrome360', '360 极速浏览器', '%LOCALAPPDATA%\\360Chrome\\Chrome\\User Data', '360chrome.exe'),
    _chromium('se360', '360 安全浏览器', '%APPDATA%\\360se6\\User Data', '360se.exe'),
    _chromium('qqbrowser', 'QQ 浏览器', '%LOCALAPPDATA%\\Tencent\\QQBrowser\\User Data', 'qqbrowser.exe'),
    Rule('firefox', 'Firefox', 'browser', 'Firefox 的网页缓存。不影响书签、密码和历史记录。',
         (Target('%LOCALAPPDATA%\\Mozilla\\Firefox\\Profiles\\*\\cache2'),
          Target('%LOCALAPPDATA%\\Mozilla\\Firefox\\Profiles\\*\\startupCache'),
          Target('%LOCALAPPDATA%\\Mozilla\\Firefox\\Profiles\\*\\thumbnails')),
         processes=('firefox.exe',)),

    # -- apps -----------------------------------------------------------------
    _electron_cache('vscode_insiders', 'VS Code Insiders', 'Code - Insiders', 'code - insiders.exe'),
    _electron_cache('obsidian', 'Obsidian 界面缓存', 'obsidian', 'obsidian.exe'),
    Rule('vscode', 'Visual Studio Code', 'app', 'VS Code 的界面缓存、扩展安装包缓存和旧日志。不影响设置和扩展。',
         (Target('%APPDATA%\\Code\\Cache'), Target('%APPDATA%\\Code\\CachedData'),
          Target('%APPDATA%\\Code\\Code Cache'), Target('%APPDATA%\\Code\\GPUCache'),
          Target('%APPDATA%\\Code\\CachedExtensionVSIXs'), Target('%APPDATA%\\Code\\logs', min_age_hours=7 * 24)),
         processes=('code.exe',)),
    Rule('discord', 'Discord', 'app', 'Discord 的图片和网页缓存。',
         (Target('%APPDATA%\\discord\\Cache'), Target('%APPDATA%\\discord\\Code Cache'),
          Target('%APPDATA%\\discord\\GPUCache')), processes=('discord.exe',)),
    Rule('teams_classic', 'Microsoft Teams（经典版）', 'app', '经典版 Teams 的缓存。',
         (Target('%APPDATA%\\Microsoft\\Teams\\Cache'), Target('%APPDATA%\\Microsoft\\Teams\\Code Cache'),
          Target('%APPDATA%\\Microsoft\\Teams\\GPUCache'), Target('%APPDATA%\\Microsoft\\Teams\\tmp')),
         processes=('teams.exe',)),
    Rule('slack', 'Slack', 'app', 'Slack 的图片和网页缓存。不影响聊天记录和登录状态。',
         (Target('%APPDATA%\\Slack\\Cache'), Target('%APPDATA%\\Slack\\Code Cache'),
          Target('%APPDATA%\\Slack\\GPUCache')), processes=('slack.exe',)),
    Rule('teams_new', 'Microsoft Teams（新版）界面缓存', 'app',
         '新版 Teams 内嵌浏览器的网页、代码和图形缓存。下次启动会重新生成，不清空应用设置或账号资料。',
         tuple(Target('%LOCALAPPDATA%\\Packages\\MSTeams_8wekyb3d8bbwe\\LocalCache\\Microsoft\\MSTeams\\EBWebView\\Default\\' + name)
               for name in ('Cache', 'Code Cache', 'GPUCache')), processes=('ms-teams.exe',)),
    Rule('app_web_cache', '应用界面缓存（Electron / 内嵌浏览器）', 'app',
         '哔哩哔哩、Cursor、Unity Hub、Steam 等用 Chromium 内核做界面的程序的网页、代码和图形缓存，下次启动会重新生成。只取缓存本身，登录状态和设置不动；正在运行的程序整个跳过。',
         finder='chromium'),
    Rule('adobe_media_cache', 'Adobe 媒体缓存', 'app',
         'Premiere Pro、After Effects 等为导入的音视频生成的缓存（.cfa/.pek 等）。删除后打开工程时会重新生成，期间较慢。',
         (Target('%APPDATA%\\Adobe\\Common\\Media Cache Files'), Target('%APPDATA%\\Adobe\\Common\\Media Cache')),
         default=False, processes=('adobe premiere pro.exe', 'afterfx.exe', 'adobe media encoder.exe')),
    Rule('shader_cache', '显卡着色器缓存', 'app',
         'DirectX 和显卡驱动编译的着色器缓存。删除后游戏首次运行会重新编译，可能短暂卡顿。',
         (Target('%LOCALAPPDATA%\\D3DSCache'), Target('%LOCALAPPDATA%\\NVIDIA\\DXCache'),
          Target('%LOCALAPPDATA%\\NVIDIA\\GLCache'), Target('%LOCALAPPDATA%\\NVIDIA Corporation\\NV_Cache'), Target('%LOCALAPPDATA%\\AMD\\DxCache'),
          Target('%LOCALAPPDATA%\\AMD\\DxcCache'), Target('%LOCALAPPDATA%\\AMD\\GLCache'),
          Target('%LOCALAPPDATA%\\AMD\\VkCache'), Target('%LOCALLOW%\\Intel\\ShaderCache')),
         default=False),
    Rule('gpu_installer', '显卡驱动安装残留', 'app',
         '显卡驱动安装时解压和下载的安装包，驱动装好后不再需要。',
         (Target('%SystemDrive%\\NVIDIA', admin=True), Target('%SystemDrive%\\AMD', admin=True),
          Target('%ProgramData%\\NVIDIA Corporation\\Downloader', admin=True)),
         default=False),

    # -- developer caches -----------------------------------------------------
    Rule('npm', 'npm 缓存', 'dev', 'Node.js 包下载缓存。删除后再次安装依赖时会重新下载。',
         (Target('%LOCALAPPDATA%\\npm-cache'),), default=False),
    Rule('yarn', 'Yarn 缓存', 'dev', 'Yarn 包下载缓存。', (Target('%LOCALAPPDATA%\\Yarn\\Cache'),), default=False),
    Rule('pnpm', 'pnpm 元数据缓存', 'dev', 'pnpm 查询包信息时的缓存（不含已安装包共用的 store）。删除后再次安装时重新获取。',
         (Target('%LOCALAPPDATA%\\pnpm-cache'),), default=False),
    Rule('browser_downloads', '自动化测试下载的浏览器', 'dev',
         'Puppeteer 和 Playwright 为自动化测试下载的整套浏览器，常有几百 MB 到几 GB。删除后，下次运行相应的安装命令会重新下载。',
         (Target('%USERPROFILE%\\.cache\\puppeteer'), Target('%LOCALAPPDATA%\\ms-playwright')), default=False),
    Rule('cpptools_ipch', 'VS Code C/C++ 智能感知缓存', 'dev',
         'C/C++ 扩展为加快代码提示生成的预编译头缓存（ipch）。删除后打开工程时会重新生成。',
         (Target('%LOCALAPPDATA%\\Microsoft\\vscode-cpptools\\ipch'),), default=False, processes=('code.exe',)),
    Rule('pip', 'pip 缓存', 'dev', 'Python 包下载缓存。', (Target('%LOCALAPPDATA%\\pip\\cache'),), default=False),
    Rule('uv', 'uv 缓存', 'dev', 'Python 包管理器 uv 的下载与构建缓存。删除后再次安装依赖时会重新下载。',
         (Target('%LOCALAPPDATA%\\uv\\cache'),), default=False, processes=('uv.exe',)),
    Rule('electron_dl', 'Electron 下载缓存', 'dev', 'electron 与 electron-builder 下载的运行时压缩包，重新打包时会再下载。',
         (Target('%LOCALAPPDATA%\\electron\\Cache'), Target('%LOCALAPPDATA%\\electron-builder\\Cache')), default=False),
    Rule('jetbrains_logs', 'JetBrains IDE 日志', 'dev', 'IntelliJ IDEA、PyCharm 等 IDE 的旧日志，只删除 7 天前的。',
         (Target('%LOCALAPPDATA%\\JetBrains\\*\\log', min_age_hours=7 * 24),), default=False),
    Rule('nuget', 'NuGet 下载缓存', 'dev', '.NET 包的 HTTP 下载缓存（不含已解压的全局包）。',
         (Target('%LOCALAPPDATA%\\NuGet\\v3-cache'), Target('%LOCALAPPDATA%\\NuGet\\plugins-cache')), default=False),
    Rule('gradle', 'Gradle 缓存', 'dev', 'Gradle 构建与依赖缓存。删除后下次构建会重新下载，耗时较长。',
         (Target('%USERPROFILE%\\.gradle\\caches'),), default=False, processes=('java.exe',)),
    Rule('maven', 'Maven 本地仓库', 'dev', 'Maven 依赖下载缓存。删除后构建时会重新下载。',
         (Target('%USERPROFILE%\\.m2\\repository'),), default=False),
    Rule('go_build', 'Go 编译缓存', 'dev', 'Go 的编译缓存。', (Target('%LOCALAPPDATA%\\go-build'),), default=False),
    Rule('cargo', 'Cargo 下载缓存', 'dev', 'Rust crate 压缩包缓存。',
         (Target('%USERPROFILE%\\.cargo\\registry\\cache'),), default=False),

    # -- advanced -------------------------------------------------------------
    Rule('windows_old', '旧版 Windows 安装', 'advanced',
         '大版本升级后保留的旧系统（Windows.old），用于回退。通过系统「磁盘清理」组件删除。',
         (Target('%SystemDrive%\\Windows.old', admin=True),), default=False, admin=True, mode='cleanmgr',
         handlers=('Previous Installations',), note='删除后无法回退到之前的 Windows 版本', slow=True),
    Rule('upgrade_leftovers', '系统升级临时文件', 'advanced',
         'Windows 升级过程留下的临时安装文件和升级日志。通过系统「磁盘清理」组件删除。',
         (Target('%SystemDrive%\\$WINDOWS.~BT', admin=True), Target('%SystemDrive%\\$WINDOWS.~WS', admin=True)),
         default=False, admin=True, mode='cleanmgr',
         handlers=('Temporary Setup Files', 'Windows Upgrade Log Files', 'Setup Log Files'), slow=True),
    Rule('component_store', '组件存储清理（WinSxS）', 'advanced',
         '用系统自带的 DISM 删除已被新版本取代的系统组件。可能需要几分钟到半小时，无法预估可释放空间。',
         (), default=False, admin=True, mode='command',
         command=('%SystemRoot%\\System32\\Dism.exe', '/Online', '/Cleanup-Image', '/StartComponentCleanup'),
         note='耗时较长，期间请勿关机', slow=True),
]
RULES_BY_ID = {r.id: r for r in RULES}
GROUP_NAMES = dict(GROUPS)


native = paths.native


_BASE_KEYS = ('USERPROFILE', 'LOCALAPPDATA', 'APPDATA', 'LOCALLOW', 'ProgramData', 'SystemRoot',
              'ProgramFiles', 'ProgramFiles(x86)', 'PUBLIC')


def unsafe_root_reason(root: str, env: dict, temp_like: bool = False) -> str | None:
    """Runtime guard against a misconfigured environment (e.g. TEMP=C:\\).

    A cleanup root must not be a drive root and must be neither a base folder
    (profile, Windows, Program Files, ...) nor an ancestor of one, nor contain
    a personal folder. ``temp_like`` roots (from %TEMP%) must also actually
    look like a temp folder, so TEMP=D:\\Data never gets wiped.
    """
    win = root.replace(os.sep, '\\') if os.sep != '\\' else root
    parts = [p for p in ntpath.splitdrive(ntpath.normpath(win))[1].split('\\') if p]
    if paths.is_drive_root(win) or len(parts) < 1:
        return 'too shallow'
    for k in _BASE_KEYS:
        base = env.get(k)
        if base and paths.is_within(base, win):
            return f'contains {k}'
    for folder in winapi.KNOWN_FOLDERS:
        try:
            known = winapi.known_folder(folder)
        except OSError:
            continue
        if known and (paths.is_within(known, win) or paths.is_within(win, known)):
            return f'overlaps {folder}'
    if temp_like:
        # ...\Temp, ...\tmp, or a numbered session folder such as ...\Temp\2
        names = [p.lower() for p in parts]
        if not (names[-1] in ('temp', 'tmp') or (names[-1].isdigit() and len(names) > 1 and names[-2] in ('temp', 'tmp'))):
            return 'TEMP does not look like a temp folder'
    return None


def resolve_target(target: Target, env: dict) -> list[str]:
    expanded = paths.expand(target.path, env)
    if expanded is None:
        return []
    local = native(expanded)
    if '*' in local or '?' in local:
        found = sorted(p for p in glob.glob(local) if os.path.isdir(p) and not fsops.path_is_link(p))
    elif target.single_file:
        found = [local] if os.path.isfile(local) else []
    else:
        found = [local] if os.path.isdir(local) and not fsops.path_is_link(local) else []
    temp_like = target.path.upper().startswith('%TEMP%')
    return [p for p in found if unsafe_root_reason(p, env, temp_like) is None
            and paths.key(os.path.realpath(p)) == paths.key(p)]


def rule_entries(rule: Rule, env: dict) -> list[tuple[Target, str, str]]:
    """Everything a rule covers right now: (what to take, where, app name for found locations or '').

    Found locations go through the same runtime guard as fixed ones, must lie at least two levels inside the
    user's app data, and never overlap a location a rule with a fixed path already covers (browsers, VS Code)."""
    entries = [(target, root, '') for target in rule.targets for root in resolve_target(target, env)]
    finder = FINDERS.get(rule.finder)
    if finder is None:
        return entries
    fixed = set()
    for other in RULES:
        if other is not rule:
            for target in other.targets:
                fixed.update(paths.key(root) for root in resolve_target(target, env))
    bases = [native(env[k]) for k in ('APPDATA', 'LOCALAPPDATA') if env.get(k)]
    for target, root, app in finder(env):
        base = next((b for b in bases if paths.is_within(root, b)), None)
        if base is None or len(os.path.relpath(root, base).split(os.sep)) < 2:
            continue
        if paths.within_any(root, fixed) or any(k.startswith(paths.key(root) + '\\') for k in fixed):
            continue
        if unsafe_root_reason(root, env) is None and paths.key(os.path.realpath(root)) == paths.key(root):
            entries.append((target, root, app))
    return entries


def running_process_names() -> set[str]:
    sim = winapi.simulation()
    if sim is not None:
        return sim.processes()
    try:
        import psutil
    except ImportError:
        return set()
    names = set()
    for proc in psutil.process_iter(['name']):
        name = proc.info.get('name')
        if name:
            names.add(name.lower())
    return names


def disk_free(env: dict) -> int:
    try:
        return winapi.disk_usage(native(env.get('SystemDrive', 'C:') + '\\')).free
    except OSError:
        return 0


def free_by_drive(roots: list[str] | None = None) -> dict[str, int]:
    """Free-byte snapshot; explicit roots keep before/after on the same drives.

    Discovery and disk usage can both be injected in tests. In sandbox mode,
    never enumerate the host's partitions. An unreadable drive is omitted;
    callers must not replace missing measurements with logical file sizes.
    """
    if roots is None:
        try:
            sim = winapi.simulation()
            if sim is not None:
                roots = [d['root'] for d in sim.drives()]
            else:
                import psutil
                roots = [p.mountpoint for p in psutil.disk_partitions(all=False)
                         if p.fstype and 'cdrom' not in (p.opts or '')]
        except Exception:  # noqa: BLE001 - an unavailable snapshot must never prevent cleanup
            return {}
    out, seen = {}, set()
    for root in roots:
        key = paths.key(root)
        if key in seen:
            continue
        seen.add(key)
        try:
            out[root] = winapi.disk_usage(root).free
        except OSError:
            continue
    return out


@dataclass
class RuleScan:
    id: str
    size: int | None = 0
    count: int = 0
    locations: list = field(default_factory=list)
    needs_admin: bool = False      # rule (or part of it) skipped for lack of elevation
    running: list = field(default_factory=list)
    error: str = ''
    logical_size: int | None = 0
    size_note: str = ''
    apps: dict = field(default_factory=dict)   # found locations: app name -> [bytes, files]
    recent_bytes: int = 0  # occupied bytes changed/created within 7 days
    stale_bytes: int = 0   # occupied bytes unchanged for at least 90 days
    unreadable: int = 0
    scope_blocked: bool = False


class JunkEngine:
    def __init__(self, env: dict | None = None, admin: bool | None = None, processes=None, oplog=None, excluded=None):
        self.env = env or paths.environment()
        self.admin = winapi.is_admin() if admin is None else admin
        self._processes = processes  # injectable for tests
        self.oplog = oplog
        self._excluded = excluded  # list or supplier: fresh settings are rechecked before cleaning
        self.last_scan: dict[str, RuleScan] = {}
        self._lock = threading.Lock()

    def _skip(self):
        values = self._excluded() if callable(self._excluded) else self._excluded
        roots = {paths.key(form) for p in values or [] for form in (p, os.path.realpath(p))}
        return lambda p: paths.within_any(p, roots)

    def _command_excluded(self, rule):
        if rule.mode not in ('command', 'cleanmgr'):
            return False
        values = self._excluded() if callable(self._excluded) else self._excluded
        targets = rule.targets or ((Target('%SystemRoot%\\WinSxS'),) if rule.id == 'component_store' else ())
        for target in targets:
            for root in resolve_target(target, self.env):
                if any(paths.is_within(p, root) or paths.is_within(root, p) for p in values or []):
                    return True
        return False

    def processes(self) -> set[str]:
        return set(self._processes) if self._processes is not None else running_process_names()

    def running_now(self) -> dict[str, list[str]]:
        """{rule id: its programs that are running right now} for every rule that waits for programs.

        Cheap (one process listing, no file access): lets the UI unlock 「请先关闭」 after the user
        closed the program, without scanning again. The last scan results are updated too."""
        running = self.processes()
        out = {r.id: [p for p in r.processes if p.lower() in running] for r in RULES if r.processes}
        with self._lock:
            for rule_id, procs in out.items():
                if rule_id in self.last_scan:
                    self.last_scan[rule_id].running = procs
        return out

    def running_apps(self, entries, cancel=None) -> dict[str, list[str]]:
        """Of the apps whose caches were found by structure, the ones that are running: {app: program names}.

        Two independent signs, either is enough:
        * Windows says a program has the cache's own bookkeeping files open (index / data_0: Chromium keeps
          them open for as long as it runs). Only those few files are asked about -- asking about every cached
          file took minutes on a real PC, because each open file makes the answer slower.
        * a running process is named like the app's folder (Cursor.exe for ...\\Cursor)."""
        by_app: dict[str, list[str]] = {}
        for _, root, app in entries:
            by_app.setdefault(app, []).append(root)
        running = {''.join(c for c in os.path.splitext(name)[0].lower() if c.isalnum()): name for name in self.processes()}
        busy: dict[str, list[str]] = {}
        for app, roots in by_app.items():
            fsops.check_cancel(cancel)
            sentinels = []
            for root in roots:
                for rel in ('index', 'data_0', os.path.join('index-dir', 'the-real-index')):
                    path = os.path.join(root, rel)
                    if os.path.isfile(path):
                        sentinels.append(path)
                        break
            names = list(winapi.programs_using(sentinels) or []) if sentinels else []
            plain = ''.join(c for c in app.split('\\')[-1].lower() if c.isalnum())
            if len(plain) >= 4:
                names += [name for key, name in running.items() if key == plain and name not in names]
            if names:
                busy[app] = names
        return busy

    def catalogue(self) -> list[dict]:
        return [{'id': r.id, 'name': r.name, 'group': r.group, 'group_name': GROUP_NAMES[r.group],
                 'description': r.description, 'default': r.default, 'admin': r.admin, 'mode': r.mode,
                 'note': r.note, 'slow': r.slow, 'processes': list(r.processes)} for r in RULES]

    # -- scanning ------------------------------------------------------------
    def scan(self, rule_ids=None, cancel=None, progress=None) -> list[dict]:
        rules = [RULES_BY_ID[i] for i in rule_ids] if rule_ids else RULES
        running = self.processes()
        results = []
        for index, rule in enumerate(rules):
            fsops.check_cancel(cancel)
            if progress:
                progress(index, len(rules), rule.name)
            result = self._scan_rule(rule, cancel)
            result.running = [p for p in rule.processes if p.lower() in running]
            results.append(result)
            with self._lock:
                self.last_scan[rule.id] = result
        return [self._public(r) for r in results]

    def _public(self, result: RuleScan) -> dict:
        rule = RULES_BY_ID[result.id]
        return {'id': rule.id, 'size': result.size, 'logical_size': result.logical_size,
                'size_note': result.size_note,
                'count': result.count, 'locations': result.locations[:20],
                'recent_bytes': result.recent_bytes, 'stale_bytes': result.stale_bytes, 'unreadable': result.unreadable,
                'needs_admin': result.needs_admin, 'running': result.running, 'error': result.error,
                'scope_blocked': result.scope_blocked,
                # the apps a found-by-structure rule covers, biggest first: [{'name', 'size', 'count'}]
                'apps': [{'name': name, 'size': size, 'count': count} for name, (size, count)
                         in sorted(result.apps.items(), key=lambda kv: -kv[1][0])[:40]],
                'available': not result.scope_blocked and (result.size is None or result.size > 0 or result.count > 0 or rule.mode in ('command',))}

    def _scan_rule(self, rule: Rule, cancel) -> RuleScan:
        result = RuleScan(rule.id)
        if self._command_excluded(rule):
            result.error = '系统清理工具不能只跳过部分文件，本项与排除目录重叠，已跳过'
            result.scope_blocked = True
            return result
        if rule.mode == 'recycle_bin':
            result.size, result.count = winapi.recycle_bin_info(None)
            result.logical_size = result.size
            result.size_note = ('Windows 提供的估算大小，可能包含稀疏或压缩文件的标称大小；'
                                '清空后的已释放按磁盘可用空间变化计算。')
            return result
        if rule.admin and not self.admin:
            result.needs_admin = True
        now = time.time()
        skip = self._skip()

        def unreadable(path, exc):
            result.unreadable += 1
            result.error = '部分位置无法读取，显示的是已检查到的大小'

        seen = set()
        for target in rule.targets:
            if target.admin and not self.admin:
                result.needs_admin = True
        for target, root, app in rule_entries(rule, self.env):
            if skip(root):
                continue
            result.locations.append(root)
            before = (result.size, result.count)
            if target.single_file:
                try:
                    st = os.lstat(fsops.long_path(root))
                    if fsops.st_is_link(st) or getattr(st, 'st_file_attributes', 0) & winapi.CLOUD_ATTRIBUTES:
                        continue
                    result.size += fsops.file_size(root, st)
                    result.logical_size += st.st_size
                    result.count += 1
                except OSError:
                    pass
                continue
            min_age = target.min_age_hours * HOUR
            try:
                for _, files, _ in fsops.walk_files(root, cancel, recursive=target.recursive,
                                                    skip_dirs=frozenset(target.skip_dirs), skip=skip, on_error=unreadable):
                    for entry in files:
                        if not target.accepts(entry.name):
                            continue
                        try:
                            st = entry.stat(follow_symlinks=False)
                        except OSError:
                            unreadable(entry.path, None)
                            continue
                        if getattr(st, 'st_file_attributes', 0) & winapi.CLOUD_ATTRIBUTES:
                            continue
                        if min_age and now - fsops.age_time(st) < min_age:
                            continue
                        key = paths.key(entry.path)
                        if key in seen:
                            continue
                        seen.add(key)
                        used = fsops.file_size(entry.path, st)
                        result.size += used
                        result.logical_size += st.st_size
                        result.count += 1
                        age = now - fsops.age_time(st)
                        if age < 7 * DAY:
                            result.recent_bytes += used
                        elif age >= 90 * DAY:
                            result.stale_bytes += used
            except PermissionError:
                result.needs_admin = True
            if app:
                known = result.apps.setdefault(app, [0, 0])
                known[0] += result.size - before[0]
                known[1] += result.count - before[1]
        if rule.id == 'component_store':
            result.size = None  # cannot be estimated cheaply
            result.logical_size = None
        return result

    # -- cleaning ------------------------------------------------------------
    def clean(self, rule_ids: list[str], cancel=None, progress=None) -> dict:
        running = self.processes()
        outcome = []
        total_freed = 0
        # on record before anything goes (see OperationLog.begin): a PanXu killed half way still left a trace
        pending = self.oplog.begin('junk', '垃圾清理', items=[RULES_BY_ID[r].name for r in rule_ids if r in RULES_BY_ID]) \
            if self.oplog is not None else None

        def step(i, name, freed):
            if progress:
                progress(i, len(rule_ids), name, freed)
            if self.oplog is not None:
                self.oplog.progress(pending, freed=freed, done=i)
        try:
            for index, rule_id in enumerate(rule_ids):
                fsops.check_cancel(cancel)
                rule = RULES_BY_ID.get(rule_id)
                if rule is None:
                    continue
                step(index, rule.name, total_freed)
                row = {'id': rule.id, 'name': rule.name, 'freed': 0, 'files': 0, 'failed': 0, 'in_use': 0,
                       'skipped': '', 'failures': [], 'held': []}
                outcome.append(row)
                blocking = [p for p in rule.processes if p.lower() in running]
                if blocking:
                    row['skipped'] = '请先关闭 ' + rule.name
                elif rule.admin and not self.admin:
                    row['skipped'] = '需要管理员权限'
                else:
                    base = total_freed
                    try:
                        self._clean_rule(rule, row, cancel, progress=lambda freed: step(index, rule.name, base + freed))
                    except fsops.Cancelled:
                        raise
                    except Exception as exc:  # noqa: BLE001 - report per rule, keep going
                        row['skipped'] = f'清理出错：{exc}'
                total_freed += row['freed']
        finally:
            # log even when cancelled: whatever was deleted must be on record
            if self.oplog is not None and outcome:
                failures = [f for r in outcome for f in r['failures']]
                cancelled = cancel is not None and cancel.is_set()
                self.oplog.add('junk', '垃圾清理' + ('（已停止）' if cancelled else ''),
                               freed=sum(r['freed'] for r in outcome),
                               items=[{'id': r['id'], 'name': r['name'], 'freed': r['freed'], 'files': r['files'],
                                       'failed': r['failed'], 'skipped': r['skipped'],
                                       'held': [h['app'] for h in r.get('held', [])]} for r in outcome],
                               failures=failures, details={'mode': 'permanent', 'cancelled': cancelled}, replaces=pending)
            elif self.oplog is not None:
                self.oplog.drop(pending)
            with self._lock:
                for rule_id in rule_ids:
                    self.last_scan.pop(rule_id, None)
        return {'rules': outcome, 'freed': total_freed}

    def _clean_rule(self, rule: Rule, row: dict, cancel, progress):
        if self._command_excluded(rule):
            row['skipped'] = '与排除目录重叠，未运行系统清理工具'
            return
        if rule.mode == 'recycle_bin':
            before = free_by_drive()
            if winapi.empty_recycle_bin(None):
                after = free_by_drive(list(before))
                row['freed'] = max(0, sum(after[root] - free for root, free in before.items() if root in after))
            return
        if rule.mode in ('cleanmgr', 'command'):
            before = disk_free(self.env)
            if rule.mode == 'cleanmgr':
                self._run_cleanmgr(rule)
            else:
                self._run_command(rule)
            row['freed'] = max(0, disk_free(self.env) - before)
            return
        stats = fsops.DeleteStats()
        skip = self._skip()
        entries = [e for e in rule_entries(rule, self.env) if not skip(e[1])]
        if any(t.admin and not self.admin for t in rule.targets):
            row['skipped'] = '部分位置需要管理员权限，已跳过'
        # Locations found by structure have no program list to wait for: Windows is asked who has their files
        # open, and an app that is running keeps all of its caches (never half of them).
        busy_apps = self.running_apps(entries, cancel) if rule.finder else {}
        if rule.finder:
            row['held'] = [{'app': app, 'by': names} for app, names in sorted(busy_apps.items())]
        try:
            for target, root, app in entries:
                if target.admin and not self.admin:
                    continue
                if app in busy_apps:
                    continue
                if target.single_file:
                    try:
                        st = os.lstat(fsops.long_path(root))
                        if fsops.st_is_link(st) or getattr(st, 'st_file_attributes', 0) & winapi.CLOUD_ATTRIBUTES:
                            continue
                        size = fsops.file_size(root, st)
                    except OSError:
                        continue
                    fsops.remove_file(root, stats, size)
                    continue
                fsops.delete_matching(root, target.accepts, min_age_seconds=target.min_age_hours * HOUR,
                                      recursive=target.recursive, skip_dirs=frozenset(target.skip_dirs),
                                      cancel=cancel, stats=stats, progress=lambda s: progress(s.bytes), skip=skip)
                progress(stats.bytes)
        finally:  # partial results survive a cancel
            row.update(freed=stats.bytes, files=stats.files, failed=stats.failed, in_use=stats.in_use,
                       failures=stats.failures)

    def _run_command(self, rule: Rule):
        sim = winapi.simulation()
        if sim is not None:
            sim.run_cleanup_command(rule.id)
            return
        argv = [paths.expand(a, self.env) if '%' in a else a for a in rule.command]
        if any(a is None for a in argv):
            raise RuntimeError('找不到系统命令')
        proc = winapi.run_hidden(argv, timeout=4 * 3600)  # DISM can take very long; never kill it early
        if proc.returncode == 3010:
            return  # success, restart required to finish
        if proc.returncode != 0:
            text = winapi.decode_console(proc.stdout + proc.stderr).strip().splitlines()
            raise RuntimeError((text[-1] if text else f'返回代码 {proc.returncode}')[:200])

    def _run_cleanmgr(self, rule: Rule, slot: int = 6547):
        """Run Disk Cleanup unattended for the given handlers only.

        Uses the documented /sagerun mechanism: mark each handler with
        StateFlagsNNNN=2, run cleanmgr, then remove the marks again.
        """
        sim = winapi.simulation()
        if sim is not None:
            sim.run_cleanup_command(rule.id)
            return
        import winreg  # Windows only
        base = r'SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\VolumeCaches'
        value = f'StateFlags{slot:04d}'
        access = winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY

        def clear_slot():
            # remove our marker from *every* handler, so a stale mark left by an earlier
            # run or another tool can never make cleanmgr empty the Recycle Bin etc.
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as root:
                    i = 0
                    while True:
                        try:
                            name = winreg.EnumKey(root, i)
                        except OSError:
                            break
                        i += 1
                        try:
                            with winreg.OpenKey(root, name, 0, access) as key:
                                winreg.DeleteValue(key, value)
                        except OSError:
                            pass
            except OSError:
                pass

        clear_slot()
        marked = []
        try:
            for handler in rule.handlers:
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base + '\\' + handler, 0, access) as key:
                        winreg.SetValueEx(key, value, 0, winreg.REG_DWORD, 2)
                        marked.append(handler)
                except FileNotFoundError:
                    continue
            if not marked:
                raise RuntimeError('此系统上没有对应的磁盘清理组件')
            exe = paths.expand('%SystemRoot%\\System32\\cleanmgr.exe', self.env)
            winapi.run_hidden([exe, f'/sagerun:{slot}'], timeout=4 * 3600)
        finally:
            clear_slot()


def known_rule_paths(env: dict) -> list[tuple[str, Rule]]:
    """Expanded (template) target paths for annotating the space view."""
    out = []
    for rule in RULES:
        for target in rule.targets:
            expanded = paths.expand(target.path, env)
            if expanded and target.pattern == '*' and not target.single_file:
                out.append((expanded, rule))
        if rule.finder:
            # found locations are real paths already (no wildcard left in them)
            out.extend((root, rule) for _, root, _ in rule_entries(rule, env))
    return out


def validate_catalogue(env: dict) -> list[str]:
    """Static safety checks for the rule catalogue (used by tests)."""
    problems = []
    bases = [env[k] for k in ('TEMP', 'LOCALAPPDATA', 'APPDATA', 'USERPROFILE', 'SystemRoot', 'ProgramData',
                              'SystemDrive', 'LOCALLOW') if k in env]
    for rule in RULES:
        for target in rule.targets:
            expanded = paths.expand(target.path, env)
            if expanded is None:
                problems.append(f'{rule.id}: unknown variable in {target.path}')
                continue
            depth = len([p for p in ntpath.splitdrive(expanded)[1].split('\\') if p])
            if paths.is_drive_root(expanded) or depth < 1:
                problems.append(f'{rule.id}: target too shallow: {expanded}')
            if not any(paths.is_within(expanded, b) for b in bases):
                problems.append(f'{rule.id}: target outside known bases: {expanded}')
            for k in ('USERPROFILE', 'LOCALAPPDATA', 'APPDATA', 'SystemRoot', 'ProgramData'):
                if k in env and paths.key(expanded) == paths.key(env[k]):
                    problems.append(f'{rule.id}: target is a base folder itself: {expanded}')
    return problems
