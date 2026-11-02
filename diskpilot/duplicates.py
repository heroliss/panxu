"""Bounded local SHA-256 evidence, independent of any model or delete permission."""
from collections import Counter
import hashlib
import os
from pathlib import Path
import stat
import time

from .scanner import (_ancestor_project, _checked_root, _classification,
                      _metadata_signature)
from .simulation import AppError, canonical, is_reparse, is_user_protected


_CLOUD_ATTRIBUTES = 0x1000 | 0x40000 | 0x400000
_CHUNK = 1024 * 1024


def _stamp(info, *, cross_api=False):
    # On current Windows Python, path lstat and handle fstat can expose
    # different ctime semantics. Retain ctime for path-to-path validation and
    # compare all stable identity/content fields across path and handle APIs.
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, None if cross_api and os.name == 'nt' else info.st_ctime_ns,
            info.st_nlink, getattr(info, 'st_file_attributes', 0))


def _entity(info, path):
    # Zero inode is unknown, never evidence that two paths share one entity.
    return ('file', info.st_dev, info.st_ino) if info.st_ino else ('path', canonical(path))


def verify_duplicates(items, max_bytes=512 * 1024 * 1024, *, max_items=100,
                      patterns=(), cancel=None, progress=None, deadline=None):
    """Size groups then SHA-256, without a second byte-comparison pass.

    The old entry keeps its 100-file/default 512-MiB limits. The independent
    directory job supplies a bounded candidate limit. Digest reuse is limited
    to verified hardlink identities within this invocation, never across jobs.
    """
    if type(max_items) is not int or not 1 <= max_items <= 100000:
        raise AppError('文件校验条目预算无效。')
    if not isinstance(items, (list, tuple)) or len(items) > max_items:
        raise AppError(f'每次最多校验 {max_items} 个文件。')
    if type(max_bytes) is not int or not 0 <= max_bytes <= 8 * 1024 ** 3:
        raise AppError('文件读取预算必须在0到8 GiB之间。')
    candidates, warnings, groups = [], [], []
    skipped = Counter()
    bytes_read = 0
    cancelled = timed_out = False
    last_progress = 0.0

    def stopped():
        nonlocal cancelled, timed_out
        if cancel is not None and (cancel() if callable(cancel) else cancel.is_set()):
            cancelled = True
        if deadline is not None and time.monotonic() >= deadline:
            timed_out = True
        return cancelled or timed_out

    def skip(reason, message):
        skipped[reason] += 1
        if len(warnings) < 40:
            warnings.append(message)

    def current_patterns():
        return patterns() if callable(patterns) else patterns

    def checked(item, expected=None):
        path = Path(item['path'])
        _checked_root(str(path.parent))
        info = path.lstat()
        if getattr(info, 'st_file_attributes', 0) & _CLOUD_ATTRIBUTES:
            raise AppError('可能是云端占位或离线文件，未触发下载。', 'cloud_placeholder')
        if is_reparse(info):
            raise AppError('包含链接或重解析点，已跳过。', 'reparse_point')
        if not stat.S_ISREG(info.st_mode):
            raise AppError('不是普通文件，已跳过。', 'not_regular')
        if (is_user_protected(item, current_patterns())
                or _classification(str(path), _ancestor_project(str(path.parent)))['risk'] == 'protected'):
            raise AppError('受保护，未读取内容。', 'protected')
        if (item.get('signature') != _metadata_signature(str(path), info)
                or expected is not None and _stamp(info) != _stamp(expected)):
            raise AppError('在扫描后发生变化，未确认。', 'changed')
        return path, info

    for item in items:
        if stopped():
            break
        if item.get('risk') == 'protected':
            skip('protected', item.get('name', '文件') + ' 受保护，未读取内容。')
            continue
        if item.get('details', {}).get('synthetic'):
            skip('synthetic', '演练项目不读取真实文件；请扫描自建测试目录校验重复内容。')
            continue
        if item.get('kind') != 'file':
            continue
        try:
            path, info = checked(item)
            candidates.append((item, path, info))
        except (OSError, ValueError) as exc:
            skip(exc.code if isinstance(exc, AppError) else 'unavailable',
                 item.get('name', '文件') + ' ' + (str(exc) if isinstance(exc, AppError) else '无法读取，未确认。'))

    size_groups = {}
    for row in candidates:
        size_groups.setdefault(row[2].st_size, []).append(row)
    candidate_files = sum(len(rows) for rows in size_groups.values() if len(rows) > 1)
    hashed_files = checked_files = 0

    def notify_hash(*, force=False):
        nonlocal last_progress
        now = time.monotonic()
        if progress is not None and (force or now - last_progress >= .1):
            progress(dict(phase='hashing', message='正在计算同样大小文件的 SHA-256',
                          candidate_files=candidate_files, checked_files=checked_files,
                          hashed_files=hashed_files, bytes_read=bytes_read, max_bytes=max_bytes))
            last_progress = now

    notify_hash(force=True)
    for size, rows in sorted(size_groups.items()):
        if len(rows) < 2:
            continue
        if stopped():
            break
        by_identity, by_hash, digest_cache = {}, {}, {}
        for item, path, before in rows:
            if stopped():
                break
            ident = _entity(before, path)
            try:
                checked(item, before)
                by_identity.setdefault(ident, []).append((item, before))
                sha = digest_cache.get(ident)
                if sha is None:
                    if max_bytes - bytes_read < size:
                        skip('byte_budget', item['name'] + ' 超过本次读取预算，未完整校验。')
                        continue
                    h, count = hashlib.sha256(), 0
                    with path.open('rb') as stream:
                        opened = os.fstat(stream.fileno())
                        if _stamp(opened, cross_api=True) != _stamp(before, cross_api=True):
                            raise AppError('打开时发生变化，未读取内容。', 'changed')
                        while count < size:
                            if stopped():
                                break
                            block = stream.read(min(_CHUNK, size - count))
                            bytes_read += len(block)
                            count += len(block)
                            notify_hash()
                            if not block:
                                break
                            h.update(block)
                        after = os.fstat(stream.fileno())
                    checked(item, before)
                    if stopped():
                        break
                    if (count != size or _stamp(after) != _stamp(opened)
                            or _stamp(after, cross_api=True) != _stamp(before, cross_api=True)):
                        raise AppError('读取时发生变化，未确认。', 'changed')
                    sha = h.hexdigest()
                    digest_cache[ident] = sha
                    hashed_files += 1
                checked_files += 1
                by_hash.setdefault(sha, []).append((item, ident, before))
            except (OSError, ValueError) as exc:
                skip(exc.code if isinstance(exc, AppError) else 'unavailable',
                     item['name'] + ' ' + (str(exc) if isinstance(exc, AppError) else '读取时变化或被占用，未确认。'))
            notify_hash()
        if stopped():
            break  # Keep earlier completed groups, not this unfinished group.
        # A later candidate can change an earlier path. Check returned members
        # once more; this is metadata, not another pass over file contents.
        valid = {}
        for item, _, before in rows:
            if stopped():
                break
            try:
                checked(item, before)
                valid[item['id']] = True
            except (OSError, ValueError) as exc:
                skip(exc.code if isinstance(exc, AppError) else 'unavailable',
                     item['name'] + ' 在本组复核时无法确认，已排除。')
        if stopped():
            break
        for ident, shared in by_identity.items():
            shared = [item for item, _ in shared if valid.get(item['id'])]
            if len(shared) > 1 and ident[0] == 'file':
                groups.append(dict(type='hardlink', items=shared, size_bytes=size, verified=True,
                    verification='file_identity', reclaimable_bytes=None,
                    description='这些路径共享同一个文件实体，不是独立副本。'))
        for sha, matches in by_hash.items():
            matches = [(item, ident) for item, ident, _ in matches if valid.get(item['id'])]
            if len(matches) > 1 and len({ident for _, ident in matches}) > 1:
                groups.append(dict(type='content', sha256=sha, items=[item for item, _ in matches],
                    size_bytes=size, verified=True, verification='sha256',
                    physical_file_count=len({ident for _, ident in matches}), reclaimable_bytes=None,
                    description='大小与 SHA-256 一致；用途和引用仍需确认，没有自动保留或删除决定。'))
    return dict(groups=groups, warnings=list(dict.fromkeys(warnings)), bytes_read=bytes_read,
                candidate_files=candidate_files, checked_files=checked_files, hashed_files=hashed_files,
                skipped=dict(skipped), partial=bool(skipped or cancelled or timed_out),
                cancelled=cancelled, timed_out=timed_out, algorithm='sha256')
