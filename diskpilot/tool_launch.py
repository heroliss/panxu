"""Host-owned launch destinations. Opening never submits a cleanup action."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import re
import subprocess
import threading

from .simulation import AppError


def _windows_directory(system=False):
    if os.name != 'nt':
        return None
    try:
        dll = ctypes.WinDLL('kernel32.dll', use_last_error=True, winmode=0x800)
        function = dll.GetSystemDirectoryW if system else dll.GetWindowsDirectoryW
        function.argtypes = [ctypes.c_wchar_p, ctypes.c_uint]
        function.restype = ctypes.c_uint
        value = ctypes.create_unicode_buffer(32768)
        length = function(value, len(value))
        return Path(value.value) if 0 < length < len(value) else None
    except (OSError, AttributeError):
        return None


def find_program(name):
    """Discover an exact local program, never execute a registry command line."""
    filenames = {'chrome': 'chrome.exe', 'edge': 'msedge.exe', 'firefox': 'firefox.exe',
                 'steam': 'steam.exe', 'conda': 'conda.exe', 'uv': 'uv.exe',
                 'pip': 'pip.exe', 'npm': 'npm.cmd', 'dotnet': 'dotnet.exe',
                 'cargo': 'cargo.exe', 'docker': 'docker.exe', 'ollama': 'ollama.exe',
                 'hf': 'hf.exe'}
    filename = filenames.get(name)
    if not filename:
        return None
    candidates = []
    if os.name == 'nt':
        try:
            import winreg
            key = 'SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\App Paths\\' + filename
            for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                    try:
                        with winreg.OpenKey(hive, key, 0, winreg.KEY_READ | view) as handle:
                            raw = winreg.QueryValueEx(handle, None)[0]
                        if isinstance(raw, str):
                            candidates.append(Path(os.path.expandvars(raw.strip().strip('"'))))
                    except OSError:
                        pass
        except ImportError:
            pass
    local, program, program86 = (os.environ.get(key) for key in ('LOCALAPPDATA', 'ProgramFiles', 'ProgramFiles(x86)'))
    relatives = {
        'chrome': ('Google/Chrome/Application/chrome.exe',),
        'edge': ('Microsoft/Edge/Application/msedge.exe',),
        'firefox': ('Mozilla Firefox/firefox.exe',),
        'steam': ('Steam/steam.exe',),
        'docker': ('Docker/Docker/Docker Desktop.exe',),
    }
    for base in (program, program86, local):
        if base:
            candidates.extend(Path(base) / relative for relative in relatives.get(name, ()))
    # Inspect known registrations before a bounded PATH fallback. In particular,
    # do not make every tools page wait for a stale UNC PATH entry.
    safe_directories = []
    for value in os.environ.get('PATH', '').split(os.pathsep)[:32]:
        try:
            folder = Path(value.strip('"'))
            if value and folder.is_absolute() and not str(folder).startswith(('\\\\', '\\??\\')) and folder != Path.cwd():
                safe_directories.append(folder)
        except (OSError, ValueError):
            continue
    candidates.extend(folder / filename for folder in safe_directories)
    for candidate in candidates:
        try:
            if not candidate.is_absolute() or str(candidate).startswith(('\\\\', '\\??\\')):
                continue
            if candidate.name.casefold() == filename.casefold() and candidate.is_file():
                return str(candidate)
            if name == 'docker' and candidate.name == 'Docker Desktop.exe' and candidate.is_file():
                return str(candidate)
        except OSError:
            pass
    return None


# Each descriptor has only a fixed destination; parameters never select a command.
_ENTRIES = {
    'recycle_bin': ('application', '打开回收站', 'explorer', ('shell:RecycleBinFolder',)),
    'disk_cleanup': ('application', '打开磁盘清理', 'cleanmgr.exe', ()),
    'power_options': ('application', '打开电源选项', 'control.exe', ('/name', 'Microsoft.PowerOptions')),
    'advanced_system': ('application', '打开高级系统设置', 'SystemPropertiesAdvanced.exe', ()),
    'system_protection': ('application', '打开系统保护', 'SystemPropertiesProtection.exe', ()),
    'uninstall_programs': ('application', '打开程序卸载', 'control.exe', ('appwiz.cpl',)),
    'services': ('application', '打开服务管理', 'mmc.exe', ('services.msc',)),
    'task_manager': ('application', '打开任务管理器', 'Taskmgr.exe', ()),
    'file_history': ('application', '打开文件历史记录', 'control.exe', ('/name', 'Microsoft.FileHistory')),
    'indexing': ('application', '打开索引选项', 'control.exe', ('/name', 'Microsoft.IndexingOptions')),
    'folder_options': ('application', '打开文件资源管理器选项', 'control.exe', ('folders',)),
    'store': ('application', '打开Microsoft Store', 'uri', ('ms-windows-store://home',)),
    'storage': ('settings', '打开存储设置', 'uri', ('ms-settings:storagesense',)),
    'storage_sense': ('settings', '打开存储感知', 'uri', ('ms-settings:storagepolicies',)),
    'delivery_optimization': ('settings', '打开传递优化', 'uri', ('ms-settings:delivery-optimization',)),
    'clipboard': ('settings', '打开剪贴板设置', 'uri', ('ms-settings:clipboard',)),
    'recent': ('settings', '打开最近使用记录设置', 'uri', ('ms-settings:personalization-start',)),
    'optional_features': ('settings', '打开可选功能', 'uri', ('ms-settings:optionalfeatures',)),
    'apps': ('settings', '打开已安装应用', 'uri', ('ms-settings:appsfeatures',)),
    'startup': ('settings', '打开启动应用', 'uri', ('ms-settings:startupapps',)),
    'printers': ('settings', '打开打印机与队列', 'uri', ('ms-settings:printers',)),
    'search_privacy': ('settings', '打开搜索权限', 'uri', ('ms-settings:search-permissions',)),
    'activity_privacy': ('settings', '打开活动历史', 'uri', ('ms-settings:privacy-activityhistory',)),
    'diagnostic_privacy': ('settings', '打开诊断数据设置', 'uri', ('ms-settings:privacy-feedback',)),
    'windows_backup': ('settings', '打开备份设置', 'uri', ('ms-settings:backup',)),
    'chrome_clear': ('application', '打开Chrome清理界面', 'chrome', ('chrome://settings/clearBrowserData',)),
    'edge_clear': ('application', '打开Edge清理界面', 'edge', ('edge://settings/clearBrowserData',)),
    'firefox_clear': ('application', '打开Firefox隐私设置', 'firefox', ('about:preferences#privacy',)),
    'chrome_settings': ('application', '打开Chrome设置', 'chrome', ('chrome://settings/',)),
    'edge_settings': ('application', '打开Edge设置', 'edge', ('edge://settings/',)),
    'steam': ('application', '打开Steam', 'steam', ()),
    'docker': ('application', '打开Docker Desktop', 'docker', ()),
    'explorer': ('application', '打开文件资源管理器', 'explorer', ()),
}


def entry_definition(entry_id):
    if entry_id not in _ENTRIES:
        raise AppError('这个程序入口不受支持。', 'unsupported_entrypoint')
    kind, label, _, _ = _ENTRIES[entry_id]
    return dict(entry_id=entry_id, kind=kind, target=entry_id, label=label)


def resolve_entry(entry_id, parameters=None):
    if entry_id not in _ENTRIES:
        raise AppError('这个程序入口不受支持。', 'unsupported_entrypoint')
    if parameters is not None and (not isinstance(parameters, dict) or set(parameters) - {'volume'}):
        raise AppError('程序入口参数不正确。', 'invalid_entrypoint')
    parameters = dict(parameters or {})
    if 'volume' in parameters and (not isinstance(parameters['volume'], str) or not re.fullmatch('[A-Z]:', parameters['volume'])):
        raise AppError('程序入口盘符不正确。', 'invalid_entrypoint')
    kind, label, program, arguments = _ENTRIES[entry_id]
    result = dict(entry_id=entry_id, parameters=parameters, kind=kind, label=label,
                  target=entry_id, available=False, reason='此入口仅在Windows中可用。')
    if os.name != 'nt':
        return result
    if program == 'uri':
        result.update(target=arguments[0], available=True, reason=None)
        return result
    if program == 'explorer':
        base = _windows_directory()
        executable = base / 'explorer.exe' if base else None
    elif program.lower().endswith('.exe'):
        base = _windows_directory(system=True)
        executable = base / program if base else None
    else:
        value = find_program(program)
        executable = Path(value) if value else None
        if program == 'docker' and executable and executable.name != 'Docker Desktop.exe':
            executable = None
    if not executable or not executable.is_file():
        result['reason'] = '未找到对应程序，可以先使用本工具查看状态和预览。'
        return result
    args = list(arguments)
    if entry_id == 'services':
        args = [str(executable.parent / 'services.msc')]
    if entry_id == 'disk_cleanup' and parameters.get('volume'):
        args = ['/d', parameters['volume'][0]]
    result.update(target=str(executable), arguments=args, available=True, reason=None)
    return result


def open_entry(entry):
    """Re-resolve the host-owned identity, ignoring supplied paths/arguments."""
    if not isinstance(entry, dict) or not isinstance(entry.get('entry_id'), str):
        raise AppError('请重新打开工具中的程序入口。', 'invalid_entrypoint')
    current = resolve_entry(entry['entry_id'], entry.get('parameters'))
    if not current['available']:
        raise AppError(current['reason'], 'entrypoint_unavailable')
    try:
        if _ENTRIES[current['entry_id']][2] == 'uri':
            os.startfile(current['target'])
        else:
            process = subprocess.Popen([current['target'], *current.get('arguments', [])], shell=False,
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            # Opening a GUI is asynchronous. Reap its handle without blocking the
            # request or leaving an unowned live Popen (ResourceWarning).
            threading.Thread(target=process.wait, daemon=True, name='registered-tool-launch').start()
    except OSError:
        raise AppError('未能打开对应程序，请检查它是否仍已安装。', 'entrypoint_unavailable') from None
    return dict(status='opened', applied=False, label=current['label'],
                message='已打开对应界面；本工具未提交清理或设置变更。')
