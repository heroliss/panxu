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
import stat
import threading
import time
from collections import OrderedDict

from .scanner import (metadata_item, _metadata_signature, _checked_root,
                      _ancestor_project, _is_reparse, _CREDENTIAL_NAMES)
from .simulation import AppError, canonical
from .windows import disks


_CLOUD_FLAGS = 0x1000 | 0x40000 | 0x400000  # offline / recall-on-open / recall-on-data


def _sensitive(path):
    for part in Path(path).parts:
        name = part.casefold()
        if name in _CREDENTIAL_NAMES or name.startswith('.env.') or name.endswith(('.pem', '.pfx', '.key')):
            return True
    return False


def _cursor(session, offset):
    data = json.dumps([session.ident, offset, session.seen], separators=(',', ':')).encode()
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

    @property
    def count(self):
        return self.base + len(self.rows)

    def close(self):
        if self.iterator is not None:
            self.iterator.close()
            self.iterator = None


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

    def close(self):
        with self._lock:
            for session in self._directories.values():
                session.close()
            self._directories.clear()

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
            existing = {row['id']: row for row in self.state._rows()}
            changed = []
            for incoming in [session.root_item, *rows]:
                old = existing.get(incoming['id'])
                same = self._same_metadata(old, incoming)
                promoted = incoming['risk'] == 'protected' and old and old.get('risk') != 'protected'
                if same and not promoted:
                    continue  # Browsing must not erase a richer scan snapshot.
                changed.append(incoming)
            if changed:
                self.state.publish(changed, f'browser:{session.ident}:{offset}:{session.seen}')

    def _new(self, absolute, info):
        try:
            iterator = os.scandir(absolute)
        except PermissionError:
            raise AppError('没有读取这个目录的权限。', 'directory_access_denied', 403) from None
        except OSError:
            raise AppError('无法打开这个目录，请刷新位置。', 'directory_unavailable', 409) from None
        try:
            project = _ancestor_project(absolute)
            root_item = self._entry(absolute, info, absolute, project)
            return _Directory(secrets.token_hex(12), absolute, _metadata_signature(absolute, info), project, iterator, root_item)
        except BaseException:
            iterator.close()
            raise

    def _advance(self, session, required):
        started, examined = time.monotonic(), 0
        while session.count < required and not session.complete:
            if examined >= self.entry_budget or examined and time.monotonic() - started >= self.time_budget:
                break
            try:
                entry = next(session.iterator)
            except StopIteration:
                session.complete = True
                session.close()
                break
            except OSError:
                session.errors += 1
                session.complete = True
                session.close()
                break
            examined += 1
            session.seen += 1
            try:
                # Match scanner identity: Windows DirEntry.stat can report
                # zero dev/ino/nlink, while lstat remains no-follow.
                info = os.lstat(os.path.join(session.path, entry.name))
                item = self._entry(os.path.join(session.path, entry.name), info, session.path,
                                   session.project, parent_id=session.root_item['id'])
            except OSError:
                session.errors += 1
                continue
            session.rows.append(item)
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

    def page(self, path='', cursor=None, limit=50, refresh=False, task=None):
        if type(limit) is not int or not 1 <= limit <= 100 or type(refresh) is not bool:
            raise AppError('每页可读取1至100项，请使用有效刷新参数。', 'invalid_browser_page')
        if path == '' and cursor is None:
            return self.drives()
        with self._lock:
            absolute, info = self._root(path)
            key = canonical(absolute)
            signature = _metadata_signature(absolute, info)
            now = time.monotonic()
            session = self._directories.get(key)
            expired = session is not None and now - session.accessed > self.ttl
            changed = session is not None and session.signature != signature
            offset = 0
            if cursor is not None:
                try:
                    if not isinstance(cursor, str) or len(cursor) > 256 or refresh:
                        raise ValueError()
                    value = json.loads(base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4)))
                    if (not isinstance(value, list) or len(value) != 3 or not session or value[0] != session.ident
                            or type(value[1]) is not int or type(value[2]) is not int
                            or not 0 <= value[1] <= session.count or not 0 <= value[2] <= session.seen
                            or value[1] < session.base or expired or changed):
                        raise ValueError()
                    offset = value[1]
                except (ValueError, TypeError, UnicodeError, json.JSONDecodeError):
                    if session and (expired or changed):
                        session.close()
                        self._directories.pop(key, None)
                    raise AppError('目录或浏览缓存已变化，请刷新当前层后继续。', 'browser_cursor_expired', 409) from None
            if refresh or expired or changed:
                if session:
                    session.close()
                    self._directories.pop(key, None)
                session = None
            if session is None:
                session = self._new(absolute, info)
                self._directories[key] = session
            session.accessed = now
            self._directories.move_to_end(key)
            while len(self._directories) > self.max_directories:
                _, evicted = self._directories.popitem(last=False)
                evicted.close()
            self._advance(session, offset + limit + 1)
            _, current_info = self._root(absolute)
            if _metadata_signature(absolute, current_info) != session.signature:
                session.close()
                self._directories.pop(key, None)
                raise AppError('读取过程中目录发生变化，请刷新当前层。', 'browser_directory_changed', 409)
            start = offset - session.base
            batch = session.rows[start:start + limit]
            self._publish(session, batch, offset)
            projected = self.catalog.observed_rows([row['id'] for row in batch], task=task, include_outside=True)
            raw = {row['id']: row for row in batch}
            for row in projected:
                observed = raw[row['id']]
                details = observed['details']
                row.update(browseable=details['browseable'], browse_block_reason=details['browse_block_reason'],
                           size_known=row['kind'] != 'directory',
                           created_at=details['timestamps']['created_at'], modified_at=details['timestamps']['modified_at'],
                           observed_at=details['timestamps']['observed_at'],
                           can_analyze=row.get('risk') != 'protected' and not row.get('simulated'))
                if row['kind'] == 'directory':
                    row['size_bytes'] = None
            returned = len(batch)
            has_more = offset + returned < session.count or not session.complete
            parent = os.path.dirname(absolute)
            status = 'partial' if session.errors else 'completed' if session.complete else 'reading'
            return dict(items=projected, path=absolute, parent_path='' if parent == absolute else parent,
                        breadcrumbs=self._breadcrumbs(absolute), next_cursor=_cursor(session, offset + returned) if has_more else None,
                        has_more=has_more, observed_count=session.count, enumerated_entries=session.seen,
                        enumeration_complete=session.complete and not session.errors, status=status,
                        coverage=dict(kind='direct_children_only', subtree_complete=False,
                                      known_total=session.count if session.complete and not session.errors else None,
                                      errors=session.errors, note='只读取当前层；目录总大小与未展开子目录未统计。'),
                        revision=self.catalog.token(), selection_revision=self.catalog.selection_revision(task),
                        cache=dict(retained_from=session.base, ttl_seconds=self.ttl))
