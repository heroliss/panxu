"""Small, fixed Win32 observations; no subprocess, shell, writes or file contents.

These observations explain configuration and capacity. They do not produce
catalog targets, prove recoverability or grant actions. Native OS calls are
finite in number but cannot be interrupted mid-call; remote drives are skipped.
"""
from __future__ import annotations

import ctypes
from datetime import datetime, timezone
import json
import ntpath
import os
import re


MAX_PAGEFILES = 16
MAX_REGISTRY_VALUES = 7
MAX_RESULT_BYTES = 64 * 1024
SOURCES = {
    'memory': 'https://learn.microsoft.com/en-us/windows/win32/api/psapi/nf-psapi-getperformanceinfo',
    'pagefiles': 'https://learn.microsoft.com/en-us/windows/win32/api/psapi/nf-psapi-enumpagefilesw',
    'power': 'https://learn.microsoft.com/en-us/windows/win32/api/powerbase/nf-powerbase-getpwrcapabilities',
    'capacity': 'https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getdiskfreespaceexw',
    'recycle_bin': 'https://learn.microsoft.com/en-us/windows/win32/api/shellapi/nf-shellapi-shqueryrecyclebinw',
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _unknown(source, exc=None, reason='信息未取得，不能据此推断关闭、未配置或健康。'):
    result = dict(status='unknown', source=source, reason=reason)
    code = getattr(exc, 'winerror', None)
    if type(code) is int:
        result['winerror'] = code
    return result


def _observe(source, collect):
    try:
        result = collect()
        return dict(status='observed', source=source, **result)
    except (OSError, ValueError, AttributeError, NotImplementedError) as exc:
        return _unknown(source, exc)


def _finish(report):
    report['completed_at'] = _now()
    report['budget']['max_output_bytes'] = MAX_RESULT_BYTES
    if len(json.dumps(report, ensure_ascii=False).encode()) > MAX_RESULT_BYTES:
        for field in ('active_pagefiles', 'configuration'):
            if field in report:
                report[field] = _unknown('output_budget', reason='结果达到程序输出预算，已省略此部分；不能据此推断其状态。')
        report.update(status='partial', partial=True, output_truncated=True)
    return report


def _system_file_location(path):
    value = path.strip().strip('"')
    if value.startswith(('\\??\\', '\\\\?\\')):
        value = value[4:]
    drive = ntpath.splitdrive(value)[0].upper()
    filename = ntpath.basename(value).casefold()
    return dict(volume=drive if re.fullmatch(r'[A-Z]:', drive) else None,
                file=filename if filename in ('pagefile.sys', 'swapfile.sys', 'memory.dmp', 'hiberfil.sys') else 'custom_system_file')


def _configured_pagefile(value):
    match = re.fullmatch(r'(.+?)\s+(\d{1,10})\s+(\d{1,10})\s*', value)
    if not match:
        return dict(parsed=False, reason='配置格式未识别；未保留任意配置文本或推断其含义。')
    initial, maximum = int(match[2]), int(match[3])
    return dict(_system_file_location(match[1]), parsed=True, initial_mb=initial, maximum_mb=maximum,
                mode='system_managed_requested' if initial == maximum == 0 else 'specified_limits')


class _Performance(ctypes.Structure):
    _fields_ = [('cb', ctypes.c_uint32)] + [(name, ctypes.c_size_t) for name in (
        'CommitTotal', 'CommitLimit', 'CommitPeak', 'PhysicalTotal', 'PhysicalAvailable',
        'SystemCache', 'KernelTotal', 'KernelPaged', 'KernelNonpaged', 'PageSize')]
    _fields_ += [(name, ctypes.c_uint32) for name in ('HandleCount', 'ProcessCount', 'ThreadCount')]


class _Pagefile(ctypes.Structure):
    _fields_ = [('cb', ctypes.c_uint32), ('Reserved', ctypes.c_uint32)] + [
        (name, ctypes.c_size_t) for name in ('TotalSize', 'TotalInUse', 'PeakUsage')]


class _PowerPrefix(ctypes.Structure):
    # The first nine documented BOOLEAN fields are stable. An oversized tail
    # accommodates the full SYSTEM_POWER_CAPABILITIES output without interpreting
    # version-dependent reserved fields or HiberFileType's undocumented values.
    _fields_ = [(name, ctypes.c_ubyte) for name in ('PowerButtonPresent', 'SleepButtonPresent',
        'LidPresent', 'SystemS1', 'SystemS2', 'SystemS3', 'SystemS4', 'SystemS5', 'HiberFilePresent')]
    _fields_ += [('_tail', ctypes.c_ubyte * 128)]


class _RecycleInfo(ctypes.Structure):
    _fields_ = [('cbSize', ctypes.c_uint32), ('size', ctypes.c_int64), ('count', ctypes.c_int64)]


class NativeEvidence:
    """Fixed local API bindings. No caller-selected DLL, registry key or command."""

    @staticmethod
    def _dll(name):
        return ctypes.WinDLL(name, use_last_error=True, winmode=0x800)  # System32 only

    def memory(self):
        function = self._dll('psapi.dll').GetPerformanceInfo
        function.argtypes = [ctypes.POINTER(_Performance), ctypes.c_uint32]
        function.restype = ctypes.c_int
        info = _Performance()
        info.cb = ctypes.sizeof(info)
        if not function(ctypes.byref(info), info.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        if not 0 < info.PageSize <= 1024 * 1024:
            raise ValueError('invalid_page_size')
        return dict(page_size_bytes=info.PageSize, commit_current_bytes=info.CommitTotal * info.PageSize,
            commit_limit_bytes=info.CommitLimit * info.PageSize, commit_peak_since_boot_bytes=info.CommitPeak * info.PageSize,
            physical_total_bytes=info.PhysicalTotal * info.PageSize, physical_available_bytes=info.PhysicalAvailable * info.PageSize,
            note='提交峰值仅为本次启动后的峰值；一次快照不能代表长期负载或确定最佳分页大小。')

    def pagefiles(self, page_size, limit=MAX_PAGEFILES):
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(_Pagefile), ctypes.c_wchar_p)
        items, truncated = [], False

        def collect(context, pointer, filename):
            nonlocal truncated
            if len(items) >= limit:
                truncated = True
                return 0
            value = pointer.contents
            items.append(dict(_system_file_location(filename or ''), path=(filename or '')[:260], allocated_pages=value.TotalSize,
                path_truncated=len(filename or '') > 260,
                used_pages=value.TotalInUse, peak_used_pages=value.PeakUsage,
                allocated_bytes=value.TotalSize * page_size if page_size else None,
                used_bytes=value.TotalInUse * page_size if page_size else None,
                peak_used_bytes=value.PeakUsage * page_size if page_size else None))
            return 1

        callback = callback_type(collect)
        function = self._dll('psapi.dll').EnumPageFilesW
        function.argtypes = [callback_type, ctypes.c_void_p]
        function.restype = ctypes.c_int
        success = function(callback, None)
        if not success and not truncated:
            raise ctypes.WinError(ctypes.get_last_error())
        return dict(items=items, complete=not truncated and not any(i['path_truncated'] for i in items), limit_reached=truncated,
                    units='pages_and_bytes' if page_size else 'pages_only',
                    note='活动分页枚举与注册表中的下次启动配置分开；此API不提供临时分页标志。')

    def power(self):
        function = self._dll('powrprof.dll').GetPwrCapabilities
        function.argtypes = [ctypes.POINTER(_PowerPrefix)]
        function.restype = ctypes.c_ubyte
        info = _PowerPrefix()
        if not function(ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        return dict(hibernation_supported=bool(info.SystemS4), hibernation_file_present=bool(info.HiberFilePresent),
                    note='能力与文件存在性不等于电源按钮、合盖或严重低电量动作已正确配置。')

    def registry_configuration(self):
        import winreg
        definitions = (
            (r'SYSTEM\CurrentControlSet\Control\Session Manager\Memory Management',
             [('PagingFiles', 'paging_files'), ('ExistingPageFiles', 'existing_page_files'), ('TempPageFile', 'temporary_pagefile_flag')]),
            (r'SYSTEM\CurrentControlSet\Control\CrashControl', [('CrashDumpEnabled', 'crash_dump_type'), ('DumpFile', 'dump_file')]),
            (r'SYSTEM\CurrentControlSet\Control\Power', [('HibernateEnabled', 'hibernate_enabled')]),
            (r'SYSTEM\CurrentControlSet\Control\Session Manager\Power', [('HiberbootEnabled', 'fast_startup_enabled')]),
        )
        result = {}
        for path, values in definitions:
            try:
                handle = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY)
            except OSError as exc:
                for name, alias in values:
                    result[alias] = _unknown('HKLM\\' + path + '\\' + name, exc)
                continue
            with handle:
                for name, alias in values:
                    source = 'HKLM\\' + path + '\\' + name
                    try:
                        value, value_type = winreg.QueryValueEx(handle, name)
                        if ((alias in ('paging_files', 'existing_page_files') and value_type != winreg.REG_MULTI_SZ)
                                or (alias == 'dump_file' and value_type not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ))
                                or (alias not in ('paging_files', 'existing_page_files', 'dump_file') and value_type != winreg.REG_DWORD)):
                            raise ValueError('unexpected_registry_type')
                        truncated = False
                        if value_type == winreg.REG_MULTI_SZ and isinstance(value, list):
                            truncated = len(value) > MAX_PAGEFILES or any(not isinstance(v, str) or len(v) > 260 for v in value)
                            value = [v[:260] for v in value[:MAX_PAGEFILES] if isinstance(v, str)]
                        elif value_type in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) and isinstance(value, str):
                            truncated, value = len(value) > 260, value[:260]
                        elif value_type != winreg.REG_DWORD or type(value) is not int:
                            raise ValueError('unexpected_registry_type')
                        if alias == 'paging_files':
                            value = [_configured_pagefile(v) for v in value]
                        elif alias == 'existing_page_files':
                            value = [_system_file_location(v) for v in value]
                        elif alias == 'dump_file':
                            value = _system_file_location(value)
                        result[alias] = dict(status='observed', source=source, value=value, truncated=truncated,
                                             interpreted=all(v['parsed'] for v in value) if alias == 'paging_files' else True)
                    except (OSError, ValueError) as exc:
                        result[alias] = _unknown(source, exc)
        return dict(values=result, note='注册表读数是配置证据；缺少值不等于关闭。配置与活动文件不一致也不能单独证明必须重启或某个故障原因。')

    def volume(self, path):
        kernel = self._dll('kernel32.dll')
        drive, _ = ntpath.splitdrive(path)
        drive_root = drive + '\\'
        drive_type = kernel.GetDriveTypeW
        drive_type.argtypes, drive_type.restype = [ctypes.c_wchar_p], ctypes.c_uint32
        kind = drive_type(drive_root)
        if kind not in (2, 3, 6):
            # Excludes remote/disconnected/CD drives before filesystem or shell
            # calls that could initiate network I/O or a media spin-up.
            return dict(root=drive_root, drive_type={0: 'unknown', 1: 'unavailable', 4: 'remote', 5: 'optical'}.get(kind, 'unsupported'),
                        capacity=_unknown('GetDiskFreeSpaceExW', reason='该类型不在本工具的本地卷查询范围。'),
                        recycle_bin=_unknown('SHQueryRecycleBinW', reason='已跳过不受支持或远程卷，不推断回收站可用。'))
        root_buffer = ctypes.create_unicode_buffer(1024)
        get_root = kernel.GetVolumePathNameW
        get_root.argtypes, get_root.restype = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32], ctypes.c_int
        if not get_root(path, root_buffer, len(root_buffer)):
            raise ctypes.WinError(ctypes.get_last_error())
        root = root_buffer.value
        available, total, free = ctypes.c_uint64(), ctypes.c_uint64(), ctypes.c_uint64()
        capacity_call = kernel.GetDiskFreeSpaceExW
        capacity_call.argtypes = [ctypes.c_wchar_p] + [ctypes.POINTER(ctypes.c_uint64)] * 3
        capacity_call.restype = ctypes.c_int

        def capacity():
            if not capacity_call(root, ctypes.byref(available), ctypes.byref(total), ctypes.byref(free)):
                raise ctypes.WinError(ctypes.get_last_error())
            return dict(total_bytes=total.value, free_bytes=free.value, available_to_current_user_bytes=available.value)

        def recycle():
            if kind != 3:
                return dict(query_succeeded=False, reason='仅对本地固定卷查询回收站。', size_bytes=None, item_count=None)
            info = _RecycleInfo()
            info.cbSize = ctypes.sizeof(info)
            function = self._dll('shell32.dll').SHQueryRecycleBinW
            function.argtypes, function.restype = [ctypes.c_wchar_p, ctypes.POINTER(_RecycleInfo)], ctypes.c_int32
            hresult = function(root, ctypes.byref(info))
            return dict(query_succeeded=hresult == 0, hresult=f'0x{hresult & 0xffffffff:08X}',
                        size_bytes=info.size if hresult == 0 else None, item_count=info.count if hresult == 0 else None)

        return dict(root=root, drive_type={2: 'removable', 3: 'fixed', 6: 'ramdisk'}[kind],
                    capacity=_observe('GetDiskFreeSpaceExW', capacity), recycle_bin=_observe('SHQueryRecycleBinW', recycle))


def system_configuration(section='memory_power', *, backend=None, platform=None):
    if section != 'memory_power':
        raise ValueError('只支持memory_power配置概览')
    report = dict(collected_at=_now(), read_only=True, actions=[], source='fixed_win32_and_registry',
        scope='machine_memory_power_configuration_only', section=section,
        budget=dict(max_pagefiles=MAX_PAGEFILES, max_registry_values=MAX_REGISTRY_VALUES, max_core_snapshot_calls=3,
                    reads_file_contents=False, native_calls_interruptible=False),
        warnings=['未检查电源按钮/低电量动作、当前转储是否可成功写出、长期内存压力或磁盘健康；不产生可操作对象。'])
    if (platform or os.name) != 'nt':
        report.update(status='unsupported', partial=True, reason='此只读证据仅支持Windows。')
        return _finish(report)
    backend = backend or NativeEvidence()
    report['memory'] = _observe('GetPerformanceInfo', backend.memory)
    report['active_pagefiles'] = _observe('EnumPageFilesW', lambda: backend.pagefiles(report['memory'].get('page_size_bytes')))
    report['power_capabilities'] = _observe('GetPwrCapabilities', backend.power)
    report['configuration'] = _observe('fixed_HKLM_values', backend.registry_configuration)
    report['partial'] = (any(report[key]['status'] == 'unknown' for key in ('memory', 'active_pagefiles', 'power_capabilities', 'configuration'))
        or not report['active_pagefiles'].get('complete', False)
        or any(v['status'] == 'unknown' or v.get('truncated') or v.get('interpreted') is False for v in report['configuration'].get('values', {}).values()))
    report['status'] = 'partial' if report['partial'] else 'observed'
    report['sources'] = {key: SOURCES[key] for key in ('memory', 'pagefiles', 'power')}
    return _finish(report)


def validate_volume_path(path):
    if not isinstance(path, str) or len(path) > 32767 or '\x00' in path:
        raise ValueError('需要任务中的有效本地位置')
    drive, tail = ntpath.splitdrive(path)
    if len(drive) != 2 or drive[1] != ':' or drive[0].upper() not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ' or not tail.startswith(('\\', '/')):
        raise ValueError('卷查询仅支持任务中的本地盘符位置，不查询网络或设备路径')


def volume_snapshot(path, *, backend=None, platform=None):
    # Only the harness supplies this path from an existing scope ID. This check
    # is additional defence, not an arbitrary path-query endpoint.
    validate_volume_path(path)
    report = dict(collected_at=_now(), read_only=True, actions=[], source='fixed_win32_volume_query',
        scope='selected_location_volume_metadata_only',
        budget=dict(max_volumes=1, max_native_calls=4, reads_file_contents=False, enumerates_recycle_items=False,
                    native_calls_interruptible=False),
        warnings=['回收站统计成功不证明未来删除会进入回收站、策略已开启或文件可恢复；回收站不是独立备份。',
                  '容量不说明物理盘数量、SSD速度、健康或已清理效果；未读SMART、温度、固件和事件日志。'])
    if (platform or os.name) != 'nt':
        report.update(status='unsupported', partial=True, reason='此只读证据仅支持Windows。')
        return _finish(report)
    observed = _observe('local_volume_apis', lambda: (backend or NativeEvidence()).volume(path))
    report.update(observed)
    report['partial'] = (observed['status'] == 'unknown' or observed.get('capacity', {}).get('status') == 'unknown'
                         or observed.get('recycle_bin', {}).get('query_succeeded') is not True)
    report['status'] = 'partial' if report['partial'] else 'observed'
    report['sources'] = {key: SOURCES[key] for key in ('capacity', 'recycle_bin')}
    return _finish(report)
