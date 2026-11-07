"""One-use, short-lived JSON hand-off for an explicitly requested elevated restart.

No executable objects, settings, keys or jobs are restored. The expected digest travels
with the launch arguments; the file is consumed only by the replacement process.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import time
import uuid

from . import __version__, fsops, paths
from .scanner import Node, SpaceScan

MAX_BYTES = 256 * 1024 ** 2
TTL = 600
FIELDS = ('ai_results', 'dups', 'junk_rows', 'startup_ai', 'app_ai', 'service_ai',
          'app_residues', 'insights', 'file_ai', 'dup_ai', 'plan')
PAGES = ('home', 'junk', 'space', 'mine', 'large', 'dups', 'apps', 'startup', 'history', 'settings')
VALUES = ('name', 'size', 'logical_size', 'files', 'dirs', 'mtime', 'changed', 'flags')


def ui_state(selection=None, page='home'):
    selection = selection if isinstance(selection, dict) else {}
    return {'page': page if page in PAGES else 'home', 'selection': {
        k: list(dict.fromkeys(v for v in selection.get(k, []) if isinstance(v, str)))
        if isinstance(selection.get(k), list) else [] for k in ('rules', 'paths', 'items')}}


def identity(backend):
    return [paths.key(backend.env.get('USERPROFILE', '')),
            paths.key(backend.sandbox.root) if backend.sandbox else None]


def scan_data(scan):
    if scan is None or not scan.complete:
        return None
    return {**{k: getattr(scan, k) for k in ('multiple', 'roots', 'root_path', 'top_files', 'exts', 'stats', 'finished_at')},
            # Keep explicit child lists: removed directories remain in nodes for stable IDs.
            'nodes': [[*[getattr(n, k) for k in VALUES], n.parent.id if n.parent else None,
                       [c.id for c in n.children or ()]] for n in scan.nodes]}


def restore_scan(data, backend):
    if data is None:
        return None
    scan = SpaceScan(data['roots'] if data['multiple'] else data['root_path'])
    scan.nodes = []
    for i, row in enumerate(data['nodes']):
        node = Node(row[0], None)
        for k, value in zip(VALUES, row[:8]):
            setattr(node, k, value)
        node.id = i
        scan.nodes.append(node)
    for node, row in zip(scan.nodes, data['nodes']):
        node.parent = scan.nodes[row[8]] if row[8] is not None else None
        if node.parent is not None and node.parent.id >= node.id:
            raise ValueError('invalid parent')
    for node, row in zip(scan.nodes, data['nodes']):
        node.children = tuple(scan.nodes[i] for i in row[9]) or None
        if any(c.parent is not node for c in node.children or ()):
            raise ValueError('invalid children')
    scan.root = scan.nodes[0]
    for k in ('top_files', 'exts', 'stats', 'finished_at'):
        setattr(scan, k, data[k])
    from .checkup import space_skip
    scan.skip = space_skip(backend.env, backend.policy(), backend.app_dirs)
    scan.complete = True
    return scan


def _file(data_dir, ticket):
    if not isinstance(ticket, str) or not re.fullmatch(r'[0-9a-f]{32}:[0-9a-f]{64}', ticket):
        raise ValueError('invalid hand-off ticket')
    return os.path.join(data_dir, 'restart-' + ticket[:32] + '.json.gz')


def discard(data_dir, ticket):
    try:
        os.unlink(_file(data_dir, ticket))
    except FileNotFoundError:
        pass


def save(backend):
    ticket = uuid.uuid4().hex + ':' + '0' * 64
    filename = _file(backend.data_dir, ticket)
    payload = {'version': __version__, 'created': time.time(), 'identity': identity(backend),
               'ui': ui_state(**backend.restart_ui), 'scan': scan_data(backend.scan),
               'results': {k: getattr(backend, k) for k in FIELDS}}
    try:
        with gzip.open(filename, 'wt', encoding='utf-8', compresslevel=1) as fh:
            json.dump(payload, fh, ensure_ascii=False, separators=(',', ':'))
            fh.flush()
            if fh.buffer.tell() > MAX_BYTES:
                raise ValueError('检查结果过大，无法保留结果重启；请先关闭后以管理员身份打开')
        with open(filename, 'rb') as fh:
            digest = hashlib.file_digest(fh, 'sha256').hexdigest()
        return ticket[:33] + digest
    except Exception:
        discard(backend.data_dir, ticket)
        raise


def consume(backend, ticket):
    filename = _file(backend.data_dir, ticket)
    try:
        st = os.lstat(filename)
        if fsops.st_is_link(st) or st.st_size > MAX_BYTES:
            raise ValueError('invalid hand-off file')
        # Read and verify the exact bytes that are decompressed, avoiding a path reopen race.
        with open(filename, 'rb') as fh:
            compressed = fh.read(MAX_BYTES + 1)
        if len(compressed) > MAX_BYTES or hashlib.sha256(compressed).hexdigest() != ticket[33:]:
            raise ValueError('hand-off checksum mismatch')
        import io
        with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as fh:
            raw = fh.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError('hand-off too large')
        data = json.loads(raw)
        if data['version'] != __version__ or not 0 <= time.time() - data['created'] < TTL or data['identity'] != identity(backend):
            raise ValueError('expired or incompatible hand-off')
        scan = restore_scan(data['scan'], backend)
        results = {k: data['results'][k] for k in FIELDS}
        results['ai_results'] = {int(k): v for k, v in results['ai_results'].items()}
        ui = ui_state(**data['ui'])
        backend.scan = scan
        for k, v in results.items():
            setattr(backend, k, v)
        if backend.app_residues and backend.plan:
            by_id = {i['id']: i for i in backend.plan['items']}
            backend.app_residues['items'] = [by_id.get(i['id'], i) for i in backend.app_residues['items']]
        backend.resume = {**ui, 'restored': True, 'refresh_pending': backend.admin,
                          'message': '已保留提权前的检查结果和勾选。空间扫描中未读取的位置仍标为不完整，可按需重扫该目录。'}
        if backend.plan:
            backend.plan['resumed'] = True
    finally:
        discard(backend.data_dir, ticket)
