"""Space analysis: a fast, multi-threaded directory-tree scan.

Only directories are kept as nodes (with rolled-up sizes); individual files
are not stored except for the largest few thousand, which feed the large-file
view. A directory's own files are re-listed on demand when the user opens it.
This keeps memory to tens of MB even for drives with millions of files.
"""
from __future__ import annotations

import heapq
import os
import queue
import stat
import threading
import time

from . import fsops, paths, winapi

TOP_FILES = 5000
FLAG_DENIED = 1     # could not list this directory
FLAG_LINKS = 2      # contains junctions/symlinks that were not followed
FLAG_CLOUD = 4      # contains cloud-only (online) files


class Node:
    __slots__ = ('name', 'parent', 'children', 'size', 'files', 'dirs', 'mtime', 'flags', 'id')

    def __init__(self, name: str, parent: 'Node | None'):
        self.name = name
        self.parent = parent
        self.children = None
        self.size = 0
        self.files = 0
        self.dirs = 0
        self.mtime = 0.0
        self.flags = 0
        self.id = -1


class _WorkerState:
    __slots__ = ('dirs', 'files', 'bytes', 'top', 'exts', 'cloud_bytes', 'cloud_files', 'denied')

    def __init__(self):
        self.dirs = self.files = self.bytes = self.cloud_bytes = self.cloud_files = self.denied = 0
        self.top: list = []          # min-heap of (size, path, mtime)
        self.exts: dict = {}         # ext -> [count, bytes]


class SpaceScan:
    def __init__(self, root: str, threads: int = 6):
        self.root_path = os.path.abspath(root)
        self.threads = max(1, threads)
        self.root = Node(self.root_path, None)
        self.nodes: list[Node] = [self.root]
        self.top_files: list[dict] = []
        self.exts: dict = {}
        self.stats: dict = {}
        self.finished_at = 0.0
        self.complete = False

    # -- scanning --------------------------------------------------------------
    def run(self, cancel: threading.Event | None = None, progress=None) -> 'SpaceScan':
        started = time.time()
        work: queue.Queue = queue.Queue()
        work.put((self.root, self.root_path))
        states = [_WorkerState() for _ in range(self.threads)]

        def worker(state: _WorkerState):
            while True:
                item = work.get()
                if item is None:
                    work.task_done()
                    return
                try:
                    if not (cancel is not None and cancel.is_set()):
                        self._scan_dir(item[0], item[1], work, state)
                except Exception:  # noqa: BLE001 - one bad directory must not stop the scan
                    item[0].flags |= FLAG_DENIED
                finally:
                    work.task_done()

        threads = [threading.Thread(target=worker, args=(s,), daemon=True, name=f'scan-{i}')
                   for i, s in enumerate(states)]
        for t in threads:
            t.start()
        while work.unfinished_tasks:
            time.sleep(0.15)
            if progress:
                progress({'dirs': sum(s.dirs for s in states), 'files': sum(s.files for s in states),
                          'bytes': sum(s.bytes for s in states), 'elapsed': time.time() - started})
        for _ in threads:
            work.put(None)
        for t in threads:
            t.join()
        fsops.check_cancel(cancel)
        self._finish(states, started)
        return self

    def _scan_dir(self, node: Node, path: str, work: queue.Queue, state: _WorkerState):
        state.dirs += 1
        try:
            it = os.scandir(fsops.long_path(path))
        except OSError:
            node.flags |= FLAG_DENIED
            state.denied += 1
            return
        children = []
        size = files = 0
        newest = 0.0
        with it:
            for entry in it:
                try:
                    st = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if fsops.st_is_link(st):
                    node.flags |= FLAG_LINKS
                    continue
                if stat.S_ISDIR(st.st_mode):
                    child = Node(entry.name, node)
                    children.append(child)
                    self.nodes.append(child)  # list.append is atomic; child index > parent index
                    work.put((child, os.path.join(path, entry.name)))
                    continue
                attrs = getattr(st, 'st_file_attributes', 0)
                fsize = st.st_size
                files += 1
                if attrs & winapi.CLOUD_ATTRIBUTES:
                    node.flags |= FLAG_CLOUD
                    state.cloud_bytes += fsize
                    state.cloud_files += 1
                    continue  # online-only: occupies no local space
                size += fsize
                if st.st_mtime > newest:
                    newest = st.st_mtime
                ext = os.path.splitext(entry.name)[1].lower()
                slot = state.exts.get(ext)
                if slot is None:
                    state.exts[ext] = [1, fsize]
                else:
                    slot[0] += 1
                    slot[1] += fsize
                if len(state.top) < TOP_FILES:
                    heapq.heappush(state.top, (fsize, os.path.join(path, entry.name), st.st_mtime))
                elif fsize > state.top[0][0]:
                    heapq.heapreplace(state.top, (fsize, os.path.join(path, entry.name), st.st_mtime))
        node.children = children or None
        node.size = size
        node.files = files
        node.mtime = newest
        state.files += files
        state.bytes += size

    def _finish(self, states, started):
        nodes = self.nodes
        for index, node in enumerate(nodes):
            node.id = index
        for node in reversed(nodes):  # children always come after their parent
            parent = node.parent
            if parent is not None:
                parent.size += node.size
                parent.files += node.files
                parent.dirs += 1 + node.dirs
                if node.mtime > parent.mtime:
                    parent.mtime = node.mtime
                if node.flags & (FLAG_DENIED | FLAG_CLOUD):
                    parent.flags |= node.flags & (FLAG_DENIED | FLAG_CLOUD)
        for node in nodes:
            if node.children:
                node.children.sort(key=lambda n: n.size, reverse=True)
                node.children = tuple(node.children)
        merged = []
        exts: dict = {}
        for s in states:
            merged.extend(s.top)
            for ext, (count, size) in s.exts.items():
                slot = exts.setdefault(ext, [0, 0])
                slot[0] += count
                slot[1] += size
        merged = heapq.nlargest(TOP_FILES, merged)
        self.top_files = [{'path': p, 'size': size, 'mtime': mtime} for size, p, mtime in merged]
        self.exts = exts
        self.stats = {
            'dirs': len(nodes), 'files': self.root.files, 'bytes': self.root.size,
            'cloud_files': sum(s.cloud_files for s in states), 'cloud_bytes': sum(s.cloud_bytes for s in states),
            'denied': sum(s.denied for s in states), 'seconds': round(time.time() - started, 1),
        }
        self.finished_at = time.time()
        self.complete = True

    # -- queries -----------------------------------------------------------------
    def node(self, node_id: int) -> Node:
        if not isinstance(node_id, int) or not 0 <= node_id < len(self.nodes):
            raise KeyError(node_id)
        return self.nodes[node_id]

    def path_of(self, node: Node) -> str:
        parts = []
        while node.parent is not None:
            parts.append(node.name)
            node = node.parent
        return os.path.join(node.name, *reversed(parts)) if parts else node.name

    def find(self, path: str) -> Node | None:
        """Locate the node for an absolute path inside this scan (case-insensitive)."""
        try:
            rel = os.path.relpath(os.path.abspath(path), self.root_path)
        except ValueError:  # different drive / UNC share
            return None
        if rel == '.':
            return self.root
        if rel.startswith('..'):
            return None
        node = self.root
        for part in rel.split(os.sep):
            low = part.lower()
            node = next((c for c in node.children or () if c.name.lower() == low), None)
            if node is None:
                return None
        return node

    def ancestors(self, node: Node) -> list[Node]:
        chain = []
        while node is not None:
            chain.append(node)
            node = node.parent
        return chain[::-1]

    def own_files(self, node: Node, limit: int = 300) -> tuple[list[dict], int, int]:
        """List a directory's files live: (largest ``limit`` files, total count, rest bytes)."""
        path = self.path_of(node)
        rows = []
        try:
            with os.scandir(fsops.long_path(path)) as it:
                for entry in it:
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if stat.S_ISDIR(st.st_mode) or fsops.st_is_link(st):
                        continue
                    attrs = getattr(st, 'st_file_attributes', 0)
                    rows.append({'name': entry.name, 'path': os.path.join(path, entry.name),
                                 'size': 0 if attrs & winapi.CLOUD_ATTRIBUTES else st.st_size,
                                 'mtime': st.st_mtime, 'cloud': bool(attrs & winapi.CLOUD_ATTRIBUTES)})
        except OSError:
            return [], 0, 0
        rows.sort(key=lambda r: r['size'], reverse=True)
        rest = sum(r['size'] for r in rows[limit:])
        return rows[:limit], len(rows), rest

    def ext_summary(self) -> list[dict]:
        cats: dict = {}
        for ext, (count, size) in self.exts.items():
            cat = paths.category_of('x' + ext)
            slot = cats.setdefault(cat, {'category': cat, 'label': paths.category_label(cat), 'count': 0, 'bytes': 0,
                                         'top_exts': []})
            slot['count'] += count
            slot['bytes'] += size
            slot['top_exts'].append((size, ext or '(无扩展名)'))
        out = sorted(cats.values(), key=lambda c: c['bytes'], reverse=True)
        for c in out:
            c['top_exts'] = [e for _, e in sorted(c['top_exts'], reverse=True)[:6]]
        return out

    def remove_path(self, path: str, removed_bytes: int, removed_files: int):
        """Reflect a deletion in the tree without rescanning."""
        node = self.find(path)
        target_parent = None
        if node is not None and node is not self.root:
            target_parent = node.parent
            target_parent.children = tuple(c for c in target_parent.children or () if c is not node) or None
            removed_bytes, removed_files = node.size, node.files
        else:
            target_parent = self.find(os.path.dirname(path))
        n = target_parent
        while n is not None:
            n.size = max(0, n.size - removed_bytes)
            n.files = max(0, n.files - removed_files)
            n = n.parent
        key = path.lower()
        self.top_files = [f for f in self.top_files
                          if f['path'].lower() != key and not f['path'].lower().startswith(key + os.sep)]
