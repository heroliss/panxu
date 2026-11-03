"""Bounded, non-recursive browsing over the shared observation catalogue.

Opening a directory observes its direct children only.  Enumeration completion
is never subtree coverage, and no file contents or links are opened here.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import threading
import time
import tempfile
from collections import OrderedDict

from .scanner import (metadata_item, _metadata_signature, _checked_root,
                      _ancestor_project, _is_reparse, _CREDENTIAL_NAMES)
from .simulation import AppError, canonical, within
from .windows import disks
from .catalog import normalize_sort, sort_value


_CLOUD_FLAGS = 0x1000 | 0x40000 | 0x400000  # offline / recall-on-open / recall-on-data


def _sensitive(path):
    for part in Path(path).parts:
        name = part.casefold()
        if name in _CREDENTIAL_NAMES or name.startswith('.env.') or name.endswith(('.pem', '.pfx', '.key')):
            return True
    return False


def _cursor(session, offset):
    data = json.dumps([session.ident, offset, session.seen, session.sort, session.direction,
                       session.task_id], separators=(',', ':')).encode()
    return base64.urlsafe_b64encode(data).decode().rstrip('=')


@dataclass
class _Directory:
    ident: str
    path: str
    signature: str
    project: str | None
    iterator: object
    root_item: dict
    rows: list = field(default_factory=list)
    base: int = 0
    seen: int = 0
    errors: int = 0
    complete: bool = False
    accessed: float = field(default_factory=time.monotonic)
    sort: str = 'enumeration'
    direction: str = 'asc'
    task_id: str | None = None
    store: object = None
    stored_count: int = 0
    sort_context: dict = field(default_factory=dict)
    sort_revision: str | None = None
    store_path: str | None = None
    cache_root: str | None = None

    @property
    def count(self):
        return self.stored_count if self.store is not None else self.base + len(self.rows)

    def close_iterator(self):
        if self.iterator is not None:
            self.iterator.close()
            self.iterator = None

    def close(self):
        self.close_iterator()
        if self.store is not None:
            self.store.close()
            self.store = None
        if self.store_path is not None:
            # These names are generated in our private cache, never supplied by
            # a browser request or selected from user files.
            if (os.path.dirname(os.path.abspath(self.store_path)) != os.path.abspath(self.cache_root or '')
                    or os.path.basename(self.store_path) != self.ident + '.sqlite3'):
                raise RuntimeError('Unexpected browser cache ownership')
            try:
                os.unlink(self.store_path)
            except FileNotFoundError:
                pass
            self.store_path = None
        self.sort_context.clear()


class DirectoryBrowser:
    """An LRU of directory iterators, independent of recursive scan jobs.

    The time bound is cooperative: Windows can block inside an individual
    filesystem call.  Every response states exactly which layer was observed.
    """
    def __init__(self, state, catalog, patterns=None, *, max_cached_directories=8,
                 ttl_seconds=120, max_cached_entries=5000, time_budget=.15,
                 entry_budget=256, drive_provider=disks):
        self.state, self.catalog = state, catalog
        self.patterns = patterns or catalog.patterns
        self.max_directories = max(1, min(32, int(max_cached_directories)))
        self.max_entries = max(101, min(10000, int(max_cached_entries)))
        self.ttl = max(0, float(ttl_seconds))
        self.time_budget = max(.001, min(1, float(time_budget)))
        self.entry_budget = max(101, min(1024, int(entry_budget)))
        self.drive_provider = drive_provider
        self._directories = OrderedDict()
        self._lock = threading.RLock()
        self._spool_cache = None

    def close(self):
        with self._lock:
            for session in self._directories.values():
                session.close()
            self._directories.clear()
            if self._spool_cache is not None:
                self._spool_cache.cleanup()
                self._spool_cache = None

    def drives(self):
        rows = []
        for value in self.drive_provider():
            path = value.get('path')
            if not isinstance(path, str) or not os.path.isabs(path):
                continue
            row = {key: value.get(key) for key in ('path', 'name', 'total_bytes', 'used_bytes', 'free_bytes')}
            row.update(kind='drive', browseable=True, read_only=True,
                       id='drive-' + hashlib.sha256(canonical(path).encode()).hexdigest()[:24])
            rows.append(row)
        return dict(items=rows, path='', parent_path=None, breadcrumbs=[dict(name='此电脑', path='')],
                    next_cursor=None, has_more=False, observed_count=len(rows),
                    enumeration_complete=True, status='completed',
                    coverage=dict(kind='drive_list', subtree_complete=False, note='磁盘入口；尚未读取磁盘内部。'),
                    revision=self.catalog.token(), selection_revision=self.catalog.selection_revision())

    @staticmethod
    def _root(path):
        if not isinstance(path, str) or not path or len(path) > 2000 or '\x00' in path:
            raise AppError('请选择一个有效的本地目录。', 'invalid_browser_path')
        if not os.path.isabs(path) or path.startswith(('\\\\', '//')):
            raise AppError('仅浏览本机绝对路径，不自动连接网络或设备路径。', 'invalid_browser_path')
        if _sensitive(path):
            raise AppError('这个位置可能保存凭据或密钥，不展开其内容。', 'protected_browser_path', 403)
        try:
            # Stop at a cloud ancestor before asking Windows for metadata
            # below it; even a child lookup may wake a cloud provider.
            chain = Path(path)
            current = chain.anchor
            for component in ('', *chain.parts[1:]):
                if component == '..':
                    current = os.path.dirname(current)
                elif component not in ('', '.'):
                    current = os.path.join(current, component)
                ancestor = os.lstat(current)
                if _is_reparse(ancestor):
                    raise ValueError('reparse ancestor')
                if getattr(ancestor, 'st_file_attributes', 0) & _CLOUD_FLAGS:
                    raise AppError('这个位置需要云端取回，浏览不会自动下载内容。', 'cloud_placeholder', 409)
            absolute, info = _checked_root(path)
        except AppError:
            raise
        except PermissionError:
            raise AppError('没有读取这个目录的权限。', 'directory_access_denied', 403) from None
        except FileNotFoundError:
            raise AppError('目录已不存在或磁盘已断开，请刷新位置。', 'directory_unavailable', 404) from None
        except NotADirectoryError:
            raise AppError('这个位置不是目录，请选择文件夹。', 'not_a_directory') from None
        except ValueError:
            raise AppError('路径含链接或重解析点，不展开此位置。', 'browser_reparse_point', 403) from None
        except OSError:
            raise AppError('无法读取这个目录，请检查位置及磁盘状态。', 'directory_unavailable', 409) from None
        if getattr(info, 'st_file_attributes', 0) & _CLOUD_FLAGS:
            raise AppError('这个位置需要云端取回，浏览不会自动下载内容。', 'cloud_placeholder', 409)
        return absolute, info

    @staticmethod
    def _entry(path, info, root, project, parent_id=None):
        is_dir = stat.S_ISDIR(info.st_mode)
        item = metadata_item(path, info, root=root, depth=0 if path == root else 1,
                             parent_id=parent_id, project=project, is_dir=is_dir)
        reparse = _is_reparse(info)
        cloud = bool(getattr(info, 'st_file_attributes', 0) & _CLOUD_FLAGS)
        blocked = reparse or cloud or _sensitive(path)
        supported = is_dir or stat.S_ISREG(info.st_mode)
        if blocked or not supported:
            item.update(risk='protected', actions=[])
        else:
            item['actions'] = [] if item['risk'] == 'protected' else ['delete']
        item['signature'] = _metadata_signature(path, info)
        item['details'].update(browser_only=True, entry_metadata_signature=item['signature'],
                               complete=not is_dir and supported and not blocked,
                               subtree_complete=not is_dir and supported and not blocked,
                               can_simulate_delete=not is_dir and not blocked and item['risk'] != 'protected',
                               size_known=not is_dir, browser_layer_only=True,
                               browseable=is_dir and not blocked,
                               browse_block_reason=('链接或重解析点不自动展开。' if reparse else
                                   '云端占位内容不自动下载。' if cloud else
                                   '凭据与密钥位置不展开。' if _sensitive(path) else None))
        # Stored logical size remains compatible with the existing catalogue.
        # The browse projection below explicitly exposes unknown directory size.
        return item

    @staticmethod
    def _same_metadata(existing, incoming):
        if not existing or existing['kind'] != incoming['kind']:
            return False
        if incoming['kind'] == 'file':
            return existing.get('signature') == incoming['signature']
        old, new = existing.get('details', {}), incoming['details']
        return all(old.get(key) == new.get(key) for key in ('file_identity', 'mtime_ns', 'ctime_ns'))

    def _publish(self, session, rows, offset):
        with self.state.lock:
            incoming_rows = [session.root_item, *rows]
            ids = list(dict.fromkeys(row['id'] for row in incoming_rows))
            placeholders = ','.join('?' for _ in ids)
            existing = {row['id']: json.loads(row['payload']) for row in self.state.db.execute(
                f'SELECT id,payload FROM catalog WHERE id IN ({placeholders})', ids)}
            changed = []
            for incoming in incoming_rows:
                old = existing.get(incoming['id'])
                same = self._same_metadata(old, incoming)
                promoted = incoming['risk'] == 'protected' and old and old.get('risk') != 'protected'
                if same and not promoted:
                    continue  # Browsing must not erase a richer scan snapshot.
                changed.append(incoming)
            if changed:
                self.state.publish(changed, f'browser:{session.ident}:{offset}:{session.seen}', include_view=False)

    def _new(self, absolute, info, *, sort='enumeration', direction='asc', task=None):
        try:
            iterator = os.scandir(absolute)
        except PermissionError:
            raise AppError('没有读取这个目录的权限。', 'directory_access_denied', 403) from None
        except OSError:
            raise AppError('无法打开这个目录，请刷新位置。', 'directory_unavailable', 409) from None
        try:
            project = _ancestor_project(absolute)
            root_item = self._entry(absolute, info, absolute, project)
            session = _Directory(secrets.token_hex(12), absolute, _metadata_signature(absolute, info), project, iterator, root_item,
                                 sort=sort, direction=direction, task_id=task.get('id') if task else None)
            if sort != 'enumeration':
                # Private cache creation precedes the directory signature. Its
                # SQLite files live one level below Temp, so browsing Temp does
                # not invalidate itself when a sort index writes its own data.
                session.cache_root = self._spool_cache.name
                session.store_path = os.path.join(session.cache_root, session.ident + '.sqlite3')
                session.store = sqlite3.connect(session.store_path, check_same_thread=False)
                session.store.execute('PRAGMA temp_store=FILE')
                session.store.execute('PRAGMA cache_size=-2048')
                session.store.execute('CREATE TABLE entries (id TEXT PRIMARY KEY,payload TEXT,unknown INTEGER,value_text TEXT,value_number REAL,name TEXT)')
                order = 'DESC' if direction == 'desc' else 'ASC'
                session.store.execute(f'CREATE INDEX ordered_entries ON entries(unknown ASC,value_number {order},value_text {order},name ASC,id ASC)')
                with self.state.lock:
                    self.catalog._index()
                    session.sort_revision = self.catalog.assessments.revision() if self.catalog.assessments else ''
                    session.sort_context = {ident: row for ident, row in self.catalog._nodes.items()
                        if row['kind'] in ('file', 'directory') and canonical(os.path.dirname(row['path'])) == canonical(absolute)}
                    if task:
                        session.sort_context.update({row['id']: row for row in self.catalog._selection_rows(task)
                            if row['kind'] in ('file', 'directory') and canonical(os.path.dirname(row['path'])) == canonical(absolute)})
            return session
        except BaseException:
            iterator.close()
            if 'session' in locals():
                session.close()
            raise

    def _sort_record(self, session, item):
        old = session.sort_context.get(item['id'])
        value_row = dict(item)
        if old and self._same_metadata(old, item):
            value_row = old
        elif item['kind'] == 'directory':
            value_row['size_known'] = False
        value = sort_value(value_row, session.sort)
        text = value if isinstance(value, str) else None
        number = value if isinstance(value, (int, float)) else None
        session.store.execute('INSERT INTO entries VALUES(?,?,?,?,?,?)',
            (item['id'], json.dumps(item, ensure_ascii=False), int(value is None), text, number,
             item['name'].casefold()))
        session.stored_count += 1

    def _advance(self, session, required):
        started, examined = time.monotonic(), 0
        while session.count < required and not session.complete:
            if examined >= self.entry_budget or examined and time.monotonic() - started >= self.time_budget:
                break
            try:
                entry = next(session.iterator)
            except StopIteration:
                session.complete = True
                session.close_iterator()
                break
            except OSError:
                session.errors += 1
                session.complete = True
                session.close_iterator()
                break
            examined += 1
            session.seen += 1
            try:
                # Match scanner identity: Windows DirEntry.stat can report
                # zero dev/ino/nlink, while lstat remains no-follow.
                info = os.lstat(os.path.join(session.path, entry.name))
                item = self._entry(os.path.join(session.path, entry.name), info, session.path,
                                   session.project, parent_id=session.root_item['id'])
                if self._spool_cache is not None and within(item['path'], self._spool_cache.name):
                    item.update(risk='protected', actions=[])
                    item['details'].update(browseable=False, can_simulate_delete=False,
                        browse_block_reason='盘序正在使用的临时排序索引，不展开或加入清理。')
            except OSError:
                session.errors += 1
                continue
            if session.store is not None:
                self._sort_record(session, item)
            else:
                session.rows.append(item)
        if session.store is not None:
            session.store.commit()
            return
        excess = len(session.rows) - self.max_entries
        if excess > 0:
            del session.rows[:excess]
            session.base += excess

    @staticmethod
    def _breadcrumbs(path):
        p = Path(path)
        rows = [dict(name='此电脑', path='')]
        current = p.anchor
        if current:
            rows.append(dict(name=current, path=current))
        for part in p.parts[1:]:
            current = os.path.join(current, part)
            rows.append(dict(name=part, path=current))
        return rows

    def page(self, path='', cursor=None, limit=50, refresh=False, task=None, sort='enumeration', direction='asc'):
        if type(limit) is not int or not 1 <= limit <= 100 or type(refresh) is not bool:
            raise AppError('每页可读取1至100项，请使用有效刷新参数。', 'invalid_browser_page')
        sort, direction = normalize_sort(sort, direction)
        if path == '' and cursor is None:
            return dict(self.drives(), sort=sort, direction=direction)
        with self._lock:
            if sort != 'enumeration' and self._spool_cache is None:
                self._spool_cache = tempfile.TemporaryDirectory(prefix='panxu-browser-sort-')
            if self._spool_cache is not None and isinstance(path, str) and os.path.isabs(path) and within(path, self._spool_cache.name):
                raise AppError('这是盘序正在使用的临时排序索引，不展开或清理。', 'owned_browser_cache', 403)
            absolute, info = self._root(path)
            key = canonical(absolute)
            signature = _metadata_signature(absolute, info)
            now = time.monotonic()
            session = self._directories.get(key)
            expired = session is not None and now - session.accessed > self.ttl
            changed = session is not None and session.signature != signature
            task_id = task.get('id') if task else None
            ordering_changed = session is not None and (session.sort != sort or session.direction != direction or session.task_id != task_id)
            assessment_revision = self.catalog.assessments.revision() if self.catalog.assessments else ''
            ratings_changed = session is not None and sort == 'rating' and session.sort_revision != assessment_revision
            offset = 0
            if cursor is not None:
                try:
                    if not isinstance(cursor, str) or len(cursor) > 512 or refresh:
                        raise ValueError()
                    value = json.loads(base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4)))
                    if (not isinstance(value, list) or len(value) != 6 or not session or value[0] != session.ident
                            or type(value[1]) is not int or type(value[2]) is not int
                            or not 0 <= value[1] <= session.count or not 0 <= value[2] <= session.seen
                            or value[1] < session.base or expired or changed or ordering_changed or ratings_changed
                            or value[3:] != [sort, direction, task_id]):
                        raise ValueError()
                    offset = value[1]
                except (ValueError, TypeError, UnicodeError, json.JSONDecodeError):
                    if session and (expired or changed or ratings_changed):
                        session.close()
                        self._directories.pop(key, None)
                    raise AppError('目录或浏览缓存已变化，请刷新当前层后继续。', 'browser_cursor_expired', 409) from None
            if refresh or expired or changed or ordering_changed or ratings_changed:
                if session:
                    session.close()
                    self._directories.pop(key, None)
                session = None
            if session is None:
                session = self._new(absolute, info, sort=sort, direction=direction, task=task)
                self._directories[key] = session
            session.accessed = now
            self._directories.move_to_end(key)
            while len(self._directories) > self.max_directories:
                _, evicted = self._directories.popitem(last=False)
                evicted.close()
            self._advance(session, session.count + self.entry_budget + 1 if session.store is not None else offset + limit + 1)
            _, current_info = self._root(absolute)
            if _metadata_signature(absolute, current_info) != session.signature:
                session.close()
                self._directories.pop(key, None)
                raise AppError('读取过程中目录发生变化，请刷新当前层。', 'browser_directory_changed', 409)
            if session.store is not None and not session.complete:
                return dict(items=[], path=absolute, parent_path='' if os.path.dirname(absolute) == absolute else os.path.dirname(absolute),
                    breadcrumbs=self._breadcrumbs(absolute), next_cursor=_cursor(session, 0), current_cursor=_cursor(session, 0),
                    has_more=True, observed_count=session.count, enumerated_entries=session.seen,
                    enumeration_complete=False, status='sorting', sort=sort, direction=direction,
                    sort_progress=dict(processed_entries=session.seen, total_entries=None, complete=False),
                    coverage=dict(kind='direct_children_only', subtree_complete=False, known_total=None, errors=session.errors,
                        note='正在分批读取当前层以准备完整排序；不会递归扫描或将局部顺序冒充完整排序。'),
                    revision=self.catalog.token(), selection_revision=self.catalog.selection_revision(task),
                    cache=dict(retained_from=0, ttl_seconds=self.ttl))
            if session.store is not None:
                order = 'DESC' if direction == 'desc' else 'ASC'
                batch = [json.loads(row[0]) for row in session.store.execute(
                    f'SELECT payload FROM entries ORDER BY unknown ASC,value_number {order},value_text {order},name ASC,id ASC LIMIT ? OFFSET ?',
                    (limit, offset))]
            else:
                start = offset - session.base
                batch = session.rows[start:start + limit]
            self._publish(session, batch, offset)
            projected = self.catalog.observed_rows([row['id'] for row in batch], task=task, include_outside=True)
            raw = {row['id']: row for row in batch}
            for row in projected:
                observed = raw[row['id']]
                details = observed['details']
                row.update(browseable=details['browseable'], browse_block_reason=details['browse_block_reason'],
                           created_at=details['timestamps']['created_at'], modified_at=details['timestamps']['modified_at'],
                           observed_at=details['timestamps']['observed_at'],
                           can_analyze=row.get('risk') != 'protected' and not row.get('simulated'))
                if row['kind'] == 'directory':
                    known = row.get('details', {}).get('complete') is True and row.get('details', {}).get('subtree_complete') is True
                    partial = not known and not row.get('details', {}).get('browser_only') and (row.get('size_bytes') or 0) > 0
                    row.update(size_known=known, size_kind='known' if known else 'observed_lower_bound' if partial else 'unknown')
                    if not known and not partial:
                        row['size_bytes'] = None
                else:
                    row.update(size_known=True, size_kind='known')
            returned = len(batch)
            has_more = offset + returned < session.count or not session.complete
            parent = os.path.dirname(absolute)
            status = 'partial' if session.errors else 'completed' if session.complete else 'reading'
            return dict(items=projected, path=absolute, parent_path='' if parent == absolute else parent,
                        breadcrumbs=self._breadcrumbs(absolute), next_cursor=_cursor(session, offset + returned) if has_more else None,
                        current_cursor=_cursor(session, offset), sort=sort, direction=direction,
                        sort_progress=dict(processed_entries=session.seen, total_entries=session.count if session.complete and not session.errors else None,
                                           complete=session.complete and not session.errors),
                        has_more=has_more, observed_count=session.count, enumerated_entries=session.seen,
                        enumeration_complete=session.complete and not session.errors, status=status,
                        coverage=dict(kind='direct_children_only', subtree_complete=False,
                                      known_total=session.count if session.complete and not session.errors else None,
                                      errors=session.errors, note='只读取当前层；目录总大小与未展开子目录未统计。'),
                        revision=self.catalog.token(), selection_revision=self.catalog.selection_revision(task),
                        cache=dict(retained_from=session.base, ttl_seconds=self.ttl))
