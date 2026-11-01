"""Read-only Windows inventory. No stop, uninstall, registry write or startup edit API."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _item(kind, key, name, owner, category, action, details, path='', protected=False, reason=''):
    details = dict(details)
    details['decision_context'] = {
        'ownership': 'system' if protected else 'application',
        'personal_choice_required': False,
        'regenerable': False,
        'classification_source': {
            'startup': 'windows_startup_registration',
            'service': 'windows_service_registration',
            'process': 'windows_process_metadata',
            'software': 'windows_uninstall_registration',
        }[kind],
        # These flags support recommendations, not execution authorization.
        'auto_select': False, 'advisory_only': True,
        'user_data_deletion_authorized': False,
    }
    details['auto_select'] = False
    details['file_deletion_authorized'] = False
    return dict(id=kind + '-' + digest(key)[:24], kind=kind, name=name, path=path or key,
                owner=owner, category=category, size_bytes=0, allocated_bytes=None,
                risk='protected' if protected else 'review',
                recommendation='保留' if protected else '了解影响后模拟',
                reason=reason, impact={'startup': '禁用后相应功能不会随登录自动就绪。',
                                       'service': '停止可能影响软件功能和其他依赖它的程序。',
                                       'process': '关闭程序可能丢失未保存工作；工作集不等于可释放物理内存。',
                                       'software': '正规卸载可能影响组件、配置及关联程序；不等于删除安装目录。'}[kind],
                actions=[] if protected else [action], signature=digest(key), details=details,
                evidence=['本机只读采集，时间：' + time.strftime('%Y-%m-%d %H:%M:%S'),
                          '本版本只写模拟状态，不实际执行此操作。'], simulated=False)


def startup_items():
    if os.name != 'nt':
        return [], ['开机启动项采集仅支持 Windows。']
    import winreg
    rows, warnings = [], []
    for hive, label in ((winreg.HKEY_CURRENT_USER, 'HKCU'), (winreg.HKEY_LOCAL_MACHINE, 'HKLM')):
        for key in (r'Software\Microsoft\Windows\CurrentVersion\Run', r'Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run'):
            try:
                with winreg.OpenKey(hive, key, 0, winreg.KEY_READ) as handle:
                    index = 0
                    while True:
                        try:
                            name, value, _ = winreg.EnumValue(handle, index)
                        except OSError:
                            break
                        index += 1
                        # Only a digest of launch command enters the catalog, never the command itself.
                        key_id = label + '\\' + key + '\\' + name
                        protected = any(s in name.casefold() for s in ('securityhealth', 'defender', 'windows security'))
                        item = _item('startup', key_id, name, '启动项注册信息', '开机启动', 'disable_startup',
                                     {'registry_location': label + '\\' + key, 'state': '已注册（实际启用状态待核对）',
                                      'command_digest': digest(str(value)), 'synthetic': False}, protected=protected,
                                     reason='这是注册的启动命令；是否被任务管理器禁用尚未确认，不能据此认定正在自动启动。')
                        item['signature'] = digest([key_id, str(value)])
                        rows.append(item)
            except FileNotFoundError:
                pass
            except OSError:
                warnings.append(label + ' 的部分启动项无法读取。')
    for root in (os.environ.get('APPDATA'), os.environ.get('PROGRAMDATA')):
        if not root:
            continue
        folder = Path(root) / 'Microsoft/Windows/Start Menu/Programs/Startup'
        try:
            for p in folder.iterdir():
                if p.name.casefold() == 'desktop.ini':
                    continue
                st = p.lstat()
                item = _item('startup', str(p), p.name, '启动文件夹', '开机启动', 'disable_startup',
                             {'state': '位于启动文件夹', 'synthetic': False}, path=str(p),
                             reason='只读取快捷方式/文件名，不跟随或运行目标。')
                item['signature'] = digest([str(p), st.st_size, st.st_mtime_ns])
                rows.append(item)
        except OSError:
            warnings.append('部分启动文件夹无法读取。')
    return rows, warnings


def service_items():
    if os.name != 'nt':
        return [], ['后台服务采集仅支持 Windows。']
    script = "[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); @(Get-CimInstance Win32_Service | Select-Object Name,DisplayName,State,StartMode,ProcessId,PathName) | ConvertTo-Json -Compress"
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                                capture_output=True, timeout=25, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode or len(result.stdout) > 3_000_000:
            return [], ['服务采集没有完成；未修改任何服务。']
        values = json.loads(result.stdout.decode('utf-8-sig'))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return [], ['服务信息暂时无法读取；可以稍后重试。']
    rows = []
    critical = {'windefend', 'rpcss', 'dcomlaunch', 'eventlog', 'bfe', 'mpssvc', 'schedule', 'winmgmt', 'samss', 'lsm', 'plugplay', 'power', 'dhcp', 'dnscache', 'nlasvc'}
    windows = os.environ.get('WINDIR', r'C:\Windows').casefold()
    for value in values if isinstance(values, list) else [values]:
        name, raw_path = str(value.get('Name', '')), str(value.get('PathName', ''))
        if not name:
            continue
        protected = name.casefold() in critical or windows in raw_path.casefold() or raw_path.casefold().startswith('\\systemroot')
        item = _item('service', name, value.get('DisplayName') or name,
                     'Windows / 系统目录' if protected else '第三方或归属待确认', '后台服务', 'stop_service',
                     {'service_name': name, 'state': value.get('State'), 'start_mode': value.get('StartMode'),
                      'pid': value.get('ProcessId'), 'image_digest': digest(raw_path), 'synthetic': False},
                     path='服务 / ' + name, protected=protected,
                     reason='按服务注册信息只读采集；第三方不代表可停用，依赖与用途需要进一步核对。')
        item['signature'] = digest([name, value.get('State'), value.get('StartMode'), raw_path])
        rows.append(item)
    return rows, []


def process_items():
    try:
        import psutil
    except ImportError:
        return [], ['缺少可选进程采集依赖 psutil；其他功能可继续使用。']
    rows = []
    windows = os.environ.get('WINDIR', r'C:\Windows').casefold()
    for process in psutil.process_iter(['pid', 'name', 'exe', 'create_time', 'memory_info']):
        try:
            data = process.info
            pid, exe = data['pid'], data.get('exe') or ''
            protected = pid in (0, 4, os.getpid()) or not exe or exe.casefold().startswith(windows)
            memory = data.get('memory_info')
            item = _item('process', f'{pid}:{data.get("create_time")}', data.get('name') or f'PID {pid}',
                         'Windows / 当前工具' if protected else '应用进程（用途待确认）', '常驻进程', 'stop_process',
                         {'pid': pid, 'create_time': data.get('create_time'), 'memory_bytes': memory.rss if memory else 0,
                          'state': '正在运行', 'synthetic': False}, path=exe or f'进程 / PID {pid}', protected=protected,
                         reason='只读工作集快照。占用高不表示无用；没有采集命令行、环境变量或进程内容。')
            rows.append(item)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    rows.sort(key=lambda x: x['details']['memory_bytes'], reverse=True)
    return rows[:300], ['进程清单为瞬时快照，最多显示 300 项；工作集含共享内存，不能相加当作可回收物理内存。']


def software_items():
    if os.name != 'nt':
        return [], ['已安装桌面软件采集仅支持 Windows。']
    import winreg
    rows, seen, warnings = [], set(), []
    for hive, label in ((winreg.HKEY_CURRENT_USER, '当前用户'), (winreg.HKEY_LOCAL_MACHINE, '整机')):
        for parent in (r'Software\Microsoft\Windows\CurrentVersion\Uninstall', r'Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'):
            try:
                with winreg.OpenKey(hive, parent, 0, winreg.KEY_READ) as root:
                    for n in range(winreg.QueryInfoKey(root)[0]):
                        sub = winreg.EnumKey(root, n)
                        try:
                            with winreg.OpenKey(root, sub) as reg:
                                def get(name, default=''):
                                    try:
                                        return winreg.QueryValueEx(reg, name)[0]
                                    except OSError:
                                        return default
                                name = str(get('DisplayName'))
                                if not name:
                                    continue
                                version, publisher = str(get('DisplayVersion')), str(get('Publisher'))
                                identity = [label, parent, sub]
                                if tuple(identity) in seen:
                                    continue
                                seen.add(tuple(identity))
                                protected = bool(get('SystemComponent', 0)) or any(s in name.casefold() for s in ('visual c++', '.net', 'webview2', 'driver', '驱动', 'windows sdk', 'windows software development kit'))
                                uninstall_present = bool(get('UninstallString'))
                                details = {'version': version, 'scope': label, 'has_uninstaller': uninstall_present,
                                           'estimated_install_bytes': int(get('EstimatedSize', 0) or 0) * 1024,
                                           'state': '已注册安装', 'usage': '使用情况未知', 'synthetic': False}
                                item = _item('software', json.dumps(identity), name, publisher or '发布者未知', '已安装软件', 'uninstall', details,
                                             path='已安装软件 / ' + label + ' / ' + name, protected=protected,
                                             reason='按桌面卸载注册信息采集，大小是安装器估计；没有读取卸载命令、账号信息或用户文件。')
                                item['signature'] = digest([identity, name, version, publisher, uninstall_present])
                                if not uninstall_present:
                                    item['actions'] = []
                                    item['recommendation'] = '没有已识别的卸载入口'
                                rows.append(item)
                        except (OSError, ValueError):
                            continue
            except OSError:
                pass
    warnings.append('首版仅枚举传统桌面卸载注册项；不宣称覆盖 Store 应用或已核实所有软件依赖。')
    return rows[:500], warnings


def inventory(section):
    if section == 'software':
        items, warnings = software_items()
    else:
        items, warnings = [], []
        for collector in (startup_items, service_items, process_items):
            new_items, notes = collector()
            items.extend(new_items)
            warnings.extend(notes)
        warnings.append('未枚举计划任务、驱动和所有自启动机制；本清单不是完整系统启动审计。')
    return dict(items=items, warnings=warnings, summary={'source': section, 'count': len(items), 'read_only': True})


def validate_system_items(items):
    from .simulation import AppError
    actual = [x for x in items if not x.get('details', {}).get('synthetic') and x['kind'] not in ('file', 'directory')]
    if not actual:
        return
    kinds = {x['kind'] for x in actual}
    lookup = {}
    for kind, collector in (('startup', startup_items), ('service', service_items), ('process', process_items), ('software', software_items)):
        if kind in kinds:
            rows, _ = collector()
            lookup.update({x['id']: x['signature'] for x in rows})
    if any(lookup.get(x['id']) != x['signature'] for x in actual):
        raise AppError('部分后台或软件项目已经变化，请刷新清单后重新预览。', 'target_changed', 409)


def disks():
    roots = [f'{letter}:\\' for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' if os.path.exists(f'{letter}:\\')] if os.name == 'nt' else ['/']
    result = []
    for root in roots:
        try:
            usage = shutil.disk_usage(root)
            result.append(dict(path=root, name=root, total_bytes=usage.total, used_bytes=usage.used, free_bytes=usage.free, source='real', read_only=True))
        except OSError:
            continue
    return result
