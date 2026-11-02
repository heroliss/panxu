"""Fast, bounded metadata projections for fixed cleanup tools.

No file contents, browser history/cookies, shell, cleanup command or write is
used. Missing defaults are not proof of an empty or uninstalled application.
"""
from __future__ import annotations

from collections import deque
import os
import ntpath
from pathlib import Path
import re
import stat
import time

from .tool_launch import find_program


MAX_ENTRIES = 600
MAX_SECONDS = .15
MAX_DEPTH = 3
_REPARSE = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)
_OFFLINE = getattr(stat, 'FILE_ATTRIBUTE_OFFLINE', 0x1000) | 0x40000 | 0x400000


def _local_root(path):
    """Do not traverse a redirected ancestor while inspecting a known cache."""
    path = Path(path)
    if not path.is_absolute() or str(path).startswith(('\\\\', '\\??\\')):
        return False
    ancestors = [path, *path.parents]
    if len(ancestors) > 64:
        return False
    for ancestor in reversed(ancestors):
        try:
            info = ancestor.lstat()
        except FileNotFoundError:
            return True  # The caller reports an absent location, not an empty cache.
        except OSError:
            return False
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & (_REPARSE | _OFFLINE):
            return False
    return True


def measure_paths(paths, *, max_entries=MAX_ENTRIES, max_seconds=MAX_SECONDS, max_depth=MAX_DEPTH,
                  filename_prefix=None):
    """A lower bound when partial; never recursively enumerate an entire disk."""
    deadline = time.monotonic() + max_seconds
    pending = deque((Path(path), 0) for path in paths)
    seen, roots_seen, files, size, visited = set(), 0, 0, 0, 0
    skipped = errors = queued_entries = 0
    complete = True
    while pending:
        if visited >= max_entries or time.monotonic() >= deadline:
            complete = False
            break
        path, depth = pending.popleft()
        visited += 1
        try:
            if depth == 0 and not _local_root(path):
                skipped += 1; complete = False; continue
            info = path.lstat()
            attributes = getattr(info, 'st_file_attributes', 0)
            if stat.S_ISLNK(info.st_mode) or attributes & (_REPARSE | _OFFLINE):
                skipped += 1; complete = False; continue
            if depth == 0:
                roots_seen += 1
            if stat.S_ISREG(info.st_mode):
                if filename_prefix and not path.name.casefold().startswith(filename_prefix):
                    continue
                identity = (info.st_dev, info.st_ino) if info.st_ino else str(path.absolute())
                if identity not in seen:
                    seen.add(identity); files += 1; size += max(0, info.st_size)
                continue
            if not stat.S_ISDIR(info.st_mode):
                skipped += 1; continue
            if depth >= max_depth:
                complete = False; continue
            with os.scandir(path) as entries:
                for entry in entries:
                    if queued_entries >= max_entries or time.monotonic() >= deadline:
                        complete = False; break
                    queued_entries += 1
                    pending.append((Path(entry.path), depth + 1))
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            errors += 1; complete = False
    return dict(status='observed' if roots_seen and complete else 'partial' if roots_seen else
                       'unknown' if skipped or errors else 'not_found',
                complete=complete, observed_bytes=size, observed_files=files, visited_entries=visited,
                roots_found=roots_seen, skipped_entries=skipped, unavailable_entries=errors,
                budget=dict(max_entries=max_entries, max_seconds=max_seconds, max_depth=max_depth),
                reads_file_contents=False)


def _metric(key, label, value, unit='bytes', **extra):
    return dict(key=key, label=label, value=value, unit=unit, **extra)


def _presentation(kind, summary, *, status='unknown', metrics=(), items=(), warnings=(), complete=False, **extra):
    return dict(kind=kind, summary=summary, status=status, metrics=list(metrics), items=list(items),
                warnings=list(warnings), complete=complete, **extra)


def _known_base(name):
    raw = os.environ.get(name)
    if raw and Path(raw).is_absolute() and not raw.startswith(('\\\\', '\\\\?\\')):
        return Path(raw)
    return None


def _candidate_paths(tool_id, params):
    local, roaming, programdata, windir = (_known_base(key) for key in ('LOCALAPPDATA', 'APPDATA', 'ProgramData', 'WINDIR'))
    home = _known_base('USERPROFILE') or Path.home()
    env_cache = {'uv_cache': 'UV_CACHE_DIR', 'pip_cache': 'PIP_CACHE_DIR', 'npm_cache': 'npm_config_cache',
                 'hf_cache': 'HF_HUB_CACHE', 'cargo_cache': 'CARGO_TARGET_DIR', 'nuget_cache': 'NUGET_PACKAGES'}
    override = _known_base(env_cache.get(tool_id, ''))
    if override:
        return [override], 'environment'
    mapping = {
        'windows_temp': [p for p in (_known_base('TEMP'), windir / 'Temp' if windir else None) if p],
        'uv_cache': [local / 'uv/cache'] if local else [],
        'pip_cache': [local / 'pip/Cache'] if local else [],
        'npm_cache': [local / 'npm-cache'] if local else [],
        'gpu_cache': [local / p for p in ('D3DSCache', 'NVIDIA/DXCache', 'NVIDIA/GLCache', 'AMD/DxCache', 'AMD/GLCache')] if local else [],
        'thumbnail_cache': [local / 'Microsoft/Windows/Explorer'] if local else [],
        'diagnostic_files': [p for p in (local / 'CrashDumps' if local else None,
            programdata / 'Microsoft/Windows/WER/ReportArchive' if programdata else None,
            programdata / 'Microsoft/Windows/WER/ReportQueue' if programdata else None,
            windir / 'Minidump' if windir else None, windir / 'MEMORY.DMP' if windir else None) if p],
        'browser_models': [local / ('Google/Chrome/User Data/OptGuideOnDeviceModel' if params.get('browser') == 'chrome'
                            else 'Microsoft/Edge/User Data/OptGuideOnDeviceModel')] if local else [],
        'nuget_cache': [home / '.nuget/packages', local / 'NuGet/Cache' if local else None],
        'conda_cache': [home / 'anaconda3/pkgs', home / 'miniconda3/pkgs', local / 'conda/conda/pkgs' if local else None],
        'hf_cache': [(_known_base('HF_HOME') or home / '.cache/huggingface') / 'hub'],
        'ollama_models': [_known_base('OLLAMA_MODELS') or home / '.ollama/models'],
        'adobe_cache': [roaming / 'Adobe/Common/Media Cache' if roaming else None,
                        roaming / 'Adobe/Common/Media Cache Files' if roaming else None],
        'printer_queue': [windir / 'System32/spool/PRINTERS'] if windir else [],
    }
    if tool_id == 'unity_cache' and params.get('project_path'):
        return [Path(params['project_path']) / 'Library'], 'selected_project'
    if tool_id == 'cargo_cache' and params.get('project_path'):
        return [Path(params['project_path']) / 'target'], 'selected_project'
    if tool_id == 'steam_cache':
        program = find_program('steam')
        return [Path(program).parent / 'appcache/httpcache'] if program else [], 'detected_program'
    if tool_id == 'browser_cache' and local:
        browser = params.get('browser', 'edge')
        roots = {'chrome': local / 'Google/Chrome/User Data', 'edge': local / 'Microsoft/Edge/User Data'}
        base = roots.get(browser)
        if not base:
            return [], 'unavailable_profile'
        profile = params.get('profile', 'current')
        profiles = []
        if profile != 'current':
            if re.fullmatch(r'[\w .-]{1,80}', profile) and profile not in ('.', '..'):
                profiles = [base / profile]
        elif _local_root(base):
            try:
                with os.scandir(base) as entries:
                    for index, entry in enumerate(entries):
                        if index >= 50: break
                        if entry.name == 'Default' or re.fullmatch(r'Profile \d+', entry.name):
                            profiles.append(Path(entry.path))
            except OSError:
                pass
        return [p / relative for p in profiles for relative in ('Cache/Cache_Data', 'Code Cache', 'GPUCache')], 'recognized_profiles'
    return [p for p in mapping.get(tool_id, []) if p], 'default_candidates'


def candidate_paths(tool_id, params):
    paths, source = _candidate_paths(tool_id, params)
    volume = params.get('volume')
    if volume:
        paths = [path for path in paths if ntpath.splitdrive(str(path))[0].upper() == volume.upper()]
    return paths, source


def volume_presentation(tool_id, params, evidence):
    capacity, recycle = evidence.get('capacity') or {}, evidence.get('recycle_bin') or {}
    total, free = capacity.get('total_bytes'), capacity.get('available_to_current_user_bytes', capacity.get('free_bytes'))
    metrics = []
    if tool_id == 'recycle_bin':
        size, count = recycle.get('size_bytes'), recycle.get('item_count')
        valid = recycle.get('query_succeeded') is True and type(size) is int and type(count) is int and size >= 0 and count >= 0
        if valid:
            metrics.extend([_metric('recycle_bytes', '回收站占用', size, denominator=total, denominator_label='占此卷总容量'),
                            _metric('recycle_count', '回收站项目', count, 'count')])
        if type(total) is int: metrics.append(_metric('volume_total_bytes', '此卷总容量', total))
        if type(free) is int: metrics.append(_metric('volume_free_bytes', '此卷可用空间', free))
        return _presentation('recycle_bin', '当前用户在此卷的回收站' if valid else '暂未取得此卷回收站统计',
            status='observed' if valid else 'unknown', complete=valid, metrics=metrics,
            action_availability=dict(available=count > 0 if valid else None,
                                     reason='回收站已空' if valid and count == 0 else ''),
            recycle=dict(bytes=size if valid else None, count=count if valid else None,
                         volume_total_bytes=total, volume_free_bytes=free, volume=params.get('volume')))
    if type(free) is int: metrics.append(_metric('volume_free_bytes', '此卷可用空间', free, denominator=total, denominator_label='此卷总容量'))
    if type(total) is int: metrics.append(_metric('volume_total_bytes', '此卷总容量', total))
    return _presentation('volume', '所选卷空间', status='observed' if metrics else 'unknown', metrics=metrics, complete=bool(metrics))


def memory_presentation(tool_id, evidence):
    memory, power = evidence.get('memory') or {}, evidence.get('power_capabilities') or {}
    metrics, items = [], []
    if tool_id == 'hibernation':
        for key, label in [('hibernation_supported', '硬件支持休眠'), ('hibernation_file_present', '休眠文件')]:
            if type(power.get(key)) is bool: items.append(dict(label=label, value='是' if power[key] else '否'))
        return _presentation('power', '休眠能力与文件状态', status=power.get('status', 'unknown'), items=items,
                             complete=power.get('status') == 'observed')
    total, available = memory.get('physical_total_bytes'), memory.get('physical_available_bytes')
    commit, limit = memory.get('commit_current_bytes'), memory.get('commit_limit_bytes')
    for key, label, value, denominator in [('physical_available_bytes', '可用物理内存', available, total),
        ('physical_total_bytes', '物理内存', total, None), ('commit_current_bytes', '当前内存提交', commit, limit),
        ('commit_limit_bytes', '提交上限', limit, None)]:
        if type(value) is int: metrics.append(_metric(key, label, value, denominator=denominator))
    if tool_id == 'pagefile':
        for entry in (evidence.get('active_pagefiles') or {}).get('items', [])[:8]:
            items.append(dict(label=entry.get('volume') or '分页文件', value=entry.get('allocated_bytes'), unit='bytes'))
    return _presentation('memory' if tool_id == 'memory_usage' else 'pagefile', '当前内存与分页快照',
        status=memory.get('status', 'unknown'), metrics=metrics, items=items, complete=memory.get('status') == 'observed')


def inspect_metadata(tool_id, params):
    paths, source = candidate_paths(tool_id, params)
    if paths:
        measured = measure_paths(paths, filename_prefix='thumbcache_' if tool_id == 'thumbnail_cache' else None)
        found, complete = measured['roots_found'] > 0, measured['complete']
        metrics = [_metric('observed_bytes', '已统计占用' if complete else '已统计占用（至少）', measured['observed_bytes']),
                   _metric('observed_files', '已统计文件', measured['observed_files'], 'count')] if found else []
        warnings = []
        if not complete: warnings.append('部分位置未能检查。')
        if source == 'default_candidates': warnings.append('仅检查已知位置。')
        if source == 'recognized_profiles': warnings.append('按可识别的浏览器配置统计。')
        return dict(**measured, location_source=source), _presentation('cache',
            '缓存占用' if found else '暂时无法检查' if measured['status'] == 'unknown' else '未找到缓存位置', status=measured['status'],
            complete=complete and found, metrics=metrics, warnings=warnings,
            action_availability=dict(available=measured['observed_files'] > 0 if complete and found else None,
                                     reason='检查范围内没有可清理文件' if complete and found and not measured['observed_files'] else ''),
            items=[dict(label='检查位置', value=str(path)) for path in paths[:4]])
    if tool_id in ('browser_privacy', 'browser_cache', 'browser_models'):
        browser = params.get('browser', 'edge'); program = find_program(browser)
        return dict(status='observed' if program else 'unknown', program_found=bool(program), reads_private_data=False), _presentation(
            'application', '已找到对应浏览器' if program else '暂未找到对应浏览器',
            status='observed' if program else 'unknown', items=[dict(label='隐私数据', value='未读取内容')])
    path = params.get('virtual_disk_path') or params.get('target_path')
    if path:
        try:
            info = Path(path).lstat(); attributes = getattr(info, 'st_file_attributes', 0)
            if stat.S_ISLNK(info.st_mode) or attributes & (_REPARSE | _OFFLINE):
                return dict(status='unknown'), _presentation('location', '此位置需要在原程序中核对')
            metric = [_metric('backing_file_bytes', '虚拟磁盘文件大小', info.st_size)] if stat.S_ISREG(info.st_mode) else []
            return dict(status='observed', exists=True), _presentation('location', '目标位置已存在', status='observed', metrics=metric, complete=True)
        except FileNotFoundError:
            return dict(status='not_found', exists=False), _presentation('location', '目标位置尚未创建', status='not_found', complete=True)
        except OSError:
            return dict(status='unknown'), _presentation('location', '暂时无法读取此位置')
    return dict(status='unknown', reason='此工具尚无完整专用状态读取器。'), _presentation(
        'status', '请在对应程序中查看状态')
