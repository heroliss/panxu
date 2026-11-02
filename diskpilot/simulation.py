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


def _parent(path):
    return ntpath.dirname(path) if ':' in path or path.startswith('\\\\') else os.path.dirname(path)


def has_ancestor(path, directories, *, include_self=False):
    """Directory set lookup proportional to path depth, never pairwise scans."""
    current = canonical(path)
    if include_self and current in directories:
        return True
    while True:
        parent = _parent(current)
        if not parent or parent == current:
            return False
        if parent in directories:
            return True
        current = parent


def top_level_items(items):
    files = [i for i in items if i['kind'] in ('file', 'directory')]
    directories = {canonical(i['path']) for i in files if i['kind'] == 'directory'}
    seen, roots = set(), []
    for item in files:
        path = canonical(item['path'])
        if path in seen or (item['kind'] != 'directory' and path in directories) or has_ancestor(path, directories):
            continue
        seen.add(path)
        roots.append(item)
    return roots


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
    # An invalidated container has no trustworthy aggregate. Its known children
    # remain independent observations and must not disappear behind an unknown
    # parent total.
    roots = top_level_items([item for item in items if not (
        item['kind'] == 'directory' and item.get('details', {}).get('aggregate_stale'))])
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
          CREATE TABLE IF NOT EXISTS operation_receipts (operation_id TEXT PRIMARY KEY, batch_id TEXT NOT NULL);
        ''')
        self.plans = {}
        self.revision = 0
        self._cached_key = None
        self._cached_view = None
        from .recovery import RecoveryStore
        RecoveryStore(self)

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
            self.recovery_store.discard_fixtures_in_transaction(synthetic)
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

    def publish(self, items, group, *, include_view=True):
        """Publish observations, optionally omitting the independent public view.

        Internal bounded readers already project their requested page. They must
        not copy the entire inventory merely to discard that return value.
        Public callers keep the existing independent snapshot by default.
        """
        with self.lock, self.db:
            if self.source() == 'demo':
                self.db.execute('DELETE FROM catalog')
            if group.startswith('filesystem:'):
                scope = group[len('filesystem:'):]
                for old in self._rows():
                    if old['kind'] not in ('file', 'directory'):
                        continue
                    ancestor = within(scope, old['path']) and canonical(scope) != canonical(old['path'])
                    if within(old['path'], scope):
                        self.db.execute('DELETE FROM catalog WHERE id=?', (old['id'],))
                    if ancestor:
                        # Nested observations invalidate subtree eligibility,
                        # not the ancestor's explainable identity. Dropping the
                        # row made already-returned opaque IDs unusable by the
                        # model and lost the visible path to its children.
                        old.update(size_bytes=None, allocated_bytes=None, actions=[],
                            recommendation='子目录已重新检查，旧统计失效；整目录处理前需重新核查。')
                        details = old.setdefault('details', {})
                        for field in ('logical_bytes_all_paths', 'unique_logical_bytes', 'hardlink_duplicate_bytes',
                                      'unique_file_entities', 'file_count', 'directory_count', 'protected_entries',
                                      'reparse_skipped', 'error_count', 'observed_allocated_bytes', 'hardlink_entities',
                                      'hardlink_entities_complete', 'has_external_hardlinks'):
                            details.pop(field, None)
                        details.update(complete=False, subtree_complete=False, can_simulate_delete=False,
                            aggregate_stale=True, requires_subtree_validation=True, allocated_known=False,
                            auto_select=False, verified_cache=False, contains_personal_or_unknown=True,
                            indexed_state='stale', signature_kind='aggregate_invalidated')
                        if isinstance(details.get('decision_context'), dict):
                            details['decision_context'].update(auto_select=False, regenerable=False,
                                personal_choice_required=True, advisory_only=True)
                        if not old['signature'].startswith('stale-subtree-sha256:'):
                            old['signature'] = 'stale-subtree-sha256:' + hashlib.sha256(
                                old['signature'].encode('utf-8')).hexdigest()
                        self.db.execute('UPDATE catalog SET payload=? WHERE id=?',
                                        (json.dumps(old, ensure_ascii=False), old['id']))
                        # A nested rescan also invalidates an older simulated
                        # parent's active effect, as before this identity fix.
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
        return self.view() if include_view else None

    def snapshot(self, patterns=()):
        """Internal read-only snapshot. Call view() for an independent copy."""
        with self.lock:
            key = (self.revision, tuple(patterns))
            if self._cached_key != key:
                self._cached_view = self._build_view(patterns)
                self._cached_key = key
            return self._cached_view

    def view(self, patterns=()):
        with self.lock:
            return copy.deepcopy(self.snapshot(patterns))

    def overview(self, patterns=()):
        with self.lock:
            snap = self.snapshot(patterns)
            return {key: copy.deepcopy(snap[key]) for key in ('mode', 'source', 'summary', 'history')}

    def _build_view(self, patterns=()):
        with self.lock:
            items = self._rows()
            overlays = {x['item_id']: x for x in self.db.execute('SELECT * FROM overlay WHERE active=1 ORDER BY rowid')}
            permanent = self.recovery_store.permanent_signatures()
            simulated_dirs = []
            for item in items:
                hit = overlays.get(item['id'])
                item['simulated'] = bool(hit and hit['signature'] == item['signature']
                                         or (item['id'], item.get('signature')) in permanent)
                if item['simulated'] and item['kind'] == 'directory':
                    simulated_dirs.append(item['path'])
                if is_user_protected(item, patterns):
                    item['risk'] = 'protected'
                    item['actions'] = []
                    item['recommendation'] = '用户规则保留'
                    item['reason'] = '命中保护清单。'
            simulated_paths = {canonical(p) for p in simulated_dirs}
            for item in items:
                if item['kind'] in ('file', 'directory') and has_ancestor(item['path'], simulated_paths, include_self=True):
                    item['simulated'] = True
            file_items = [x for x in items if x['kind'] in ('file', 'directory')]
            simulated = [x for x in file_items if x['simulated']]
            summary = dict(mode='simulation', source=self.source(), total_items=len(items),
                           visible_items=sum(not x['simulated'] for x in items),
                           simulated_items=sum(x['simulated'] for x in items),
                           logical_bytes=logical_estimate(file_items),
                           simulated_bytes=logical_estimate(simulated),
                           visible_file_items=sum(not x['simulated'] for x in file_items),
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
            # Internal reads stay on the locked snapshot; selected objects are
            # copied below before they become a mutable review plan.
            data = self.snapshot(patterns)
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
                    raise AppError('这项内容在检查后有了变化，请重新检查：' + item['name'], 'stale_snapshot', 409)
            observations = {x['item']['id']: fingerprint(x['item'], patterns) for x in selected}
            for entry in selected:
                item = entry['item']
                if item['kind'] in ('file', 'directory') and not item.get('details', {}).get('synthetic') and not validate_item_snapshot(item):
                    raise AppError('这项内容刚刚有了变化，请重新检查后再处理。', 'stale_snapshot', 409)
            plan_id = secrets.token_hex(12)
            public = dict(id=plan_id, mode='simulation', items=[dict(x['item'], action=x['action']) for x in selected],
                          counts={kind: sum(x['item']['kind'] == kind for x in selected) for kind in ('file', 'directory', 'startup', 'service', 'process', 'software')},
                          estimated_bytes=logical_estimate([x['item'] for x in selected]),
                          warnings=['此批次仅写入模拟记录，不删除文件、不修改启动项、不停止服务或进程、不卸载软件。'] + warnings)
            self.plans[plan_id] = dict(public=public, observations=observations, revision=self.revision,
                                       policy=policy_hash(patterns), expires=time.monotonic() + 900)
            return public

    def receipt(self, operation_id):
        with self.lock:
            row = self.db.execute('SELECT b.* FROM operation_receipts r JOIN batches b ON b.id=r.batch_id WHERE r.operation_id=?', (operation_id,)).fetchone()
            if not row:
                return None
            return dict(json.loads(row['payload']), id=row['id'], created_at=row['created_at'], status=row['status'], mode='simulation')

    def execute(self, plan_id, patterns=(), system_validator=None, *, operation_id=None, include_view=True):
        """Commit one journal transaction, optionally returning only its batch.

        Public callers retain the independent full view. The action runtime
        consumes just the receipt and must not copy the whole inventory for
        every item in a batch; identity and transaction checks are shared.
        """
        with self.lock:
            if operation_id is not None:
                if not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 80:
                    raise AppError('操作标识无效。')
                previous = self.receipt(operation_id)
                if previous:
                    return dict(self.view(patterns) if include_view else {}, batch=previous)
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
            recovery_snapshots = self.recovery_store.capture(items)
            with self.db:
                self.db.execute('INSERT INTO batches VALUES(?,?,?,?)', (batch_id, now, 'simulated', json.dumps(payload, ensure_ascii=False)))
                for item in items:
                    self.db.execute('INSERT INTO overlay VALUES(?,?,?,?,1)', (batch_id, item['id'], item['signature'], item['action']))
                self.recovery_store.retain_in_transaction(batch_id, now, items, recovery_snapshots)
                if operation_id:
                    self.db.execute('INSERT INTO operation_receipts VALUES(?,?)', (operation_id, batch_id))
            self.plans.pop(plan_id, None)
            self.revision += 1
            view = self.view(patterns) if include_view else {}
            view['batch'] = dict(payload, id=batch_id, created_at=now, status='simulated', mode='simulation')
            return view

    def undo(self, batch_id, patterns=()):
        with self.lock, self.db:
            row = self.db.execute('SELECT status FROM batches WHERE id=?', (batch_id,)).fetchone()
            if not row:
                raise AppError('批次不存在。', 'unknown_batch', 404)
            if row[0] not in ('simulated', 'partially_undone'):
                raise AppError('该批次已经撤销。', 'already_undone', 409)
            self.recovery_store.undo_in_transaction(batch_id)
            self.db.execute('''UPDATE overlay SET active=0 WHERE batch_id=? AND NOT EXISTS (
                SELECT 1 FROM recovery_entries r WHERE r.batch_id=overlay.batch_id AND r.item_id=overlay.item_id
                AND r.status IN ('retained','purged'))''', (batch_id,))
            remaining = self.db.execute("SELECT COUNT(*) FROM recovery_entries WHERE batch_id=? AND status IN ('retained','purged')", (batch_id,)).fetchone()[0]
            self.db.execute('UPDATE batches SET status=? WHERE id=?', ('partially_undone' if remaining else 'undone', batch_id))
            self.revision += 1
            self.plans.clear()
        return self.view(patterns)

    def reset(self, patterns=()):
        with self.lock, self.db:
            self.recovery_store.undo_in_transaction()
            self.db.execute('''UPDATE overlay SET active=0 WHERE NOT EXISTS (
                SELECT 1 FROM recovery_entries r WHERE r.batch_id=overlay.batch_id AND r.item_id=overlay.item_id
                AND r.status IN ('retained','purged'))''')
            self.db.execute("""UPDATE batches SET status=CASE WHEN EXISTS (
                SELECT 1 FROM recovery_entries r WHERE r.batch_id=batches.id AND r.status IN ('retained','purged'))
                THEN 'partially_undone' ELSE 'undone' END WHERE status IN ('simulated','partially_undone')""")
            self.revision += 1
            self.plans.clear()
        return self.view(patterns)
