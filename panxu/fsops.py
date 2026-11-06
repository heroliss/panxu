"""File-system walking and deletion primitives.

Two deletion paths exist on purpose:

* ``delete_*``  permanently removes files. Only used for rule-vetted junk
  (temp files, caches) or when the user explicitly picks "直接删除".
* ``recycle``   moves user-selected items to the Recycle Bin.

Neither path ever follows a junction or symbolic link: links are skipped
while walking, and a link selected directly is removed as a link only.
"""
from __future__ import annotations

import os
import stat
import threading
import time
from dataclasses import dataclass, field

from . import winapi

REPARSE = winapi.FILE_ATTRIBUTE_REPARSE_POINT
MAX_RECORDED_FAILURES = 200


def long_path(path: str) -> str:
    """Prefix long Windows paths with \\\\?\\ so they work without LongPathsEnabled."""
    if not winapi.IS_WINDOWS or len(path) < 240 or path.startswith('\\\\?\\'):
        return path
    if path.startswith('\\\\'):
        return '\\\\?\\UNC\\' + path[2:]
    return '\\\\?\\' + os.path.abspath(path)


def entry_attrs(entry: os.DirEntry) -> int:
    try:
        return getattr(entry.stat(follow_symlinks=False), 'st_file_attributes', 0)
    except OSError:
        return 0


NAME_SURROGATE = 0x20000000  # reparse-tag bit: "this points at another name" (junction, symlink, ...)


def st_is_link(st: os.stat_result) -> bool:
    """Decide from an lstat result whether this entry is a link we must not cross.

    Cloud-file reparse points (OneDrive) are *not* links: their tag lacks the
    name-surrogate bit, so their contents are scanned normally. A reparse
    point whose tag is unknown is treated as a link, to stay on the safe side.
    """
    if stat.S_ISLNK(st.st_mode):
        return True
    if not getattr(st, 'st_file_attributes', 0) & REPARSE:
        return False
    tag = getattr(st, 'st_reparse_tag', 0)
    return not tag or bool(tag & NAME_SURROGATE)


def is_link(entry: os.DirEntry) -> bool:
    """True for symlinks, junctions and other name-surrogate reparse points."""
    try:
        if entry.is_symlink():
            return True
        return st_is_link(entry.stat(follow_symlinks=False))
    except OSError:
        return True


def path_is_link(path: str) -> bool:
    """Fails closed: anything we cannot inspect is treated as a link."""
    try:
        return st_is_link(os.lstat(long_path(path)))
    except FileNotFoundError:
        return False
    except OSError:
        return True


def file_size(path: str, st: os.stat_result | None = None) -> int:
    """Local disk bytes, with no extra system call for ordinary files.

    Sparse/compressed Windows files use their allocated size; if the filesystem
    cannot report it, fall back to logical size. Ordinary files use logical
    size (cluster slack and shared hard-link storage are not accounted for).
    Links and online-only placeholders contribute no local bytes. ``st`` must
    come from lstat/stat(follow_symlinks=False), never a followed link.
    """
    if st is None:
        st = os.lstat(long_path(path))
    attrs = getattr(st, 'st_file_attributes', 0)
    if st_is_link(st) or attrs & winapi.CLOUD_ATTRIBUTES or not stat.S_ISREG(st.st_mode):
        return 0
    if attrs & (winapi.FILE_ATTRIBUTE_SPARSE_FILE | winapi.FILE_ATTRIBUTE_COMPRESSED):
        allocated = winapi.file_allocation_size(long_path(path))
        if allocated is not None:
            return allocated
    return st.st_size


def age_time(st: os.stat_result) -> float:
    """When a file last changed *on this machine*.

    Files unpacked from archives or installers keep an old mtime, so on Windows
    the creation time (st_birthtime, or st_ctime before Python 3.12) counts too.
    """
    if not winapi.IS_WINDOWS:
        return st.st_mtime
    created = getattr(st, 'st_birthtime', None) or st.st_ctime
    return max(st.st_mtime, created)


def remove_dir(path: str) -> bool:
    """rmdir that also handles folders Windows marked read-only (customised folders)."""
    target = long_path(path)
    try:
        os.rmdir(target)
        return True
    except PermissionError:
        try:
            os.chmod(target, stat.S_IWRITE)
            os.rmdir(target)
            return True
        except OSError:
            return False
    except OSError:
        return False


def error_reason(exc: OSError) -> str:
    code = getattr(exc, 'winerror', None)
    if code in (32, 33) or exc.errno == 16:
        return '正在被其他程序使用'
    if code == 5 or isinstance(exc, PermissionError):
        return '没有权限（可能需要管理员）'
    if code == 145 or exc.errno == 39:
        return '目录不为空'
    if code in (2, 3) or isinstance(exc, FileNotFoundError):
        return '已不存在'
    return exc.strerror or str(exc)


@dataclass
class DeleteStats:
    files: int = 0
    dirs: int = 0
    bytes: int = 0
    failed: int = 0
    in_use: int = 0
    failures: list = field(default_factory=list)
    # recycle(): what left its place but never reached the Recycle Bin -- Windows deleted it for good (too large
    # for the bin, and the user said yes to Windows' prompt). Counted in files / bytes as well.
    permanent: list = field(default_factory=list)
    permanent_bytes: int = 0
    # Successfully removed selections: path -> (disk bytes, logical bytes, files).
    # Internal accounting for updating scans/plans; not part of the public result.
    measured: dict[str, tuple[int, int, int]] = field(default_factory=dict)
    # recycle(): the item being moved right now and how many are done, for a progress display
    current: str = ''
    done_items: int = 0
    total_items: int = 0

    def fail(self, path: str, exc_or_reason):
        reason = exc_or_reason if isinstance(exc_or_reason, str) else error_reason(exc_or_reason)
        self.failed += 1
        if reason == '正在被其他程序使用':
            self.in_use += 1
        if len(self.failures) < MAX_RECORDED_FAILURES:
            self.failures.append({'path': path, 'reason': reason})

    def merge(self, other: 'DeleteStats'):
        self.files += other.files
        self.dirs += other.dirs
        self.bytes += other.bytes
        self.failed += other.failed
        self.in_use += other.in_use
        room = MAX_RECORDED_FAILURES - len(self.failures)
        if room > 0:
            self.failures.extend(other.failures[:room])
        self.permanent.extend(other.permanent)
        self.permanent_bytes += other.permanent_bytes
        self.measured.update(other.measured)

    def as_dict(self) -> dict:
        return {'files': self.files, 'dirs': self.dirs, 'bytes': self.bytes, 'failed': self.failed,
                'in_use': self.in_use, 'failures': self.failures}


class Cancelled(Exception):
    pass


def check_cancel(cancel: threading.Event | None):
    if cancel is not None and cancel.is_set():
        raise Cancelled()


def remove_file(path: str, stats: DeleteStats, size: int = 0) -> bool:
    target = long_path(path)
    try:
        os.remove(target)
    except FileNotFoundError:
        return False
    except PermissionError as exc:
        # Read-only files cannot be removed on Windows until the flag is cleared.
        try:
            os.chmod(target, stat.S_IWRITE)
            os.remove(target)
        except OSError:
            stats.fail(path, exc)
            return False
    except OSError as exc:
        stats.fail(path, exc)
        return False
    stats.files += 1
    stats.bytes += size
    return True


def remove_link(path: str, stats: DeleteStats) -> bool:
    """Remove a junction/symlink itself, never its target."""
    target = long_path(path)
    try:
        if os.path.isdir(target) and not os.path.islink(target):
            os.rmdir(target)  # junction: rmdir removes the link only
        else:
            os.remove(target)
    except OSError as exc:
        try:
            os.rmdir(target)
        except OSError:
            stats.fail(path, exc)
            return False
    stats.dirs += 1
    return True


def walk_files(root: str, cancel=None, recursive=True, skip_dirs: frozenset = frozenset()):
    """Yield (dirpath, files, subdirs) top-down; files/subdirs are DirEntry lists.

    Links are reported in neither list, so callers never cross them.
    ``skip_dirs`` holds lower-case folder names that are never entered.
    """
    stack = [root]
    while stack:
        check_cancel(cancel)
        current = stack.pop()
        files, dirs = [], []
        try:
            with os.scandir(long_path(current)) as it:
                for entry in it:
                    try:
                        if is_link(entry):
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if entry.name.lower() not in skip_dirs:
                                dirs.append(entry)
                        else:
                            files.append(entry)
                    except OSError:
                        continue
        except OSError:
            continue
        yield current, files, dirs
        if recursive:
            stack.extend(os.path.join(current, d.name) for d in dirs)


def delete_matching(root: str, accept, *, min_age_seconds: float = 0, remove_empty_dirs: bool = True,
                    recursive: bool = True, skip_dirs: frozenset = frozenset(), cancel=None,
                    progress=None, stats: DeleteStats | None = None) -> DeleteStats:
    """Permanently delete files under ``root`` for which ``accept(name)`` is true.

    ``root`` itself is never removed. Empty sub-directories older than the age
    limit are removed afterwards, deepest first. Pass ``stats`` to accumulate
    into an existing object (keeps partial results if cancelled).
    """
    stats = stats if stats is not None else DeleteStats()
    now = time.time()
    visited_dirs = []
    for dirpath, files, dirs in walk_files(root, cancel, recursive=recursive, skip_dirs=skip_dirs):
        if dirpath != root:
            visited_dirs.append(dirpath)
        for entry in files:
            if not accept(entry.name):
                continue
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if min_age_seconds and now - age_time(st) < min_age_seconds:
                continue
            path = os.path.join(dirpath, entry.name)
            remove_file(path, stats, file_size(path, st))
            if progress and stats.files % 200 == 0:
                progress(stats)
    if remove_empty_dirs:
        for dirpath in sorted(visited_dirs, key=len, reverse=True):
            check_cancel(cancel)
            try:
                if min_age_seconds and now - age_time(os.stat(long_path(dirpath))) < min_age_seconds:
                    continue
            except OSError:
                continue
            if remove_dir(dirpath):  # fails harmlessly when not empty or in use
                stats.dirs += 1
    return stats


def delete_path(path: str, *, cancel=None, progress=None, stats: DeleteStats | None = None) -> DeleteStats:
    """Permanently delete one file or directory tree (links removed as links)."""
    stats = stats if stats is not None else DeleteStats()
    if path_is_link(path):
        remove_link(path, stats)
        return stats
    if not os.path.isdir(long_path(path)):
        try:
            st = os.lstat(long_path(path))
            size = file_size(path, st)
        except OSError as exc:
            stats.fail(path, exc)
            return stats
        if remove_file(path, stats, size):
            stats.measured[path] = (size, st.st_size, 1)
        return stats
    dirs = []
    initial_bytes, initial_files = stats.bytes, stats.files
    logical = 0
    for dirpath, files, subdirs in walk_files(path, cancel):
        dirs.append(dirpath)
        for entry in files:
            try:
                st = entry.stat(follow_symlinks=False)
                size = file_size(os.path.join(dirpath, entry.name), st)
                file_logical = st.st_size
            except OSError:
                size = file_logical = 0
            if remove_file(os.path.join(dirpath, entry.name), stats, size):
                logical += file_logical
            if progress and stats.files % 200 == 0:
                progress(stats)
        # Links inside the tree are removed as links so the parent can go.
        try:
            with os.scandir(long_path(dirpath)) as it:
                for entry in it:
                    if is_link(entry):
                        remove_link(os.path.join(dirpath, entry.name), stats)
        except OSError:
            pass
    for dirpath in sorted(dirs, key=len, reverse=True):
        if remove_dir(dirpath):
            stats.dirs += 1
        elif dirpath == path and os.path.lexists(long_path(path)):
            stats.fail(path, '部分内容无法删除，目录已保留')
    if not os.path.lexists(long_path(path)):
        stats.measured[path] = (stats.bytes - initial_bytes, logical, stats.files - initial_files)
    return stats


def path_sizes(path: str, cancel=None) -> tuple[int, int, int]:
    """(local disk bytes, logical bytes, file count), without following links."""
    check_cancel(cancel)
    if path_is_link(path):
        return 0, 0, 0
    if not os.path.isdir(long_path(path)):
        try:
            st = os.lstat(long_path(path))
            return file_size(path, st), st.st_size, 1
        except OSError:
            return 0, 0, 0
    total = logical = count = 0
    for dirpath, files, _ in walk_files(path, cancel):
        for entry in files:
            try:
                st = entry.stat(follow_symlinks=False)
                total += file_size(os.path.join(dirpath, entry.name), st)
                logical += st.st_size
                count += 1
            except OSError:
                pass
    return total, logical, count


def path_size(path: str, cancel=None) -> tuple[int, int]:
    """(local disk bytes, file count), without following links."""
    size, _, count = path_sizes(path, cancel)
    return size, count


def _arrived_in_bin(gone: list[str], since: float) -> set[str] | None:
    """Of the paths that left their place, the ones the Recycle Bin now holds (normcased), or None when a bin
    could not be read -- then they are taken to be there, as before."""
    if not gone:
        return set()
    out: set[str] = set()
    for root in {os.path.splitdrive(os.path.abspath(p))[0] + os.sep for p in gone}:
        found = winapi.recycled_since(root, since)
        if found is None:
            return None
        out |= {os.path.normcase(os.path.abspath(p)) for p in found}
    return out


def recycle(paths: list[str], *, batch: int = 64, cancel=None, progress=None, hwnd=None) -> tuple[DeleteStats, list[str]]:
    """Move paths to the Recycle Bin. Returns (stats, list of paths gone from their place).

    Paths must already be validated by the caller's ProtectionPolicy. ``hwnd``
    parents Windows' own "cannot recycle, delete permanently?" prompt; what the
    user let Windows delete that way is in ``stats.permanent`` (and still among
    the returned paths: it is gone either way).
    """
    stats = DeleteStats()
    done: list[str] = []
    pending = []
    for path in paths:
        check_cancel(cancel)
        if not os.path.lexists(long_path(path)):
            stats.fail(path, '已不存在')
            continue
        size, logical, count = path_sizes(path, cancel)
        pending.append((path, size, logical, count))
    stats.total_items = len(pending)
    for start in range(0, len(pending), batch):
        if cancel is not None and cancel.is_set():
            for path, _, _, _ in pending[start:]:
                stats.fail(path, '已取消')
            break
        chunk = pending[start:start + batch]
        long_ones = [p for p in chunk if len(p[0]) >= 260]
        for path, _, _, _ in long_ones:
            stats.fail(path, '路径过长，回收站无法处理')
        chunk = [p for p in chunk if len(p[0]) < 260]
        if not chunk:
            continue
        stats.current = chunk[0][0]
        if progress:
            progress(stats)  # what is being moved now: one large folder can take minutes
        started = time.time()
        try:
            rc, aborted = winapi.recycle_paths([p[0] for p in chunk], hwnd)
        except OSError as exc:
            for path, _, _, _ in chunk:
                stats.fail(path, exc)
            continue
        gone = [path for path, _, _, _ in chunk if not os.path.lexists(path)]
        binned = _arrived_in_bin(gone, started)
        for path, size, logical, count in chunk:
            if os.path.lexists(path):
                stats.fail(path, '已取消' if aborted else f'移到回收站失败（代码 {rc}）')
                continue
            stats.files += count
            stats.bytes += size
            stats.measured[path] = (size, logical, count)
            done.append(path)
            if binned is not None and os.path.normcase(os.path.abspath(path)) not in binned:
                stats.permanent.append(path)
                stats.permanent_bytes += size
        stats.done_items += len(chunk)
        if progress:
            progress(stats)
        if aborted:
            for path, _, _, _ in pending[start + batch:]:
                stats.fail(path, '已取消')
            break
    return stats, done
