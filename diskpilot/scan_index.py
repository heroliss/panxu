"""Durable, bounded metadata observation; never a filesystem snapshot.

Names are spooled into SQLite before metadata work. A live scandir handle is
advanced, not reopened for every model turn. OS enumeration handles cannot be
serialized: after a process restart only unfinished directory enumeration is
replayed, explicitly metered and deduplicated against the durable spool. Finished
directories, metadata work, and keyset page cursors survive unchanged.

No contents are read and no user file is changed. Directory observations are
never actionable: a complete index is still not a current subtree validation.
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import sqlite3
import stat
import threading
import time
from pathlib import Path

from .scanner import (_allocated, _ancestor_project, _checked_root, _digest,
                      _identity, _is_reparse, _metadata_signature, _project_at,
                      metadata_item)


_TERMINAL = ('done', 'partial', 'failed', 'depth_limited')
_PAGE_MAX = 200
_TOTAL_MAX = 100000


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _integer(value, low, high, name):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ValueError(f'{name} 必须为 {low} 到 {high} 的整数')
    return value


class ScanIndex:
    def __init__(self, db_path, *, max_total_entries=_TOTAL_MAX):
        self.max_total_entries = _integer(max_total_entries, 1, _TOTAL_MAX, 'max_total_entries')
        self.path = Path(db_path).absolute()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._live = {}
        self._closed = False
        self._db = sqlite3.connect(str(self.path), timeout=20, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute('PRAGMA journal_mode=WAL')
        self._db.execute('PRAGMA synchronous=NORMAL')
        self._db.executescript('''
            CREATE TABLE IF NOT EXISTS scans (
                id TEXT PRIMARY KEY, root TEXT NOT NULL, root_id TEXT NOT NULL,
                project TEXT, max_depth INTEGER NOT NULL, status TEXT NOT NULL,
                created REAL NOT NULL, updated REAL NOT NULL, work INTEGER DEFAULT 0,
                replay_work INTEGER DEFAULT 0, rechecks INTEGER DEFAULT 0,
                entry_limit INTEGER NOT NULL DEFAULT 100000,
                spooled INTEGER NOT NULL DEFAULT 1, limit_reached INTEGER NOT NULL DEFAULT 0,
                warning_json TEXT NOT NULL DEFAULT '[]');
            CREATE TABLE IF NOT EXISTS directories (
                scan_id TEXT NOT NULL, id TEXT NOT NULL, path TEXT NOT NULL,
                parent_id TEXT, depth INTEGER NOT NULL, project TEXT,
                metadata_signature TEXT NOT NULL, state TEXT NOT NULL,
                replay INTEGER NOT NULL DEFAULT 0, files INTEGER DEFAULT 0,
                dirs INTEGER DEFAULT 1, logical INTEGER DEFAULT 0,
                unique_bytes INTEGER DEFAULT 0, allocated INTEGER DEFAULT 0,
                allocation_unknown INTEGER DEFAULT 0, protected INTEGER DEFAULT 0,
                skipped INTEGER DEFAULT 0, errors INTEGER DEFAULT 0,
                gaps INTEGER DEFAULT 0, PRIMARY KEY(scan_id,id));
            CREATE INDEX IF NOT EXISTS directory_work ON directories(scan_id,state,depth);
            CREATE INDEX IF NOT EXISTS directory_children ON directories(scan_id,parent_id,state);
            CREATE TABLE IF NOT EXISTS observations (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, scan_id TEXT NOT NULL,
                path TEXT NOT NULL, parent_id TEXT, depth INTEGER NOT NULL,
                stage TEXT NOT NULL, item_id TEXT, item_json TEXT,
                delivered INTEGER NOT NULL DEFAULT 0, UNIQUE(scan_id,path));
            CREATE INDEX IF NOT EXISTS observation_work ON observations(scan_id,stage,seq);
            CREATE INDEX IF NOT EXISTS observation_delivery ON observations(scan_id,delivered,stage,seq);
            CREATE INDEX IF NOT EXISTS observation_children ON observations(scan_id,parent_id,stage);
            CREATE INDEX IF NOT EXISTS observation_item ON observations(scan_id,item_id);
            CREATE TABLE IF NOT EXISTS entities (
                scan_id TEXT NOT NULL, directory_id TEXT NOT NULL, identity TEXT NOT NULL,
                size INTEGER NOT NULL, allocated INTEGER, signature TEXT NOT NULL,
                PRIMARY KEY(scan_id,directory_id,identity));
        ''')
        # Preserve observations from development versions of the index too.
        columns = {row['name'] for row in self._db.execute('PRAGMA table_info(scans)')}
        for name, declaration in [('entry_limit', 'INTEGER NOT NULL DEFAULT 100000'),
                                  ('spooled', 'INTEGER NOT NULL DEFAULT 1'),
                                  ('limit_reached', 'INTEGER NOT NULL DEFAULT 0')]:
            if name not in columns:
                self._db.execute(f'ALTER TABLE scans ADD COLUMN {name} {declaration}')
                if name == 'spooled':
                    self._db.execute('UPDATE scans SET spooled=(SELECT COUNT(*) FROM observations WHERE scan_id=scans.id)')
        # A fresh owner has no OS handles. Do not erase acquired names or rows.
        with self._db:
            rows = self._db.execute("SELECT scan_id,id FROM directories WHERE state='enumerating'").fetchall()
            for row in rows:
                self._db.execute("UPDATE directories SET state='queued',replay=1 WHERE scan_id=? AND id=?", tuple(row))
                self._db.execute('UPDATE scans SET rechecks=rechecks+1 WHERE id=?', (row['scan_id'],))
                self._warn(row['scan_id'], '进程已重启：未完成目录需重新枚举名称；已取得条目保留，重查工作单独计量。')

    def close(self):
        with self._lock:
            if not self._closed:
                for _, iterator in self._live.values():
                    iterator.close()
                self._live.clear()
                self._db.close()
                self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def __del__(self):
        # Normal owners call close(); also avoid leaking directory handles when
        # a cancelled task owner is collected during interpreter shutdown.
        try:
            self.close()
        except Exception:
            pass

    def _scan(self, scan_id):
        if self._closed:
            raise RuntimeError('扫描索引已关闭')
        row = self._db.execute('SELECT * FROM scans WHERE id=?', (scan_id,)).fetchone()
        if row is None:
            raise ValueError('未知 scan_id')
        return row

    def _warn(self, scan_id, text):
        row = self._db.execute('SELECT warning_json FROM scans WHERE id=?', (scan_id,)).fetchone()
        values = json.loads(row[0])
        if text not in values:
            values = (values + [text])[-20:]
            self._db.execute('UPDATE scans SET warning_json=? WHERE id=?', (_json(values), scan_id))

    def _invalidate(self, scan_id, reason):
        self._db.execute("UPDATE scans SET status='invalidated',updated=? WHERE id=?", (time.time(), scan_id))
        self._warn(scan_id, '观察已失效：' + reason + '；保留旧记录供查看，须重新开始扫描。')
        active = self._live.pop(scan_id, None)
        if active:
            active[1].close()

    def _pause(self, scan_id):
        """Persist a pause without retaining an OS enumeration handle."""
        active = self._live.pop(scan_id, None)
        if active:
            active[1].close()
            changed = self._db.execute("UPDATE directories SET state='queued',replay=1 WHERE scan_id=? AND id=? AND state='enumerating'",
                                       (scan_id, active[0])).rowcount
            if changed:
                self._db.execute('UPDATE scans SET rechecks=rechecks+1 WHERE id=?', (scan_id,))
                self._warn(scan_id, '暂停已释放目录枚举句柄；恢复时重新枚举该未完成目录，保留既有条目并计量重查工作。')
        self._db.execute("UPDATE scans SET status='cancelled' WHERE id=? AND status='running'", (scan_id,))

    def _directory(self, scan_id, ident):
        return self._db.execute('SELECT * FROM directories WHERE scan_id=? AND id=?', (scan_id, ident)).fetchone()

    def _guard_directory(self, scan_id, directory):
        try:
            _, info = _checked_root(directory['path'])
            if _metadata_signature(directory['path'], info) != directory['metadata_signature']:
                self._invalidate(scan_id, '目录身份或元数据变化')
                return False
            return True
        except (OSError, ValueError):
            self._invalidate(scan_id, '目录已不可访问或变为链接/重解析点')
            return False

    def _guard(self, scan_id):
        scan = self._scan(scan_id)
        if scan['status'] == 'invalidated':
            return False
        root = self._directory(scan_id, scan['root_id'])
        if not self._guard_directory(scan_id, root):
            return False
        if _ancestor_project(scan['root']) != scan['project']:
            self._invalidate(scan_id, '祖先项目保护标记变化')
            return False
        return True

    def start(self, root, *, max_depth=12):
        _integer(max_depth, 0, 64, 'max_depth')
        absolute, info = _checked_root(root)
        project = _ancestor_project(absolute)
        item = metadata_item(absolute, info, root=absolute, depth=0, parent_id=None, project=project, is_dir=True)
        ident = secrets.token_hex(16)
        state = 'queued' if max_depth else 'depth_limited'
        now = time.time()
        with self._lock, self._db:
            self._db.execute('INSERT INTO scans(id,root,root_id,project,max_depth,status,created,updated,entry_limit) VALUES(?,?,?,?,?,?,?,?,?)',
                             (ident, absolute, item['id'], project, max_depth, 'running', now, now, self.max_total_entries))
            self._db.execute('''INSERT INTO directories(scan_id,id,path,parent_id,depth,project,metadata_signature,state,protected,gaps)
                                VALUES(?,?,?,?,?,?,?,?,?,?)''',
                             (ident, item['id'], absolute, None, 0, project, _metadata_signature(absolute, info), state,
                              int(item['risk'] == 'protected'), int(max_depth == 0)))
            self._db.execute('INSERT INTO observations(scan_id,path,parent_id,depth,stage,item_id,item_json) VALUES(?,?,?,?,?,?,?)',
                             (ident, absolute, None, 0, 'observed', item['id'], _json(item)))
            if not max_depth:
                self._warn(ident, '达到深度限制；未展开目录不能整组处理。')
                self._db.execute("UPDATE scans SET status='partial' WHERE id=?", (ident,))
        return ident

    def _ancestors(self, scan_id, ident):
        while ident:
            row = self._directory(scan_id, ident)
            yield row
            ident = row['parent_id']

    def _add_counts(self, scan_id, parent_id, **values):
        allowed = {'files', 'dirs', 'logical', 'protected', 'skipped', 'errors', 'gaps'}
        assert values and set(values).issubset(allowed)
        assignments = ','.join(f'{key}={key}+?' for key in values)
        for directory in self._ancestors(scan_id, parent_id):
            self._db.execute(f'UPDATE directories SET {assignments} WHERE scan_id=? AND id=?',
                             (*values.values(), scan_id, directory['id']))

    def _metadata(self, scan, row):
        scan_id = scan['id']
        parent = self._directory(scan_id, row['parent_id'])
        if not self._guard_directory(scan_id, parent):
            return
        try:
            info = os.lstat(row['path'])
            if _is_reparse(info) or not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                self._db.execute("UPDATE observations SET stage='skipped' WHERE seq=?", (row['seq'],))
                self._add_counts(scan_id, row['parent_id'], skipped=1, gaps=1)
                self._warn(scan_id, '已跳过链接、重解析点或非普通条目；覆盖不完整，不能整目录处理。')
                return
            is_dir = stat.S_ISDIR(info.st_mode)
            project = parent['project'] or (_project_at(row['path']) if is_dir else None)
            item = metadata_item(row['path'], info, root=scan['root'], depth=row['depth'],
                                 parent_id=row['parent_id'], project=project, is_dir=is_dir)
            item['details'].update(scan_id=scan_id, observation_index=True)
            if is_dir:
                state = 'queued' if row['depth'] < scan['max_depth'] else 'depth_limited'
                self._db.execute('''INSERT INTO directories(scan_id,id,path,parent_id,depth,project,metadata_signature,state,protected,gaps)
                                    VALUES(?,?,?,?,?,?,?,?,?,?)''',
                                 (scan_id, item['id'], row['path'], row['parent_id'], row['depth'], project,
                                  _metadata_signature(row['path'], info), state, int(item['risk'] == 'protected'), int(state == 'depth_limited')))
                self._add_counts(scan_id, row['parent_id'], dirs=1, protected=int(item['risk'] == 'protected'), gaps=int(state == 'depth_limited'))
                if state == 'depth_limited':
                    self._warn(scan_id, '达到深度限制；未展开目录不能整组处理。')
            else:
                item['signature'] = _metadata_signature(row['path'], info)
                item['details'].update(complete=True, subtree_complete=True,
                                       can_simulate_delete=item['risk'] != 'protected', allocated_known=item['allocated_bytes'] is not None)
                item['actions'] = ['delete'] if item['risk'] != 'protected' else []
                self._add_counts(scan_id, row['parent_id'], files=1, logical=info.st_size, protected=int(item['risk'] == 'protected'))
                for directory in self._ancestors(scan_id, row['parent_id']):
                    identity = _identity(info, row['path'])
                    prior = self._db.execute('SELECT * FROM entities WHERE scan_id=? AND directory_id=? AND identity=?',
                                             (scan_id, directory['id'], identity)).fetchone()
                    # Compare entity metadata, not the path-containing signature.
                    entity_signature = _digest([str(info.st_size), str(info.st_mtime_ns), str(info.st_ctime_ns), str(info.st_nlink)])
                    if prior is not None:
                        if prior['signature'] != entity_signature:
                            self._invalidate(scan_id, '同一文件实体在观察期间变化')
                            return
                        continue
                    allocated = _allocated(info)
                    self._db.execute('INSERT INTO entities VALUES(?,?,?,?,?,?)',
                                     (scan_id, directory['id'], identity, info.st_size, allocated, entity_signature))
                    self._db.execute('''UPDATE directories SET unique_bytes=unique_bytes+?,allocated=allocated+?,
                                        allocation_unknown=allocation_unknown+? WHERE scan_id=? AND id=?''',
                                     (info.st_size, allocated or 0, int(allocated is None), scan_id, directory['id']))
            self._db.execute("UPDATE observations SET stage='observed',item_id=?,item_json=? WHERE seq=?",
                             (item['id'], _json(item), row['seq']))
        except OSError as exc:
            self._db.execute("UPDATE observations SET stage='failed' WHERE seq=?", (row['seq'],))
            self._add_counts(scan_id, row['parent_id'], errors=1, gaps=1)
            self._warn(scan_id, f'条目元数据读取失败：{type(exc).__name__}；保留未覆盖状态。')

    def _enumerate(self, scan):
        scan_id = scan['id']
        active = self._live.get(scan_id)
        if active:
            directory = self._directory(scan_id, active[0])
            if directory['state'] != 'enumerating':
                active[1].close()
                del self._live[scan_id]
                active = None
        if not active:
            directory = self._db.execute("SELECT * FROM directories WHERE scan_id=? AND state='queued' ORDER BY depth,rowid LIMIT 1", (scan_id,)).fetchone()
            if directory is None:
                return False
            if not self._guard_directory(scan_id, directory):
                return True
            try:
                iterator = os.scandir(directory['path'])
                self._live[scan_id] = (directory['id'], iterator)
                self._db.execute("UPDATE directories SET state='enumerating' WHERE scan_id=? AND id=?", (scan_id, directory['id']))
            except OSError as exc:
                self._db.execute("UPDATE directories SET state='failed' WHERE scan_id=? AND id=?", (scan_id, directory['id']))
                self._add_counts(scan_id, directory['id'], errors=1, gaps=1)
                self._warn(scan_id, f'目录读取失败：{type(exc).__name__}；覆盖不完整。')
            return True
        if not self._guard_directory(scan_id, directory):
            return True
        if directory['replay']:
            self._db.execute('UPDATE scans SET replay_work=replay_work+1 WHERE id=?', (scan_id,))
        try:
            entry = next(active[1])
            # Only names are acquired; never ask DirEntry to follow a target.
            path = os.path.join(directory['path'], entry.name)
            if os.path.normcase(path) in {os.path.normcase(str(self.path) + suffix) for suffix in ('', '-wal', '-shm')}:
                self._add_counts(scan_id, directory['id'], skipped=1, gaps=1)
                self._warn(scan_id, '扫描索引自身的工作文件已排除；该目录不能整组处理。')
                return True
            exists = self._db.execute('SELECT 1 FROM observations WHERE scan_id=? AND path=?', (scan_id, path)).fetchone()
            if exists is None:
                current = self._scan(scan_id)
                if current['spooled'] >= current['entry_limit']:
                    active[1].close()
                    del self._live[scan_id]
                    self._db.execute("UPDATE directories SET state='limit' WHERE scan_id=? AND id=?", (scan_id, directory['id']))
                    self._add_counts(scan_id, directory['id'], gaps=1)
                    self._db.execute("UPDATE scans SET status='partial',limit_reached=1 WHERE id=?", (scan_id,))
                    self._warn(scan_id, f"达到本次索引总条目上限 {current['entry_limit']}（包含根目录）；保留已观察内容，请缩小范围开始新的扫描。")
                    return True
                self._db.execute('INSERT INTO observations(scan_id,path,parent_id,depth,stage) VALUES(?,?,?,?,?)',
                                 (scan_id, path, directory['id'], directory['depth'] + 1, 'pending'))
                self._db.execute('UPDATE scans SET spooled=spooled+1 WHERE id=?', (scan_id,))
        except StopIteration:
            active[1].close()
            del self._live[scan_id]
            if self._guard_directory(scan_id, directory):
                self._db.execute("UPDATE directories SET state='materialized',replay=0 WHERE scan_id=? AND id=?", (scan_id, directory['id']))
        except OSError as exc:
            active[1].close()
            del self._live[scan_id]
            self._db.execute("UPDATE directories SET state='failed' WHERE scan_id=? AND id=?", (scan_id, directory['id']))
            self._add_counts(scan_id, directory['id'], errors=1, gaps=1)
            self._warn(scan_id, f'目录枚举中断：{type(exc).__name__}；覆盖不完整。')
        return True

    def _finalize(self, scan_id):
        directory = self._db.execute('''SELECT d.* FROM directories d WHERE d.scan_id=? AND d.state='materialized'
            AND NOT EXISTS(SELECT 1 FROM observations o WHERE o.scan_id=d.scan_id AND o.parent_id=d.id AND o.stage='pending')
            AND NOT EXISTS(SELECT 1 FROM directories c WHERE c.scan_id=d.scan_id AND c.parent_id=d.id
                           AND c.state IN ('queued','enumerating','materialized')) ORDER BY d.depth DESC LIMIT 1''', (scan_id,)).fetchone()
        if directory is None:
            return False
        if self._guard_directory(scan_id, directory):
            state = 'partial' if directory['gaps'] else 'done'
            self._db.execute('UPDATE directories SET state=? WHERE scan_id=? AND id=?', (state, scan_id, directory['id']))
        return True

    def advance(self, scan_id, *, max_entries=256, cancel=None):
        """Perform <=max_entries acquisition/metadata/finalization operations.

        Metadata operations include at most max_depth ancestor counter updates.
        SQL bookkeeping is not an exact millisecond/I/O deadline. Output is
        separately capped; all acquired rows remain available through page().
        """
        budget = _integer(max_entries, 1, 512, 'max_entries')
        with self._lock, self._db:
            scan = self._scan(scan_id)
            done = 0
            valid = self._guard(scan_id)
            if scan['status'] == 'running' and valid:
                while done < budget:
                    try:
                        stop = cancel() if callable(cancel) else (cancel.is_set() if cancel is not None else False)
                    except Exception:
                        stop = True
                        self._warn(scan_id, '取消信号读取失败，已暂停。')
                    if stop:
                        self._pause(scan_id)
                        break
                    pending = self._db.execute("SELECT * FROM observations WHERE scan_id=? AND stage='pending' ORDER BY seq LIMIT 1", (scan_id,)).fetchone()
                    if pending is not None:
                        self._metadata(scan, pending)
                    elif self._finalize(scan_id):
                        pass
                    elif self._enumerate(scan):
                        pass
                    else:
                        root = self._directory(scan_id, scan['root_id'])
                        self._db.execute('UPDATE scans SET status=? WHERE id=?', ('completed' if root['state'] == 'done' else 'partial', scan_id))
                        break
                    done += 1
                    if self._scan(scan_id)['status'] != 'running':
                        break
            self._db.execute('UPDATE scans SET work=work+?,updated=? WHERE id=?', (done, time.time(), scan_id))
            # Deliver newly materialized rows. An unfinished restart never wipes
            # delivered observations, and rows beyond this cap remain queued.
            rows = self._db.execute("SELECT * FROM observations WHERE scan_id=? AND stage='observed' AND delivered=0 ORDER BY seq LIMIT ?", (scan_id, _PAGE_MAX - 1)).fetchall()
            items = [self._render(scan_id, row) for row in rows]
            self._db.executemany('UPDATE observations SET delivered=1 WHERE seq=?', [(row['seq'],) for row in rows])
            root_row = self._db.execute('SELECT * FROM observations WHERE scan_id=? AND item_id=?', (scan_id, scan['root_id'])).fetchone()
            if not any(item['id'] == scan['root_id'] for item in items):
                items.insert(0, self._render(scan_id, root_row))
            return self._report(scan_id, items, work_done=done)

    def _render(self, scan_id, row):
        item = json.loads(row['item_json'])
        item['details'].update(scan_id=scan_id, observation_index=True)
        if item['kind'] == 'directory':
            directory = self._directory(scan_id, item['id'])
            complete = directory['state'] == 'done'
            item.update(size_bytes=directory['unique_bytes'], allocated_bytes=directory['allocated'] if complete and not directory['allocation_unknown'] else None)
            item['signature'] = 'index-observation-sha256:' + _digest([directory['metadata_signature'], directory['state'], str(directory['logical']), str(directory['files']), str(directory['dirs'])])
            item['details'].update(complete=complete, subtree_complete=complete, can_simulate_delete=False,
                                   requires_subtree_validation=True, auto_select=False, verified_cache=False,
                                   file_count=directory['files'], directory_count=directory['dirs'],
                                   protected_entries=directory['protected'], reparse_skipped=directory['skipped'],
                                   error_count=directory['errors'], logical_bytes_all_paths=directory['logical'],
                                   unique_logical_bytes=directory['unique_bytes'], indexed_state=directory['state'])
            decision = dict(item['details']['decision_context'])
            decision.update(auto_select=False, advisory_only=True)
            if not complete:
                decision.update(personal_choice_required=True, regenerable=False)
            item['details']['decision_context'] = decision
            item['actions'] = []
            item['recommendation'] = '目录索引仅供调查；整目录处理前需另行核查完整子树' if complete else '目录观察尚不完整；可继续调查，不能整组处理'
        if self._scan(scan_id)['status'] == 'invalidated':
            item['actions'] = []
            item['details'].update(complete=False, subtree_complete=False, can_simulate_delete=False,
                                   auto_select=False, verified_cache=False, stale=True)
            item['details']['decision_context'].update(auto_select=False)
        return item

    @staticmethod
    def _cursor(scan_id, after):
        return base64.urlsafe_b64encode(_json([scan_id, after]).encode()).decode().rstrip('=')

    @staticmethod
    def _after(scan_id, cursor):
        if cursor is None:
            return 0
        try:
            if not isinstance(cursor, str) or len(cursor) > 256:
                raise ValueError()
            ident, after = json.loads(base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4)))
            if ident != scan_id or isinstance(after, bool) or not isinstance(after, int) or after < 0:
                raise ValueError()
            return after
        except (ValueError, TypeError, UnicodeError) as exc:
            raise ValueError('无效或属于其他扫描的 cursor') from exc

    def page(self, scan_id, *, cursor=None, limit=40):
        limit = _integer(limit, 1, _PAGE_MAX, 'limit')
        after = self._after(scan_id, cursor)
        with self._lock, self._db:
            self._scan(scan_id)
            self._guard(scan_id)
            # Keyset over durable acquisition order; no OFFSET or tree rescan.
            # Do not cross a pending row: it may materialize on the next step.
            pending = self._db.execute("SELECT MIN(seq) FROM observations WHERE scan_id=? AND stage='pending' AND seq>?", (scan_id, after)).fetchone()[0]
            ceiling = pending if pending is not None else 9223372036854775807
            rows = self._db.execute("SELECT * FROM observations WHERE scan_id=? AND stage='observed' AND seq>? AND seq<? ORDER BY seq LIMIT ?", (scan_id, after, ceiling, limit + 1)).fetchall()
            shown = rows[:limit]
            for row in shown:
                item = json.loads(row['item_json'])
                if item['kind'] == 'directory':
                    directory = self._directory(scan_id, item['id'])
                    if directory['state'] != 'failed' and not self._guard_directory(scan_id, directory):
                        break
                else:
                    try:
                        parent = self._directory(scan_id, row['parent_id'])
                        if not self._guard_directory(scan_id, parent):
                            break
                        info = os.lstat(item['path'])
                        if _is_reparse(info) or _metadata_signature(item['path'], info) != item['signature']:
                            self._invalidate(scan_id, '文件身份或元数据变化')
                            break
                    except (OSError, ValueError):
                        self._invalidate(scan_id, '文件已不可访问或祖先路径变化')
                        break
            items = [self._render(scan_id, row) for row in shown]
            end = shown[-1]['seq'] if shown else after
            result = self._report(scan_id, items, work_done=0)
            may_acquire = result['summary']['status'] in ('running', 'cancelled')
            result.update(cursor=self._cursor(scan_id, end), next_cursor=self._cursor(scan_id, end),
                          has_more=len(rows) > limit or may_acquire and (bool(result['coverage']['pending_metadata'])
                          or bool(result['coverage']['pending_directories'])),
                          page_count=len(items))
            return result

    def _report(self, scan_id, items, *, work_done=0):
        scan = self._scan(scan_id)
        root = self._directory(scan_id, scan['root_id'])
        states = {row[0]: row[1] for row in self._db.execute('SELECT state,COUNT(*) FROM directories WHERE scan_id=? GROUP BY state', (scan_id,))}
        pending = self._db.execute("SELECT COUNT(*) FROM observations WHERE scan_id=? AND stage='pending'", (scan_id,)).fetchone()[0]
        observed = self._db.execute("SELECT COUNT(*) FROM observations WHERE scan_id=? AND stage='observed'", (scan_id,)).fetchone()[0]
        enum_complete = not any(states.get(key, 0) for key in ('queued', 'enumerating', 'failed', 'depth_limited', 'limit'))
        complete = scan['status'] == 'completed' and root['state'] == 'done'
        coverage = dict(complete=complete, partial=not complete, enumeration_complete=enum_complete,
                        invalidated=scan['status'] == 'invalidated', observed_entries=max(0, observed - 1),
                        pending_metadata=pending, pending_directories=sum(states.get(key, 0) for key in ('queued', 'enumerating', 'materialized')),
                        directories_total=root['dirs'], directories_complete=states.get('done', 0),
                        reparse_skipped=root['skipped'], error_count=root['errors'], depth_limited=states.get('depth_limited', 0),
                        recheck_required_count=scan['rechecks'], replay_work=scan['replay_work'], work_total=scan['work'],
                        limit_reached=bool(scan['limit_reached']), entry_limit=scan['entry_limit'], spooled_entries=scan['spooled'],
                        snapshot=False, can_delete_whole_directory=False)
        aggregate = dict(file_count=root['files'], directory_count=root['dirs'], logical_bytes=root['logical'],
                         unique_logical_bytes=root['unique_bytes'], hardlink_duplicate_bytes=root['logical'] - root['unique_bytes'],
                         allocated_bytes=root['allocated'] if complete and not root['allocation_unknown'] else None,
                         observed_allocated_bytes=root['allocated'] if not root['allocation_unknown'] else None,
                         protected_entries=root['protected'], values_are_observed=True)
        summary = dict(root=scan['root'], status=scan['status'], partial=not complete,
                       cancelled=scan['status'] == 'cancelled', scanned_entries=max(0, observed - 1),
                       **aggregate, reparse_skipped=root['skipped'], error_count=root['errors'], max_depth=scan['max_depth'],
                       output_items=len(items), signature_kind='metadata_only', reads_file_contents=False,
                       work_done=work_done, scan_id=scan_id, coverage=coverage)
        summary.update(limit_reached=bool(scan['limit_reached']), entry_limit=scan['entry_limit'])
        return dict(scan_id=scan_id, items=items, summary=summary, coverage=coverage, aggregate=aggregate,
                    cursor=None, next_cursor=None, warnings=json.loads(scan['warning_json']))

    def status(self, scan_id):
        with self._lock, self._db:
            self._guard(scan_id)
            return self._report(scan_id, [])

    def cancel(self, scan_id):
        with self._lock, self._db:
            scan = self._scan(scan_id)
            if scan['status'] == 'running':
                self._pause(scan_id)
            return self._report(scan_id, [])

    def resume(self, scan_id):
        with self._lock, self._db:
            scan = self._scan(scan_id)
            if scan['status'] == 'cancelled' and self._guard(scan_id):
                self._db.execute("UPDATE scans SET status='running' WHERE id=?", (scan_id,))
            return self._report(scan_id, [])
