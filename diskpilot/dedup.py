"""Independent directory duplicate search: metadata then SHA-256, without an LLM.

No catalog publication, scoring, deletion or deletion authorization lives here.
Results are local evidence at an observation time, not a filesystem lock.
"""
from collections import Counter
import copy
import hashlib
import os
from pathlib import Path
import stat
import time

from .duplicates import _CLOUD_ATTRIBUTES, verify_duplicates
from .scanner import (_ancestor_project, _checked_root, _identity,
                      _metadata_signature, _project_at, metadata_item)
from .simulation import AppError, canonical, is_reparse, is_user_protected, within


MAX_GROUPS = 200
MAX_RESULT_ITEMS = 2000
MAX_SECONDS = 1200
_DEFAULTS = dict(max_entries=25000, max_depth=16, max_bytes=2 * 1024 ** 3)
_BOUNDS = dict(max_entries=(1, 100000), max_depth=(1, 32), max_bytes=(1, 8 * 1024 ** 3))


def validate_duplicate_request(payload):
    """Validate synchronously without probing the filesystem or calling a model."""
    if not isinstance(payload, dict) or 'paths' not in payload or set(payload) - {'paths', *_DEFAULTS}:
        raise AppError('请选择目录，并使用有效的重复文件查找设置。', 'invalid_duplicate_request')
    paths = payload['paths']
    if (not isinstance(paths, list) or not 1 <= len(paths) <= 8
            or any(not isinstance(path, str) or not path.strip() or len(path) > 32767
                   or '\x00' in path or not os.path.isabs(os.path.expanduser(path.strip()))
                   or path.strip().startswith(('\\\\', '//')) for path in paths)):
        raise AppError('请选择1到8个本地绝对目录；不接受相对路径或网络共享。', 'invalid_duplicate_paths')
    result = dict(paths=[os.path.expanduser(path.strip()) for path in paths])
    for name, default in _DEFAULTS.items():
        value = payload.get(name, default)
        low, high = _BOUNDS[name]
        if type(value) is not int or not low <= value <= high:
            raise AppError(f'{name} 必须为 {low} 到 {high} 的整数。', 'invalid_duplicate_limit')
        result[name] = value
    return result


def _compact(item):
    result = {key: item[key] for key in ('id', 'kind', 'name', 'path', 'owner', 'category',
              'size_bytes', 'allocated_bytes', 'risk', 'actions', 'signature')}
    result['details'] = {key: copy.deepcopy(item['details'][key]) for key in
                         ('root', 'file_identity', 'timestamps', 'is_hardlink', 'link_count')}
    result['details']['source'] = 'local_duplicate_scan'
    return result


def run_duplicate_scan(payload, *, patterns=(), cancel=None, progress=None):
    request = validate_duplicate_request(payload)
    deadline = time.monotonic() + MAX_SECONDS
    skipped, files, directories, roots, warnings = Counter(), {}, [], [], []
    scanned_entries = directory_count = 0
    cancelled = limited = timed_out = False
    last_progress = 0.0

    def stopped():
        nonlocal cancelled, timed_out
        if cancel is not None and (cancel() if callable(cancel) else cancel.is_set()):
            cancelled = True
        if time.monotonic() >= deadline:
            timed_out = True
        return cancelled or timed_out

    def skip(reason, path, message):
        skipped[reason] += 1
        if len(warnings) < 40:
            warnings.append(message + '：' + str(path))

    def current_patterns():
        return patterns() if callable(patterns) else patterns

    def notify(path, force=False):
        nonlocal last_progress
        now = time.monotonic()
        if progress is not None and (force or now - last_progress >= .1):
            progress(dict(phase='scanning', message='正在按大小筛选重复文件候选',
                          current_path=str(path), scanned_entries=scanned_entries,
                          files_seen=len(files), directory_count=directory_count,
                          max_entries=request['max_entries'], bytes_read=0))
            last_progress = now

    checked_roots = []
    for raw in request['paths']:
        if stopped():
            break
        try:
            absolute, info = _checked_root(raw)
            if getattr(info, 'st_file_attributes', 0) & _CLOUD_ATTRIBUTES:
                skip('cloud_placeholder', absolute, '云端占位或离线目录已跳过，未触发下载')
                continue
            checked_roots.append((absolute, info))
        except (OSError, ValueError):
            skip('unavailable', raw, '目录无法读取或包含链接')
    # Check raw chains before canonical overlap removal: link/../safe cannot
    # be hidden by normalization or by an overlapping parent root.
    for absolute, info in sorted(checked_roots, key=lambda row: (len(canonical(row[0])), canonical(row[0]))):
        if not any(within(absolute, prior) for prior in roots):
            roots.append(absolute)
            directories.append((absolute, info, 0, _ancestor_project(absolute), None, absolute))
    if not roots and not cancelled and not timed_out:
        raise AppError('没有可读取的本地目录；请检查路径、权限、云端状态和链接。', 'duplicate_scan_failed')
    directory_stamps = []
    while directories and not stopped() and not limited:
        path, before, depth, project, parent_id, root = directories.pop()
        try:
            absolute, current = _checked_root(path)
            if (_identity(current, path) != _identity(before, path)
                    or current.st_mtime_ns != before.st_mtime_ns):
                skip('changed', path, '目录在检查时变化，已跳过')
                continue
            if getattr(current, 'st_file_attributes', 0) & _CLOUD_ATTRIBUTES:
                skip('cloud_placeholder', path, '云端占位或离线目录已跳过，未触发下载')
                continue
            project = project or _project_at(path)
            row = metadata_item(path, current, root=root, depth=depth,
                                parent_id=parent_id, project=project, is_dir=True)
            directory_count += 1
            # A protected volume root may be browsed into ordinary child
            # locations; protected application/project subtrees stay unread.
            volume_root = Path(path).parent == Path(path)
            if is_user_protected(row, current_patterns()) or row['risk'] == 'protected' and not volume_root:
                skip('protected', path, '受保护目录已跳过')
                continue
            if depth >= request['max_depth']:
                skip('depth_limit', path, '达到目录深度预算，未展开')
                continue
            directory_stamps.append((path, current))
            with os.scandir(absolute) as entries:
                for entry in entries:
                    if stopped():
                        break
                    if scanned_entries >= request['max_entries']:
                        limited = True
                        break
                    scanned_entries += 1
                    notify(entry.path)
                    try:
                        # Windows DirEntry.stat may expose dev/ino/nlink as
                        # zero. lstat supplies the real file identity without
                        # following a link; it must match later fstat checks.
                        info = os.lstat(entry.path)
                        if getattr(info, 'st_file_attributes', 0) & _CLOUD_ATTRIBUTES:
                            skip('cloud_placeholder', entry.path, '云端占位或离线文件已跳过，未触发下载')
                            continue
                        if is_reparse(info):
                            skip('reparse_point', entry.path, '链接或重解析点已跳过')
                            continue
                        if stat.S_ISDIR(info.st_mode):
                            directories.append((entry.path, info, depth + 1, project, row['id'], root))
                        elif stat.S_ISREG(info.st_mode):
                            item = metadata_item(entry.path, info, root=root, depth=depth + 1,
                                parent_id=row['id'], project=project, is_dir=False)
                            if item['risk'] == 'protected' or is_user_protected(item, current_patterns()):
                                skip('protected', entry.path, '受保护文件已跳过，未读取内容')
                                continue
                            item['signature'] = _metadata_signature(entry.path, info)
                            item['actions'] = ['delete']  # evidence, not an execution/scope token
                            files[item['id']] = _compact(item)
                        else:
                            skip('not_regular', entry.path, '非普通文件已跳过')
                    except (OSError, ValueError):
                        skip('unavailable', entry.path, '条目无法读取，已跳过')
        except (OSError, ValueError):
            skip('unavailable', path, '目录无法读取或已变化，已跳过')
    if limited:
        warnings.append('达到条目预算；尚未检查的目录和文件不代表不存在重复。')
    notify('', True)
    verification = verify_duplicates(list(files.values()), request['max_bytes'], max_items=request['max_entries'],
        patterns=patterns, cancel=cancel, progress=progress, deadline=deadline)
    # Changes make scope coverage uncertain even when already verified files
    # remain useful evidence. This is bounded metadata, not another byte read.
    for path, before in directory_stamps:
        if stopped():
            break
        try:
            _, after = _checked_root(path)
            if (_identity(after, path), after.st_mtime_ns, after.st_ctime_ns) != (
                    _identity(before, path), before.st_mtime_ns, before.st_ctime_ns):
                skip('changed_directory', path, '目录在查找期间变化，范围可能不完整')
        except (OSError, ValueError):
            skip('changed_directory', path, '目录在查找期间变为不可访问或链接')
    cancelled = cancelled or verification['cancelled']
    timed_out = timed_out or verification['timed_out']
    groups = sorted(verification['groups'], key=lambda group:
                    (-(group['size_bytes'] * max(0, len(group['items']) - 1)), group['type'], group.get('sha256', '')))
    total_group_items = sum(len(group['items']) for group in groups)
    output, returned_items = [], 0
    for group in groups[:MAX_GROUPS]:
        if returned_items >= MAX_RESULT_ITEMS:
            break
        # The result cap also bounds copying: a single huge identical group
        # must not be deep-copied in full just to display its first 2000 paths.
        members = sorted(group['items'], key=lambda item: canonical(item['path']))
        group = {key: copy.deepcopy(value) for key, value in group.items() if key != 'items'}
        identity = (group['type'] + ':' + str(group['size_bytes']) + ':' +
                    (group.get('sha256') or members[0]['details']['file_identity']))
        group['group_id'] = 'duplicate-' + hashlib.sha256(identity.encode()).hexdigest()[:32]
        group['items_total'] = len(members)
        group['items'] = copy.deepcopy(members[:MAX_RESULT_ITEMS - returned_items])
        group['omitted_items'] = group['items_total'] - len(group['items'])
        returned_items += len(group['items'])
        output.append(group)
    truncated = len(output) < len(groups) or returned_items < total_group_items
    if truncated:
        warnings.append('结果展示达到200组或2000个路径上限；汇总保留真实数量，请缩小目录继续查看。')
    if timed_out:
        warnings.append('达到本次20分钟工作预算；未检查部分不代表没有重复。')
    if cancelled:
        warnings.append('查找已取消；仅展示取消前已核验的部分，不代表目录已检查完。')
    skipped.update(verification['skipped'])
    warnings.extend(verification['warnings'])
    partial = bool(skipped or limited or cancelled or timed_out or verification['partial'] or truncated)
    summary = dict(scanned_entries=scanned_entries, files_seen=len(files), directory_count=directory_count,
        candidate_files=verification['candidate_files'], checked_files=verification['checked_files'],
        hashed_files=verification['hashed_files'], content_groups=sum(g['type'] == 'content' for g in groups),
        hardlink_groups=sum(g['type'] == 'hardlink' for g in groups), total_groups=len(groups),
        grouped_paths=total_group_items, returned_groups=len(output), returned_files=returned_items,
        omitted_groups=len(groups) - len(output), omitted_group_items=total_group_items - returned_items,
        skipped=dict(skipped), entry_limit_reached=limited, timed_out=timed_out)
    result = dict(source='local_duplicate_scan', algorithm='sha256',
        status='cancelled' if cancelled else 'partial' if partial else 'completed', partial=partial,
        roots=roots, summary=summary, groups=output, warnings=list(dict.fromkeys(warnings))[:80],
        bytes_read=verification['bytes_read'], result_truncated=truncated,
        limits=dict(request, max_groups=MAX_GROUPS, max_result_items=MAX_RESULT_ITEMS, max_seconds=MAX_SECONDS),
        read_only=True, llm_used=False, actual_reclaimed_bytes=0)
    if progress is not None:
        progress(dict(phase='finished', message='查找已取消' if cancelled else '重复文件查找结束',
                      status=result['status'], summary=summary, bytes_read=result['bytes_read']))
    return result
