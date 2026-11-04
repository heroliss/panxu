"""Duplicate-file finder: size → partial hash → full hash.

Cloud-only placeholders are skipped (hashing them would download them), and
hard links to the same physical file are not reported as duplicates. Programs'
folders and code projects are not searched: the same DLL in two portable
programs, or an asset copied into a build folder, is needed in both places.
"""
from __future__ import annotations

import hashlib
import os
import stat
import time

from . import fsops, winapi
from .insights import is_program_dir, is_project_dir

CHUNK = 1024 * 1024
EDGE = 64 * 1024


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
            if dirpath != root and (is_program_dir(files, dirs) or is_project_dir(files, dirs)):
                dirs.clear()
                continue
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
                'files': [{'path': p, 'mtime': m} for p, m in group],
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
