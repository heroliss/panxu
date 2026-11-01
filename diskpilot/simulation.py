"""A journal-backed virtual overlay. There is deliberately no live executor."""
from __future__ import annotations

import copy
import fnmatch
import hashlib
import json
import ntpath
import os
from pathlib import Path
import secrets
import sqlite3
import stat
import threading
import time
from datetime import datetime, timezone

from .demo import demo_items
from .scanner import validate_item_snapshot


class AppError(ValueError):
    def __init__(self, message, code='invalid_request', status=400):
        super().__init__(message)
        self.code, self.status = code, status


def canonical(path):
    return ntpath.normcase(ntpath.normpath(str(path))) if ':' in str(path) else os.path.normcase(os.path.abspath(path))


def within(child, parent):
    a, b = canonical(child), canonical(parent)
    return a == b or a.startswith(b.rstrip('\\/') + ('\\' if ':' in b else os.sep))


def is_reparse(st):
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, 'st_file_attributes', 0) & 0x400)


def observe_path(path, limit=10000, patterns=()):
    """Bounded metadata fingerprint, with ancestor and traversal guards."""
    p = Path(path)
    if not p.is_absolute():
        raise AppError('只接受绝对路径。')
    for ancestor in [p, *p.parents]:
        if is_reparse(ancestor.lstat()):
            raise AppError('路径包含目录链接或重解析点，已跳过。', 'reparse_point')
    rows, queue = [], [p]
    while queue:
        q = queue.pop()
        if is_user_protected({'path': str(q), 'name': q.name}, patterns):
            raise AppError('目录内有命中用户保护规则的项目，请细选。', 'protected_descendant')
        st = q.lstat()
        if is_reparse(st):
            raise AppError('目录内存在重解析点，请单独查看。', 'reparse_point')
        rows.append((str(q), st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_nlink, st.st_mode))
        if len(rows) > limit:
            raise AppError('所选目录过大，请细选子项后模拟。', 'verification_limit')
        if stat.S_ISDIR(st.st_mode):
            with os.scandir(q) as entries:
                queue.extend(Path(x.path) for x in entries)
    return hashlib.sha256(json.dumps(sorted(rows), ensure_ascii=False).encode()).hexdigest()


def fingerprint(item, patterns=()):
    if item.get('details', {}).get('synthetic'):
        return 'synthetic:' + item['signature']
    if item['kind'] in ('file', 'directory'):
        try:
            return observe_path(item['path'], patterns=patterns)
        except (OSError, PermissionError) as exc:
            raise AppError('无法重新核对目标：' + type(exc).__name__, 'target_unavailable') from None
    return item['signature']


def is_user_protected(item, patterns):
    targets = (canonical(item.get('path', '')), item.get('name', '').casefold(), item.get('owner', '').casefold())
    for raw in patterns:
        pattern = str(raw).strip().casefold()
        if not pattern:
            continue
        if any(fnmatch.fnmatch(t, pattern) or pattern in t for t in targets):
            return True
    return False


def policy_hash(patterns):
    return hashlib.sha256(json.dumps(patterns, ensure_ascii=False).encode()).hexdigest()


def logical_estimate(items):
    """Count observed shared entities once, without claiming allocation savings."""
    roots = [x for x in items if x['kind'] in ('file', 'directory') and not any(x['id'] != y['id'] and y['kind'] == 'directory' and within(x['path'], y['path']) for y in items if y['kind'] in ('file', 'directory'))]
    total, identities = 0, {}
    for item in roots:
        total += item.get('size_bytes', 0) or 0
        details = item.get('details', {})
        entities = details.get('hardlink_entities', []) if item['kind'] == 'directory' else ([dict(file_identity=details.get('file_identity'), size_bytes=item.get('size_bytes', 0))] if details.get('is_hardlink') else [])
        for entity in entities:
            identity = entity.get('file_identity')
            if not identity:
                continue
            if identity in identities:
                total -= entity.get('size_bytes', 0) or 0
            else:
                identities[identity] = True
    return max(0, total)


class SimulationState:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.data_dir / 'simulation.sqlite3', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
          PRAGMA journal_mode=WAL;
          CREATE TABLE IF NOT EXISTS catalog (id TEXT PRIMARY KEY, payload TEXT NOT NULL, grp TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS batches (id TEXT PRIMARY KEY, created_at TEXT, status TEXT, payload TEXT);
          CREATE TABLE IF NOT EXISTS overlay (batch_id TEXT, item_id TEXT, signature TEXT, action TEXT,
              active INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(batch_id,item_id));
        ''')
        self.plans = {}
        self.revision = 0

    def close(self):
        with self.lock:
            self.db.close()

    def _rows(self):
        return [json.loads(x[0]) for x in self.db.execute('SELECT payload FROM catalog ORDER BY rowid')]

    def source(self):
        row = self.db.execute("SELECT value FROM meta WHERE key='source'").fetchone()
        return row[0] if row else 'empty'

    def discard_development_fixtures(self):
        """Upgrade older development stores without touching observed user data."""
        with self.lock, self.db:
            synthetic = {x['id'] for x in self._rows() if x.get('details', {}).get('synthetic')}
            for key in synthetic:
                self.db.execute('DELETE FROM catalog WHERE id=?', (key,))
                self.db.execute('DELETE FROM overlay WHERE item_id=?', (key,))
            for row in self.db.execute('SELECT id,payload FROM batches').fetchall():
                data = json.loads(row['payload'])
                entries = data.get('items', [])
                kept = [x for x in entries if x.get('id') not in synthetic and not x.get('id', '').startswith('demo-')]
                if len(kept) == len(entries):
                    continue
                if not kept:
                    self.db.execute('DELETE FROM overlay WHERE batch_id=?', (row['id'],))
                    self.db.execute('DELETE FROM batches WHERE id=?', (row['id'],))
                else:
                    data.update(items=kept, item_count=len(kept), estimated_bytes=0)
                    self.db.execute('UPDATE batches SET payload=? WHERE id=?', (json.dumps(data, ensure_ascii=False), row['id']))
            if self.source() == 'demo':
                source = 'local' if self.db.execute('SELECT COUNT(*) FROM catalog').fetchone()[0] else 'empty'
                self.db.execute("INSERT OR REPLACE INTO meta VALUES('source',?)", (source,))
            self.revision += 1
            self.plans.clear()

    def replace_demo(self):
        with self.lock, self.db:
            self.db.execute('DELETE FROM catalog')
            for item in demo_items():
                self.db.execute('INSERT INTO catalog VALUES(?,?,?)', (item['id'], json.dumps(item, ensure_ascii=False), 'demo'))
            self.db.execute("INSERT OR REPLACE INTO meta VALUES('source','demo')")
            self.revision += 1
            self.plans.clear()
        return self.view()

    def publish(self, items, group):
        with self.lock, self.db:
            if self.source() == 'demo':
                self.db.execute('DELETE FROM catalog')
            if group.startswith('filesystem:'):
                scope = group[len('filesystem:'):]
                for old in self._rows():
                    if old['kind'] not in ('file', 'directory'):
                        continue
                    ancestor = within(scope, old['path']) and canonical(scope) != canonical(old['path'])
                    if ancestor or within(old['path'], scope):
                        self.db.execute('DELETE FROM catalog WHERE id=?', (old['id'],))
                    if ancestor:
                        # A nested rescan invalidates an older simulated parent.
                        self.db.execute('UPDATE overlay SET active=0 WHERE item_id=?', (old['id'],))
            self.db.execute('DELETE FROM catalog WHERE grp=?', (group,))
            for item in items:
                if not item.get('id') or len(json.dumps(item)) > 100000:
                    continue
                self.db.execute('INSERT OR REPLACE INTO catalog VALUES(?,?,?)',
                                (item['id'], json.dumps(item, ensure_ascii=False), group))
            self.db.execute("INSERT OR REPLACE INTO meta VALUES('source','local')")
            self.revision += 1
            self.plans.clear()
        return self.view()

    def view(self, patterns=()):
        with self.lock:
            items = self._rows()
            overlays = {x['item_id']: x for x in self.db.execute('SELECT * FROM overlay WHERE active=1 ORDER BY rowid')}
            simulated_dirs = []
            for item in items:
                hit = overlays.get(item['id'])
                item['simulated'] = bool(hit and hit['signature'] == item['signature'])
                if item['simulated'] and item['kind'] == 'directory':
                    simulated_dirs.append(item['path'])
                if is_user_protected(item, patterns):
                    item['risk'] = 'protected'
                    item['actions'] = []
                    item['recommendation'] = '用户规则保留'
                    item['reason'] = '命中保护清单。'
            for item in items:
                if item['kind'] in ('file', 'directory') and any(within(item['path'], p) for p in simulated_dirs):
                    item['simulated'] = True
            file_items = [x for x in items if x['kind'] in ('file', 'directory')]
            roots = [x for x in file_items if not any(x['id'] != y['id'] and y['kind'] == 'directory' and within(x['path'], y['path']) for y in file_items)]
            simulated = [x for x in file_items if x['simulated']]
            sim_roots = [x for x in simulated if not any(x['id'] != y['id'] and y['kind'] == 'directory' and within(x['path'], y['path']) for y in simulated)]
            summary = dict(mode='simulation', source=self.source(), total_items=len(items),
                           visible_items=sum(not x['simulated'] for x in items),
                           simulated_items=sum(x['simulated'] for x in items),
                           logical_bytes=logical_estimate(roots),
                           simulated_bytes=logical_estimate(sim_roots),
                           process_memory_bytes=sum(x.get('details', {}).get('memory_bytes', 0) or 0 for x in items if x['kind'] == 'process'),
                           by_kind={kind: sum(x['kind'] == kind for x in items) for kind in ('file', 'directory', 'startup', 'service', 'process', 'software')},
                           actual_reclaimed_bytes=0)
            return dict(mode='simulation', source=self.source(), items=items, summary=summary, history=self.history())

    def history(self):
        with self.lock:
            result = []
            for row in self.db.execute('SELECT * FROM batches ORDER BY created_at DESC LIMIT 100'):
                data = json.loads(row['payload'])
                data.update(id=row['id'], created_at=row['created_at'], status=row['status'], mode='simulation')
                result.append(data)
            return result

    def prepare(self, ids, patterns=(), action=None):
        if not isinstance(ids, list) or not ids or len(ids) > 500 or any(not isinstance(x, str) for x in ids):
            raise AppError('请选择 1–500 个项目。')
        with self.lock:
            data = self.view(patterns)
            catalog = {x['id']: x for x in data['items']}
            selected, warnings = [], []
            for key in dict.fromkeys(ids):
                if key not in catalog:
                    raise AppError('项目已不在当前清单中，请刷新。', 'unknown_item')
                item = catalog[key]
                if item['risk'] == 'protected':
                    raise AppError('所选项目受保护：' + item['name'], 'protected_item')
                if item['simulated']:
                    warnings.append(item['name'] + ' 已模拟处理，跳过重复操作。')
                    continue
                if item['kind'] == 'directory':
                    if item.get('details', {}).get('complete') is False or item.get('details', {}).get('subtree_complete') is False or item.get('details', {}).get('can_simulate_delete') is False:
                        raise AppError('目录扫描不完整或不适合整组处理，请细选：' + item['name'], 'incomplete_directory')
                    if any(y['risk'] == 'protected' and within(y.get('path', ''), item['path']) for y in data['items'] if y['kind'] in ('file', 'directory')):
                        raise AppError('目录包含受保护子项，请细选。', 'protected_descendant')
                chosen = action or (item['actions'][0] if item.get('actions') else None)
                if chosen not in item.get('actions', []):
                    raise AppError('该操作不适用于所选项目。', 'unsupported_action')
                selected.append(dict(item=copy.deepcopy(item), action=chosen))
            selected = [x for x in selected if not any(x['item']['id'] != y['item']['id'] and y['item']['kind'] == 'directory' and x['item']['kind'] in ('file', 'directory') and within(x['item']['path'], y['item']['path']) for y in selected)]
            if not selected:
                raise AppError('没有可执行的模拟项目。')
            for entry in selected:
                item = entry['item']
                if item['kind'] in ('file', 'directory') and not item.get('details', {}).get('synthetic') and not validate_item_snapshot(item):
                    raise AppError('目标已偏离扫描快照，请重新扫描：' + item['name'], 'stale_snapshot', 409)
            observations = {x['item']['id']: fingerprint(x['item'], patterns) for x in selected}
            for entry in selected:
                item = entry['item']
                if item['kind'] in ('file', 'directory') and not item.get('details', {}).get('synthetic') and not validate_item_snapshot(item):
                    raise AppError('目标在预览校验期间发生变化，请重新扫描。', 'stale_snapshot', 409)
            plan_id = secrets.token_hex(12)
            public = dict(id=plan_id, mode='simulation', items=[dict(x['item'], action=x['action']) for x in selected],
                          counts={kind: sum(x['item']['kind'] == kind for x in selected) for kind in ('file', 'directory', 'startup', 'service', 'process', 'software')},
                          estimated_bytes=logical_estimate([x['item'] for x in selected]),
                          warnings=['此批次仅写入模拟记录，不删除文件、不修改启动项、不停止服务或进程、不卸载软件。'] + warnings)
            self.plans[plan_id] = dict(public=public, observations=observations, revision=self.revision,
                                       policy=policy_hash(patterns), expires=time.monotonic() + 900)
            return public

    def execute(self, plan_id, patterns=(), system_validator=None):
        with self.lock:
            plan = self.plans.get(plan_id)
            if not plan:
                raise AppError('计划不存在、已执行或已失效，请重新预览。', 'stale_plan', 409)
            if plan['revision'] != self.revision or plan['policy'] != policy_hash(patterns) or time.monotonic() > plan['expires']:
                raise AppError('扫描或保护规则已改变，请重新预览。', 'stale_plan', 409)
            items = plan['public']['items']
            if system_validator:
                system_validator(items)
            # Read-only preflight must finish BEFORE one journal transaction.
            for item in items:
                if fingerprint(item, patterns) != plan['observations'][item['id']]:
                    raise AppError('目标在预览后发生变化，请重新扫描：' + item['name'], 'target_changed', 409)
            batch_id = secrets.token_hex(12)
            result_items = [dict(id=x['id'], name=x['name'], path=x['path'], kind=x['kind'], action=x['action'], status='simulated') for x in items]
            payload = dict(item_count=len(items), estimated_bytes=plan['public']['estimated_bytes'], items=result_items, actual_reclaimed_bytes=0)
            now = datetime.now(timezone.utc).isoformat()
            with self.db:
                self.db.execute('INSERT INTO batches VALUES(?,?,?,?)', (batch_id, now, 'simulated', json.dumps(payload, ensure_ascii=False)))
                for item in items:
                    self.db.execute('INSERT INTO overlay VALUES(?,?,?,?,1)', (batch_id, item['id'], item['signature'], item['action']))
            self.plans.pop(plan_id, None)
            self.revision += 1
            view = self.view(patterns)
            view['batch'] = dict(payload, id=batch_id, created_at=now, status='simulated', mode='simulation')
            return view

    def undo(self, batch_id, patterns=()):
        with self.lock, self.db:
            row = self.db.execute('SELECT status FROM batches WHERE id=?', (batch_id,)).fetchone()
            if not row:
                raise AppError('批次不存在。', 'unknown_batch', 404)
            if row[0] != 'simulated':
                raise AppError('该批次已经撤销。', 'already_undone', 409)
            self.db.execute('UPDATE overlay SET active=0 WHERE batch_id=?', (batch_id,))
            self.db.execute("UPDATE batches SET status='undone' WHERE id=?", (batch_id,))
            self.revision += 1
            self.plans.clear()
        return self.view(patterns)

    def reset(self, patterns=()):
        with self.lock, self.db:
            self.db.execute('UPDATE overlay SET active=0')
            self.db.execute("UPDATE batches SET status='undone' WHERE status='simulated'")
            self.revision += 1
            self.plans.clear()
        return self.view(patterns)
