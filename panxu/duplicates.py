"""Duplicate-file finder: size → partial hash → full hash.

Cloud-only placeholders are skipped (hashing them would download them), and
hard links to the same physical file are not reported as duplicates. A copy
inside a program's folder or a code project is marked with where it is
(``place``): the same DLL in two portable programs, or a font two projects each
load from their own folder, may be needed in both places.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
import time

from . import fsops, paths, winapi
from .insights import is_program_dir, is_project_dir

CHUNK = 1024 * 1024
EDGE = 64 * 1024
# a project's build output: the build writes it again
BUILD_DIRS = {'out', 'dist', 'build', 'target', 'bin', 'obj', 'release', 'debug', '.next', '.nuxt', 'coverage'}
# which copy of a group to keep first: a program's file, then a project's source, then a build output, then a
# copy next to a project, then a loose copy
PLACE_RANK = {'program': 0, 'project': 1, 'build': 2, 'near': 3, None: 4}
IN_USE = ('program', 'project', 'build')  # inside a program's folder or a project: the copy it may load


def place_label(f: dict) -> str:
    """'代码工程「萍萍瑜伽故事\\代码工程」' for a copy with a place: two levels, so that two projects whose own
    folder has the same name can be told apart."""
    parts = [p for p in re.split(r'[\\/]', f.get('place_root') or '') if p and not p.endswith(':')]
    name = '\\'.join(parts[-2:])
    if f.get('place') == 'build':
        return f'工程「{name}」的构建输出'
    if f.get('place') == 'near':
        return f'「{name}」里的代码工程旁边'
    return {'program': '程序文件夹', 'project': '代码工程'}.get(f.get('place'), '') + f'「{name}」'


def place_of(path: str, homes: dict, near: dict | None = None) -> dict | None:
    """{'place': 'program' | 'project' | 'build', 'place_root': folder} for a file inside a program's folder or
    a code project (the nearest one); 'near' for one elsewhere in the folder that holds a project
    (萍萍瑜伽故事\\audio next to 萍萍瑜伽故事\\代码工程: often the project's source material); None for a loose
    file. ``homes``: folder key -> (kind, folder); ``near``: key of a folder holding a project -> folder."""
    d, below, beside = os.path.dirname(path), [], None
    while True:
        k = paths.key(d)
        found = homes.get(k)
        if found:
            kind, root = found
            if kind == 'project' and any(p.lower() in BUILD_DIRS for p in below):
                kind = 'build'
            return {'place': kind, 'place_root': root}
        if beside is None and near and k in near:
            beside = near[k]
        up = os.path.dirname(d)
        if up == d:
            return {'place': 'near', 'place_root': beside} if beside else None
        below.append(os.path.basename(d))
        d = up


def _hash_partial(path: str, size: int) -> bytes | None:
    try:
        with open(fsops.long_path(path), 'rb') as fh:
            h = hashlib.blake2b(digest_size=16)
            if size <= 2 * EDGE:
                h.update(fh.read())  # small file: this covers all of it
            else:
                h.update(fh.read(EDGE))
                fh.seek(size - EDGE)
                h.update(fh.read(EDGE))
            return h.digest()
    except OSError:
        return None


def _hash_full(path: str, cancel=None, progress=None) -> bytes | None:
    try:
        with open(fsops.long_path(path), 'rb') as fh:
            h = hashlib.blake2b(digest_size=20)
            while True:
                fsops.check_cancel(cancel)
                block = fh.read(CHUNK)
                if not block:
                    break
                h.update(block)
                if progress:
                    progress(len(block))
            return h.digest()
    except OSError:
        return None


def find_duplicates(roots: list[str], *, min_size: int = 1024 * 1024, skip=None, cancel=None, progress=None) -> dict:
    """Return {'groups': [...], 'stats': {...}}.

    ``skip(path) -> bool`` excludes directories (system folders, user exclusions).
    """
    started = time.time()
    by_size: dict[int, list] = {}
    seen_dirs = set()
    homes: dict[str, tuple[str, str]] = {}  # programs' folders and code projects met on the way
    files_seen = 0

    def report(phase, **extra):
        if progress:
            progress({'phase': phase, 'files': files_seen, 'elapsed': time.time() - started, **extra})

    for root in roots:
        for dirpath, files, dirs in fsops.walk_files(root, cancel):
            dkey = os.path.normcase(os.path.abspath(dirpath))
            if dkey in seen_dirs:
                dirs.clear()
                continue
            seen_dirs.add(dkey)
            if skip and skip(dirpath):
                dirs.clear()  # walk_files extends the stack after yield, so this prunes
                continue
            if dirpath != root:
                kind = 'program' if is_program_dir(files, dirs) else 'project' if is_project_dir(files, dirs) else None
                if kind:
                    homes[paths.key(dirpath)] = (kind, dirpath)
            for entry in files:
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if not stat.S_ISREG(st.st_mode) or st.st_size < min_size:
                    continue
                if getattr(st, 'st_file_attributes', 0) & winapi.CLOUD_ATTRIBUTES:
                    continue
                files_seen += 1
                by_size.setdefault(st.st_size, []).append([os.path.join(dirpath, entry.name), st.st_mtime])
            if files_seen % 500 == 0:
                report('listing')

    candidates = {s: v for s, v in by_size.items() if len(v) > 1}
    # drop hard links to the same physical file
    for size, items in list(candidates.items()):
        unique, ids = [], set()
        for item in items:
            try:
                st = os.stat(fsops.long_path(item[0]))
                ident = (st.st_dev, st.st_ino) if st.st_ino else None
            except OSError:
                continue
            if ident and ident in ids:
                continue
            if ident:
                ids.add(ident)
            unique.append(item)
        if len(unique) > 1:
            candidates[size] = unique
        else:
            del candidates[size]

    total_candidates = sum(len(v) for v in candidates.values())
    done = 0
    partial_groups = []
    for size, items in candidates.items():
        groups: dict[bytes, list] = {}
        for item in items:
            fsops.check_cancel(cancel)
            digest = _hash_partial(item[0], size)
            done += 1
            if digest is not None:
                groups.setdefault(digest, []).append(item)
            if done % 200 == 0:
                report('quick', done=done, total=total_candidates)
        partial_groups.extend((size, g) for g in groups.values() if len(g) > 1)

    # folders that hold a code project (never a folder the user chose to search, nor a drive): what else lies in
    # them is often the project's material
    chosen = {paths.key(r) for r in roots}
    near = {}
    for kind, home in homes.values():
        parent = os.path.dirname(home)
        if kind == 'project' and paths.key(parent) not in chosen and os.path.dirname(parent) != parent:
            near[paths.key(parent)] = parent

    bytes_total = sum(size * len(g) for size, g in partial_groups if size > 2 * EDGE)
    hashed = [0]

    def on_bytes(n):
        hashed[0] += n
        if hashed[0] % (64 * CHUNK) < n:
            report('full', bytes_done=hashed[0], bytes_total=bytes_total)

    groups_out = []
    for size, items in partial_groups:
        if size <= 2 * EDGE:
            # the partial hash already covered the whole file
            full_groups = {b'': items}
        else:
            full_groups = {}
            for item in items:
                digest = _hash_full(item[0], cancel, on_bytes)
                if digest is not None:
                    full_groups.setdefault(digest, []).append(item)
        for digest, group in full_groups.items():
            if len(group) < 2:
                continue
            group.sort(key=lambda it: it[1])
            groups_out.append({
                'id': len(groups_out),
                'size': size,
                'wasted': size * (len(group) - 1),
                'files': [{'path': p, 'mtime': m, **(place_of(p, homes, near) or {})} for p, m in group],
            })
    groups_out.sort(key=lambda g: g['wasted'], reverse=True)
    for index, g in enumerate(groups_out):
        g['id'] = index
    return {'groups': groups_out, 'stats': {
        'files': files_seen, 'groups': len(groups_out), 'wasted': sum(g['wasted'] for g in groups_out),
        'seconds': round(time.time() - started, 1)}}


def verify_unchanged(path: str, size: int, mtime: float) -> bool:
    try:
        st = os.stat(fsops.long_path(path))
    except OSError:
        return False
    return st.st_size == size and abs(st.st_mtime - mtime) < 2
