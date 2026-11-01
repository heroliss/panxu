"""Bounded, read-only duplicate verification for explicitly selected local files."""
import hashlib
import os
from pathlib import Path
import stat

from .simulation import AppError, is_reparse


def verify_duplicates(items, max_bytes=512 * 1024 * 1024):
    if len(items) > 100:
        raise AppError('每次最多校验 100 个文件。')
    candidates, warnings, groups = [], [], []
    for item in items:
        if item.get('risk') == 'protected':
            warnings.append(item['name'] + ' 受保护，未读取内容。')
            continue
        if item.get('details', {}).get('synthetic'):
            warnings.append('演练项目不读取真实文件；请扫描自建测试目录校验重复内容。')
            continue
        if item['kind'] != 'file':
            continue
        path = Path(item['path'])
        try:
            if any(is_reparse(p.lstat()) for p in (path, *path.parents)):
                warnings.append(item['name'] + ' 包含链接，已跳过。')
                continue
            st = path.lstat()
            attrs = getattr(st, 'st_file_attributes', 0)
            if attrs & (0x1000 | 0x40000 | 0x400000):
                warnings.append(item['name'] + ' 可能是云端占位或离线文件，未触发下载。')
                continue
            if not stat.S_ISREG(st.st_mode):
                continue
            candidates.append((item, path, st))
        except OSError:
            warnings.append(item['name'] + ' 无法读取。')
    size_groups = {}
    for row in candidates:
        size_groups.setdefault(row[2].st_size, []).append(row)
    remaining = max_bytes
    for size, rows in size_groups.items():
        if len(rows) < 2:
            continue
        by_identity, by_hash = {}, {}
        for item, path, before in rows:
            ident = (before.st_dev, before.st_ino)
            by_identity.setdefault(ident, []).append(item)
            if remaining < size:
                warnings.append(item['name'] + ' 超过本次 512 MiB 读取预算，未完整校验。')
                continue
            remaining -= size
            try:
                h = hashlib.sha256()
                read_bytes = 0
                with path.open('rb') as stream:
                    opened = os.fstat(stream.fileno())
                    if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns):
                        raise OSError('changed')
                    for block in iter(lambda: stream.read(1024 * 1024), b''):
                        read_bytes += len(block)
                        if read_bytes > size:
                            raise OSError('changed')
                        h.update(block)
                    after = os.fstat(stream.fileno())
                current = path.lstat()
                expected = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                if read_bytes != size or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != expected or (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) != expected:
                    raise OSError('changed')
                by_hash.setdefault(h.hexdigest(), []).append((item, ident))
            except OSError:
                warnings.append(item['name'] + ' 在读取时变化或被占用，未确认。')
        for ident, shared in by_identity.items():
            if len(shared) > 1:
                groups.append(dict(type='hardlink', items=shared, size_bytes=size, verified=True,
                                   reclaimable_bytes=None, description='这些路径共享同一个文件实体，不是独立副本。'))
        for sha, matches in by_hash.items():
            if len(matches) > 1 and len({ident for _, ident in matches}) > 1:
                groups.append(dict(type='content', sha256=sha, items=[x for x, _ in matches], size_bytes=size, verified=True,
                                   reclaimable_bytes=None, description='SHA-256 内容一致；用途和引用仍需确认，没有自动保留/删除决定。'))
    return dict(groups=groups, warnings=list(dict.fromkeys(warnings)), bytes_read=max_bytes - remaining)
