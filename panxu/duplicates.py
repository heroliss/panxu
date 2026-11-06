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


AI_RANK = {'keep': 0, 'careful': 1, None: 1, 'delete': 2}


def must_stay(f: dict, ai: dict | None = None) -> bool:
    """A copy a group keeps whatever else goes: a program's own, one its project's files mention, or one the
    model says to keep."""
    return f.get('place') == 'program' or bool((f.get('refs') or {}).get('count')) or (ai or {}).get('verdict') == 'keep'


def removal_score(f: dict, ai: dict | None = None) -> tuple[int, str | None]:
    """建议清理度 (0-100) for recycling this copy while another one stays, and what holds it back (None: a
    loose copy, nothing does). The model's advice (``ai``, see Analyst.review_duplicates) lowers it freely but
    raises it only a little and never past what 盘序 itself found (a program's copy, a mentioned one)."""
    place, refs = f.get('place'), f.get('refs') or {}
    name = os.path.basename(f['path'])
    if place == 'program':
        score, why = 20, f'「{name}」在{place_label(f)}里：程序可能加载它，删掉后可能不能运行'
    elif refs.get('count'):
        more = f' 等 {refs["count"]} 个文件' if refs['count'] > 1 else ''
        score, why = 20, f'「{name}」被「{refs["found"][0]}」{more}提到：删掉这份，用到它的地方很可能出问题'
    elif place in ('project', 'near') and refs.get('complete'):
        score = 60 if place == 'project' else 70
        why = (f'「{name}」在{place_label(f)}{"里" if place == "project" else ""}，但那里的代码和设置都没有提到这个文件名'
               f'（搜索了 {refs["searched"]} 个文件），很可能没在用；运行时拼出来的文件名搜不到，请你确认')
    elif place == 'project':
        score, why = 30, f'「{name}」在{place_label(f)}里：工程可能引用它' + ('（文件太多，没有搜完）' if refs else '')
    elif place == 'near':
        score, why = 50, f'「{name}」在{place_label(f)}：可能是工程的素材来源，例如脚本从那里读取'
    elif place == 'build':
        score, why = 50, f'「{name}」在{place_label(f)}里：重新构建时会再生成'
    else:
        score, why = 85, None
    verdict = (ai or {}).get('verdict')
    if verdict == 'keep':
        score = min(score, 20)
    elif verdict == 'careful':
        score = min(score, 40)
    elif verdict == 'delete' and score > 20:
        score = max(score, min(score + 10, 70))
    return score, why


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
                block = fh.read(size + 1)
                if len(block) != size:
                    return None
                h.update(block)  # small file: this covers all of it
            else:
                h.update(fh.read(EDGE))
                fh.seek(size - EDGE)
                h.update(fh.read(EDGE))
            return h.digest()
    except OSError:
        return None


def _hash_full(path: str, cancel=None, progress=None, expected_size=None, deadline=None) -> bytes | None:
    try:
        with open(fsops.long_path(path), 'rb') as fh:
            h = hashlib.blake2b(digest_size=20)
            read = 0
            while True:
                fsops.check_cancel(cancel)
                if deadline is not None and time.monotonic() >= deadline:
                    return None
                block = fh.read(CHUNK if expected_size is None else min(CHUNK, expected_size - read + 1))
                if not block:
                    break
                read += len(block)
                if expected_size is not None and read > expected_size:
                    return None
                h.update(block)
                if progress:
                    progress(len(block))
            return h.digest() if expected_size is None or read == expected_size else None
    except OSError:
        return None


# -- does the project use it? -------------------------------------------------------------------------------
# A project names the files it loads in its code and settings (url('../lib/fonts/NotoSerifJP-600.woff'),
# staticFile('mix_master.wav'), a Unity .meta, a Godot .tscn). Searching those for a copy's file name is read-only
# and local, and tells far better than any guess whether deleting the copy breaks the project. A name built at run
# time cannot be found, so 「not mentioned」 lowers the risk but does not prove the file unused.
REF_EXTS = {
    '.js', '.jsx', '.ts', '.tsx', '.mjs', '.cjs', '.json', '.css', '.scss', '.sass', '.less', '.html', '.htm', '.vue',
    '.svelte', '.astro', '.md', '.mdx', '.txt', '.py', '.ipynb', '.rb', '.php', '.java', '.kt', '.kts', '.gradle',
    '.cs', '.csproj', '.vb', '.c', '.cc', '.cpp', '.h', '.hpp', '.go', '.rs', '.swift', '.dart', '.lua', '.gd',
    '.tscn', '.tres', '.godot', '.unity', '.prefab', '.asset', '.mat', '.meta', '.xml', '.yaml', '.yml', '.toml',
    '.ini', '.cfg', '.conf', '.properties', '.plist', '.sh', '.bat', '.cmd', '.ps1', '.sql', '.csv', '.tex', '.svg',
    '.ass', '.srt', '.vtt', '.cmake', '.mk'}
REF_SKIP_DIRS = {'node_modules', '.git', '.svn', '.hg', '__pycache__', '.venv', 'venv', '.cache'} | BUILD_DIRS
REF_MAX_FILE = 2 * 1024 * 1024
REF_MAX_FILES = 20000
REF_MAX_BYTES = 200 * 1024 * 1024


def _search_names(root: str, names: set[str], cancel=None, skip=None, max_bytes=REF_MAX_BYTES, deadline=None) -> dict:
    """Which code and settings files below ``root`` mention each of ``names`` (lower-case file names)."""
    found: dict[str, list[str]] = {}
    searched = total = 0
    complete = True

    def unreadable(_path, _exc):
        nonlocal complete
        complete = False

    def excluded(path):
        nonlocal complete
        if skip and skip(path):
            complete = False
            return True
        return False

    for dirpath, files, dirs in fsops.walk_files(root, cancel, skip=excluded, on_error=unreadable):
        if deadline is not None and time.monotonic() >= deadline:
            return {'found': found, 'searched': searched, 'bytes': total, 'complete': False}
        dirs[:] = [d for d in dirs if d.name.lower() not in REF_SKIP_DIRS]
        for entry in files:
            if deadline is not None and time.monotonic() >= deadline:
                return {'found': found, 'searched': searched, 'bytes': total, 'complete': False}
            if os.path.splitext(entry.name)[1].lower() not in REF_EXTS:
                continue
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError:
                complete = False
                continue
            if not stat.S_ISREG(st.st_mode) or st.st_size > REF_MAX_FILE \
                    or getattr(st, 'st_file_attributes', 0) & winapi.CLOUD_ATTRIBUTES:
                complete = False
                continue
            if searched >= REF_MAX_FILES or total + st.st_size > max_bytes:
                return {'found': found, 'searched': searched, 'bytes': total, 'complete': False}
            path = os.path.join(dirpath, entry.name)
            try:
                with open(fsops.long_path(path), 'rb') as fh:
                    raw = fh.read(st.st_size + 1)
                    if len(raw) != st.st_size:
                        complete = False
                        continue
                    text = raw.decode('utf-8', 'ignore').lower()
            except OSError:
                complete = False
                continue
            searched += 1
            total += st.st_size
            for name in names:
                if name in text:
                    found.setdefault(name, []).append(path)
    return {'found': found, 'searched': searched, 'bytes': total, 'complete': complete}


def check_references(groups: list[dict], cancel=None, progress=None, skip=None, max_bytes=None, max_seconds=None) -> dict:
    """Add ``refs`` to every copy in a code project or next to one: {'found': [files mentioning its name, relative
    to the searched folder], 'count', 'searched', 'complete'}. Each folder is read once for all its names."""
    wanted: dict[str, tuple[str, set[str]]] = {}
    for g in groups:
        for f in g['files']:
            if f.get('place') in ('project', 'near'):
                root = f['place_root']
                wanted.setdefault(paths.key(root), (root, set()))[1].add(os.path.basename(f['path']).lower())
    results = {}
    total = 0
    deadline = time.monotonic() + max_seconds if max_seconds is not None else None
    for i, (k, (root, names)) in enumerate(wanted.items()):
        if progress:
            progress({'phase': 'refs', 'done': i, 'total': len(wanted), 'current': root})
        budget = REF_MAX_BYTES if max_bytes is None else min(REF_MAX_BYTES, max(0, max_bytes - total))
        if deadline is not None and time.monotonic() >= deadline:
            budget = 0
        results[k] = _search_names(root, names, cancel, skip=skip, max_bytes=budget, deadline=deadline) if budget else {
            'found': {}, 'searched': 0, 'bytes': 0, 'complete': False}
        total += results[k]['bytes']
    for g in groups:
        for f in g['files']:
            r = results.get(paths.key(f.get('place_root') or '')) if f.get('place') in ('project', 'near') else None
            if r is None:
                continue
            mine = paths.key(f['path'])
            found = [p for p in r['found'].get(os.path.basename(f['path']).lower(), []) if paths.key(p) != mine]
            f['refs'] = {'found': [os.path.relpath(p, f['place_root']) for p in found[:5]], 'count': len(found),
                         'searched': r['searched'], 'complete': r['complete']}
    return {'bytes': total, 'complete': all(r['complete'] for r in results.values())}


def find_duplicates(roots: list[str], *, min_size: int = 1024 * 1024, skip=None, cancel=None, progress=None,
                    container_roots=(), max_files: int | None = None, max_hash_bytes: int | None = None,
                    max_reference_bytes: int | None = None, max_hash_seconds=None, max_reference_seconds=None) -> dict:
    """Return {'groups': [...], 'stats': {...}}.

    ``skip(path) -> bool`` excludes directories (system folders, user exclusions).
    """
    started = time.time()
    by_size: dict[int, list] = {}
    seen_dirs = set()
    homes: dict[str, tuple[str, str]] = {}  # programs' folders and code projects met on the way
    files_seen = 0
    truncated = False
    deferred = hashed_bytes = 0
    containers = {paths.key(r) for r in container_roots if r}
    listing_done = False

    def report(phase, **extra):
        if progress:
            progress({'phase': phase, 'files': files_seen, 'elapsed': time.time() - started, **extra})

    for root in roots:
        if listing_done:
            break
        if fsops.path_is_link(root) or (skip and skip(root)):
            continue
        for dirpath, files, dirs in fsops.walk_files(root, cancel):
            dkey = os.path.normcase(os.path.abspath(dirpath))
            if dkey in seen_dirs:
                dirs.clear()
                continue
            seen_dirs.add(dkey)
            if skip and skip(dirpath):
                dirs.clear()  # walk_files extends the stack after yield, so this prunes
                continue
            if paths.key(dirpath) not in containers:
                kind = 'program' if is_program_dir(files, dirs) else 'project' if is_project_dir(files, dirs) else None
                if kind:
                    homes[paths.key(dirpath)] = (kind, dirpath)
            for entry in files:
                if skip and skip(entry.path):
                    continue
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if not stat.S_ISREG(st.st_mode) or st.st_size < min_size:
                    continue
                if getattr(st, 'st_file_attributes', 0) & winapi.CLOUD_ATTRIBUTES:
                    continue
                if max_files is not None and files_seen >= max_files:
                    truncated = listing_done = True
                    break
                files_seen += 1
                by_size.setdefault(st.st_size, []).append([os.path.join(dirpath, entry.name), st.st_mtime,
                                                         fsops.file_size(entry.path, st)])
            if files_seen % 500 == 0:
                report('listing')
            if listing_done:
                break

    candidates = {s: v for s, v in by_size.items() if len(v) > 1}
    # drop hard links to the same physical file
    for size, items in list(candidates.items()):
        unique, ids = [], set()
        for item in items:
            fsops.check_cancel(cancel)
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
    deadline = time.monotonic() + max_hash_seconds if max_hash_seconds is not None else None
    timed_out = False
    done = 0
    def partial_groups():
        # Verify each size batch fully before visiting the next. A slow drive
        # must not spend its entire budget on quick hashes and return nothing.
        nonlocal done, deferred, hashed_bytes, timed_out
        for size, items in sorted(candidates.items(), reverse=True):
            groups: dict[bytes, list] = {}
            for item in items:
                fsops.check_cancel(cancel)
                if deadline is not None and time.monotonic() >= deadline:
                    timed_out = True
                    break
                cost = min(size, 2 * EDGE)
                if max_hash_bytes is not None and hashed_bytes + cost > max_hash_bytes:
                    deferred += 1
                    continue
                if not verify_unchanged(item[0], size, item[1]):
                    continue
                digest = _hash_partial(item[0], size)
                hashed_bytes += cost
                done += 1
                if digest is not None and verify_unchanged(item[0], size, item[1]):
                    groups.setdefault(digest, []).append(item)
                if done % 200 == 0:
                    report('quick', done=done, total=total_candidates)
            for group in groups.values():
                if len(group) > 1:
                    yield size, group
            if timed_out:
                break

    # folders that hold a code project (never a folder the user chose to search, nor a drive): what else lies in
    # them is often the project's material
    chosen = {paths.key(r) for r in roots}
    near = {}
    for kind, home in homes.values():
        parent = os.path.dirname(home)
        if kind == 'project' and paths.key(parent) not in chosen and os.path.dirname(parent) != parent:
            near[paths.key(parent)] = parent

    bytes_total = sum(size * len(g) for size, g in candidates.items() if size > 2 * EDGE)
    if max_hash_bytes is not None:
        bytes_total = min(bytes_total, max_hash_bytes)
    hashed = [0]

    def on_bytes(n):
        nonlocal hashed_bytes
        hashed_bytes += n
        hashed[0] += n
        if hashed[0] % (64 * CHUNK) < n:
            report('full', bytes_done=hashed[0], bytes_total=bytes_total)

    groups_out = []
    for size, items in partial_groups():
        if deadline is not None and time.monotonic() >= deadline:
            timed_out = True
            break
        cost = size * len(items) if size > 2 * EDGE else 0
        if max_hash_bytes is not None and hashed_bytes + cost > max_hash_bytes:
            deferred += len(items)
            continue
        if size <= 2 * EDGE:
            # the partial hash already covered the whole file
            full_groups = {b'': items}
        else:
            full_groups = {}
            for item in items:
                if not verify_unchanged(item[0], size, item[1]):
                    continue
                digest = _hash_full(item[0], cancel, on_bytes, expected_size=size, deadline=deadline)
                if digest is not None and verify_unchanged(item[0], size, item[1]):
                    full_groups.setdefault(digest, []).append(item)
        for digest, group in full_groups.items():
            if len(group) < 2:
                continue
            group.sort(key=lambda it: it[1])
            groups_out.append({
                'id': len(groups_out),
                'size': size,
                'wasted': sum(used for _, _, used in group[1:]),
                'files': [{'path': p, 'mtime': m, 'size': used, 'logical_size': size,
                           **(place_of(p, homes, near) or {})} for p, m, used in group],
            })
    groups_out.sort(key=lambda g: g['wasted'], reverse=True)
    for index, g in enumerate(groups_out):
        g['id'] = index
    if deadline is not None and time.monotonic() >= deadline:
        timed_out = True
    refs = check_references(groups_out, cancel, lambda p: report(**p), skip=skip,
                            max_bytes=max_reference_bytes, max_seconds=max_reference_seconds)
    return {'groups': groups_out, 'stats': {
        'files': files_seen, 'groups': len(groups_out), 'wasted': sum(g['wasted'] for g in groups_out),
        'roots': list(roots), 'truncated': truncated or deferred > 0 or timed_out, 'file_limit': max_files,
        'timed_out': timed_out, 'hash_seconds_limit': max_hash_seconds,
        'hash_limit': max_hash_bytes, 'hashed_bytes': hashed_bytes, 'deferred_files': deferred,
        'reference_bytes': refs['bytes'], 'references_complete': refs['complete'],
        'seconds': round(time.time() - started, 1)}}


def verify_unchanged(path: str, size: int, mtime: float) -> bool:
    try:
        st = os.lstat(fsops.long_path(path))
    except OSError:
        return False
    return (not fsops.st_is_link(st) and not getattr(st, 'st_file_attributes', 0) & winapi.CLOUD_ATTRIBUTES
            and st.st_size == size and st.st_mtime == mtime)
