"""Durable recovery journal for the simulation executor.

Only metadata and operation records are stored. Planned per-volume locations
are future executor inputs: this module never creates, moves or deletes files.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import ntpath
import os
import secrets

from .simulation import AppError, canonical, within


def _now():
    return datetime.now(timezone.utc).isoformat()


def _volume(path):
    drive, tail = ntpath.splitdrive(path)
    if drive and not drive.startswith('\\\\') and tail.startswith(('\\', '/')):
        return drive.upper() + '\\'
    return os.path.abspath(os.sep) if os.path.isabs(path) and not drive else None


class RecoveryStore:
    """Recovery entries, immutable snapshots and reviewed, idempotent plans."""
    def __init__(self, state):
        self.state = state
        previous = getattr(state, 'recovery_store', None)
        self.assessment_resolver = getattr(previous, 'assessment_resolver', None)
        self._permanent_revision = None
        self._permanent = set()
        state.recovery_store = self
        with state.lock, state.db:
            state.db.executescript('''
              CREATE TABLE IF NOT EXISTS recovery_state (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER NOT NULL);
              INSERT OR IGNORE INTO recovery_state VALUES(1,0);
              CREATE TABLE IF NOT EXISTS recovery_entries (
                id TEXT PRIMARY KEY, batch_id TEXT NOT NULL, item_id TEXT NOT NULL,
                signature TEXT NOT NULL, original_path TEXT NOT NULL, kind TEXT NOT NULL,
                volume TEXT, size_bytes INTEGER, status TEXT NOT NULL,
                revision INTEGER NOT NULL, deleted_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                payload TEXT NOT NULL, UNIQUE(batch_id,item_id));
              CREATE INDEX IF NOT EXISTS recovery_entries_status ON recovery_entries(status,deleted_at,id);
              CREATE INDEX IF NOT EXISTS recovery_entries_batch ON recovery_entries(batch_id,item_id);
              CREATE INDEX IF NOT EXISTS recovery_overlay_item ON overlay(item_id,active);
              CREATE TABLE IF NOT EXISTS recovery_plans (
                id TEXT PRIMARY KEY, action TEXT NOT NULL, status TEXT NOT NULL,
                confirmation TEXT NOT NULL, expires_at TEXT NOT NULL,
                payload TEXT NOT NULL, result TEXT);
            ''')
            self._backfill()

    def revision(self):
        return int(self.state.db.execute('SELECT revision FROM recovery_state WHERE id=1').fetchone()[0])

    def _bump(self):
        self.state.db.execute('UPDATE recovery_state SET revision=revision+1 WHERE id=1')
        return self.revision()

    def _storage(self, entry_id, path):
        volume = _volume(path)
        store = hashlib.sha256(canonical(str(self.state.data_dir)).encode()).hexdigest()[:12]
        join = ntpath.join if volume and ':' in volume else os.path.join
        root = join(volume, '.PanXu-Recovery', 'store-' + store) if volume else None
        return dict(volume=volume, planned_root=root,
                    planned_path=join(root, entry_id, ntpath.basename(path.rstrip('\\/'))) if root else None,
                    actual_path=None, actual_moved=False, volume_identity=None,
                    volume_identity_verified=False, same_volume_verified=False,
                    journal_state='simulated_retained', executor='simulation')

    @staticmethod
    def _recorded(value):
        return bool(isinstance(value, dict) and (value.get('state') not in (None, 'unanalysed')
                    or value.get('source_ref') or value.get('reason') or value.get('purpose')))

    def capture(self, items):
        """Freeze existing advice before the delete journal transaction; no LLM."""
        files = [item for item in items if item.get('action') == 'delete' and item.get('kind') in ('file', 'directory')]
        resolved = {}
        if files and self.assessment_resolver:
            resolved = self.assessment_resolver(self.state.snapshot()['items'])
        elif files and self.state.db.execute("SELECT 1 FROM sqlite_master WHERE name='assessment_records'").fetchone():
            # Without the host resolver retain direct records, with their
            # source metadata. Do not manufacture inherited historical advice.
            for row in self.state.db.execute('SELECT item_id,payload FROM assessment_records'):
                record = json.loads(row['payload'])
                resolved[row['item_id']] = dict(record, state='explicit', rating=record.get('cleanup_rating'))
        result = {}
        for item in files:
            value = item.get('assessment') or item.get('details', {}).get('assessment') or resolved.get(item['id'])
            assessment = copy.deepcopy(value) if self._recorded(value) else None
            result[item['id']] = dict(snapshot=copy.deepcopy(item), assessment=assessment,
                                      analysis_recorded=assessment is not None, snapshot_origin='delete_plan')
        return result

    def _insert(self, batch_id, at, item, *, signature, status='retained', captured=None, historical=False):
        ident = 'recovery-' + hashlib.sha256((batch_id + '\0' + item['id']).encode()).hexdigest()[:24]
        captured = captured or {}
        size = item.get('size_bytes')
        if type(size) is not int or size < 0 or item.get('kind') == 'directory' and item.get('details', {}).get('subtree_complete') is not True:
            size = None
        storage = self._storage(ident, item.get('path', ''))
        payload = dict(name=item.get('name') or ntpath.basename(item.get('path', '')), storage=storage,
                       assessment=copy.deepcopy(captured.get('assessment')),
                       analysis_recorded=bool(captured.get('analysis_recorded')),
                       snapshot=copy.deepcopy(captured.get('snapshot')) if captured.get('snapshot') else copy.deepcopy(item),
                       snapshot_origin=captured.get('snapshot_origin', 'legacy_batch'),
                       historical=historical, historical_snapshot_available=not historical,
                       mode='simulation', actual_moved=False, actual_reclaimed_bytes=0,
                       auto_expiry=None, permanent_deletion_requires_user=True)
        if status == 'restored': storage['journal_state'] = 'simulated_restored'
        cursor = self.state.db.execute('INSERT OR IGNORE INTO recovery_entries VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (ident, batch_id, item['id'], signature, item.get('path', ''), item['kind'], storage['volume'],
             size, status, 1, at, at, json.dumps(payload, ensure_ascii=False)))
        return cursor.rowcount > 0

    def retain_in_transaction(self, batch_id, at, items, captured):
        """Caller owns the same SQLite transaction as batches and overlay."""
        changed = False
        for item in items:
            if item.get('action') == 'delete' and item.get('kind') in ('file', 'directory'):
                changed = self._insert(batch_id, at, item, signature=item['signature'], captured=captured.get(item['id'])) or changed
        if changed: self._bump()

    def discard_fixtures_in_transaction(self, synthetic_ids):
        removed = set()
        for row in self.state.db.execute('SELECT id,item_id,payload FROM recovery_entries'):
            payload = json.loads(row['payload'])
            if row['item_id'] in synthetic_ids or row['item_id'].startswith('demo-') or payload.get('snapshot', {}).get('details', {}).get('synthetic'):
                removed.add(row['id'])
        if not removed: return
        self.state.db.executemany('DELETE FROM recovery_entries WHERE id=?', [(ident,) for ident in removed])
        for row in self.state.db.execute('SELECT id,payload FROM recovery_plans').fetchall():
            if any(item['id'] in removed for item in json.loads(row['payload']).get('items', [])):
                self.state.db.execute('DELETE FROM recovery_plans WHERE id=?', (row['id'],))
        self._bump()

    def _backfill(self):
        if self.state.db.execute("SELECT 1 FROM meta WHERE key='recovery_backfill_v1'").fetchone():
            return
        changed = False
        for batch in self.state.db.execute('SELECT * FROM batches ORDER BY created_at,id').fetchall():
            for item in json.loads(batch['payload']).get('items', []):
                if item.get('action') != 'delete' or item.get('kind') not in ('file', 'directory') or not item.get('id'):
                    continue
                overlay = self.state.db.execute('SELECT signature,active FROM overlay WHERE batch_id=? AND item_id=?',
                                                (batch['id'], item['id'])).fetchone()
                signature = overlay['signature'] if overlay else item.get('signature', 'legacy:unknown')
                status = 'retained' if overlay and overlay['active'] and batch['status'] == 'simulated' else 'restored'
                # Old batch rows lack the original size and model response.
                # Reading today's catalogue would not recover those snapshots.
                changed = self._insert(batch['id'], batch['created_at'], item, signature=signature,
                                       status=status, historical=True) or changed
        self.state.db.execute("INSERT OR REPLACE INTO meta VALUES('recovery_backfill_v1','1')")
        if changed: self._bump()

    def _row(self, row):
        value = json.loads(row['payload'])
        value.update({key: row[key] for key in ('id', 'batch_id', 'item_id', 'signature', 'original_path', 'kind',
                      'volume', 'size_bytes', 'status', 'revision', 'deleted_at', 'updated_at')})
        value['size_known'] = value['size_bytes'] is not None
        return value

    def _current_reason(self, entry):
        overlay = self.state.db.execute('SELECT * FROM overlay WHERE item_id=? AND active=1 ORDER BY rowid DESC LIMIT 1',
                                        (entry['item_id'],)).fetchone()
        if not overlay or overlay['batch_id'] != entry['batch_id'] or overlay['signature'] != entry['signature'] or overlay['action'] != 'delete':
            return '这条删除记录已不再是当前有效记录。'
        current = self.state.db.execute('SELECT payload FROM catalog WHERE id=?', (entry['item_id'],)).fetchone()
        if not current or json.loads(current[0]).get('signature') != entry['signature']:
            return '原对象的记录已变化或缺失，请先核对。'
        return ''

    def _ancestor_reason(self, entry, selected=()):
        for row in self.state.db.execute("SELECT * FROM recovery_entries WHERE kind='directory' AND status IN ('retained','purged')"):
            if row['id'] in selected or row['id'] == entry['id'] or canonical(entry['original_path']) == canonical(row['original_path']):
                continue
            if within(entry['original_path'], row['original_path']):
                if row['status'] == 'purged':
                    current = self.state.db.execute('SELECT payload FROM catalog WHERE id=?', (row['item_id'],)).fetchone()
                    effective = bool(current and json.loads(current[0]).get('signature') == row['signature'])
                else:
                    effective = not self._current_reason(row)
                if effective:
                    return '上层目录仍在已删除区，请先还原上层目录。' if row['status'] == 'retained' else '上层目录已永久删除，不能单独还原此项。'
        return ''

    def _public(self, row):
        entry = self._row(row)
        reason = self._current_reason(entry) or self._ancestor_reason(entry) if entry['status'] == 'retained' else '这项记录已经处理。'
        entry.update(can_restore=entry['status'] == 'retained' and not reason,
                     can_purge=entry['status'] == 'retained', restore_unavailable_reason=reason)
        return entry

    @staticmethod
    def _aggregate(rows):
        rows = list(rows)
        return dict(retained_count=len(rows), logical_bytes=sum(row['size_bytes'] or 0 for row in rows),
                    unknown_size_count=sum(row['size_bytes'] is None for row in rows),
                    file_count=sum(row['kind'] == 'file' for row in rows), directory_count=sum(row['kind'] == 'directory' for row in rows))

    def summary(self):
        with self.state.lock:
            rows = self.state.db.execute("SELECT volume,size_bytes,kind FROM recovery_entries WHERE status='retained'").fetchall()
            volumes = []
            for volume in sorted({row['volume'] for row in rows}, key=lambda value: value or ''):
                values = [row for row in rows if row['volume'] == volume]
                volumes.append(dict(volume=volume, **self._aggregate(values), volume_identity_verified=False))
            return dict(**self._aggregate(rows), volumes=volumes, revision=self.revision(), mode='simulation',
                        actual_reclaimed_bytes=0, auto_expiry=None, logical_bytes_are_total=all(row['size_bytes'] is not None for row in rows))

    def list(self, page=1, limit=50, status='retained', volume=''):
        if type(page) is not int or not 1 <= page <= 100000 or type(limit) is not int or not 1 <= limit <= 100:
            raise AppError('每页可查看1至100项，请选择有效页码。', 'invalid_recovery_page')
        if status not in ('retained', 'restored', 'purged', 'all') or not isinstance(volume, str) or len(volume) > 2000:
            raise AppError('已删除区筛选无效。', 'invalid_recovery_filter')
        filters, args = [], []
        if status != 'all': filters.append('status=?'); args.append(status)
        if volume: filters.append('volume=?'); args.append(volume)
        where = ' WHERE ' + ' AND '.join(filters) if filters else ''
        with self.state.lock:
            total = self.state.db.execute('SELECT COUNT(*) FROM recovery_entries' + where, args).fetchone()[0]
            rows = self.state.db.execute('SELECT * FROM recovery_entries' + where + ' ORDER BY deleted_at DESC,id DESC LIMIT ? OFFSET ?',
                                         [*args, limit, (page - 1) * limit]).fetchall()
            return dict(items=[self._public(row) for row in rows], page=page, limit=limit, total=total,
                        has_more=page * limit < total, revision=self.revision(), mode='simulation', actual_reclaimed_bytes=0)

    def _selected(self, ids):
        if not isinstance(ids, list) or not 1 <= len(ids) <= 500 or any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
            raise AppError('请选择1至500项不同的已删除记录。', 'invalid_recovery_selection')
        rows = {row['id']: row for row in self.state.db.execute('SELECT * FROM recovery_entries WHERE id IN (' + ','.join('?' for _ in ids) + ')', ids)}
        if any(ident not in rows for ident in ids):
            raise AppError('某些已删除记录已不存在，请刷新列表。', 'recovery_entry_unavailable', 409)
        return [rows[ident] for ident in ids]

    def _validate(self, rows, action):
        selected = {row['id'] for row in rows}
        for row in rows:
            if row['status'] != 'retained':
                raise AppError('所选记录已处理，请刷新后重新选择。', 'recovery_entry_changed', 409)
            if action == 'restore':
                reason = self._current_reason(row) or self._ancestor_reason(row, selected)
                if reason: raise AppError(reason, 'recovery_restore_unavailable', 409)

    def prepare(self, ids, action='restore'):
        if action not in ('restore', 'purge'):
            raise AppError('请选择还原或永久删除。', 'invalid_recovery_action')
        with self.state.lock, self.state.db:
            rows = self._selected(ids)
            self._validate(rows, action)
            at = datetime.now(timezone.utc)
            plan_id, confirmation = secrets.token_hex(12), secrets.token_hex(16)
            items = [{key: entry.get(key) for key in ('id', 'name', 'original_path', 'kind', 'size_bytes', 'size_known', 'status', 'revision', 'assessment')}
                     for entry in (self._row(row) for row in rows)]
            warnings = ['当前仅记录模拟结果，不移动或删除真实文件，实际释放空间为0。']
            if action == 'purge': warnings.append('永久删除后，撤销操作或重置模拟记录都不能还原这些内容。')
            plan = dict(id=plan_id, action=action, confirmation=confirmation, count=len(rows),
                        estimated_bytes=sum(row['size_bytes'] or 0 for row in rows),
                        unknown_size_count=sum(row['size_bytes'] is None for row in rows), warnings=warnings,
                        items=items, revision=self.revision(), status='awaiting_review', mode='simulation',
                        created_at=at.isoformat(), expires_at=(at + timedelta(minutes=15)).isoformat(),
                        actual_reclaimed_bytes=0)
            self.state.db.execute('INSERT INTO recovery_plans VALUES(?,?,?,?,?,?,NULL)',
                (plan_id, action, 'awaiting_review', confirmation, plan['expires_at'], json.dumps(plan, ensure_ascii=False)))
            return copy.deepcopy(plan)

    def get_plan(self, plan_id):
        if not isinstance(plan_id, str): raise AppError('计划编号无效。', 'invalid_recovery_plan')
        with self.state.lock:
            row = self.state.db.execute('SELECT * FROM recovery_plans WHERE id=?', (plan_id,)).fetchone()
            if not row: raise AppError('这个已删除区计划不存在。', 'unknown_recovery_plan', 404)
            value = json.loads(row['payload'])
            value['status'] = row['status']
            if row['status'] == 'awaiting_review' and row['expires_at'] < _now(): value['status'] = 'expired'
            if row['result']: value['result'] = json.loads(row['result'])
            return value

    def _change_entry(self, row, status):
        payload = json.loads(row['payload'])
        payload['storage']['journal_state'] = 'simulated_' + status
        self.state.db.execute('UPDATE recovery_entries SET status=?,revision=revision+1,updated_at=?,payload=? WHERE id=?',
                              (status, _now(), json.dumps(payload, ensure_ascii=False), row['id']))
        if status == 'restored':
            self.state.db.execute('UPDATE overlay SET active=0 WHERE batch_id=? AND item_id=? AND signature=?',
                                  (row['batch_id'], row['item_id'], row['signature']))

    def _sync_batches(self, batch_ids):
        for batch_id in set(batch_ids):
            batch = self.state.db.execute('SELECT payload FROM batches WHERE id=?', (batch_id,)).fetchone()
            if not batch: continue
            counts = {status: 0 for status in ('retained', 'restored', 'purged')}
            for row in self.state.db.execute('SELECT status,COUNT(*) FROM recovery_entries WHERE batch_id=? GROUP BY status', (batch_id,)):
                counts[row[0]] = row[1]
            active = self.state.db.execute('SELECT COUNT(*) FROM overlay WHERE batch_id=? AND active=1', (batch_id,)).fetchone()[0]
            status = ('undone' if not active and not counts['retained'] and not counts['purged'] else
                      'partially_undone' if counts['restored'] or counts['purged'] else 'simulated')
            payload = json.loads(batch[0])
            payload['recovery_counts'] = counts
            self.state.db.execute('UPDATE batches SET status=?,payload=? WHERE id=?', (status, json.dumps(payload, ensure_ascii=False), batch_id))

    def execute(self, plan_id, confirmation):
        with self.state.lock:
            plan = self.get_plan(plan_id)
            if (type(confirmation) is not str or len(confirmation) != 32 or not confirmation.isascii()
                    or not hmac.compare_digest(confirmation, plan['confirmation'])):
                raise AppError('请先核对并确认这份计划。', 'recovery_confirmation_required')
            if plan['status'] == 'completed': return copy.deepcopy(plan['result'])
            if plan['status'] != 'awaiting_review': raise AppError('计划已失效，请重新预览。', 'stale_recovery_plan', 409)
            rows = self._selected([item['id'] for item in plan['items']])
            frozen = {item['id']: item for item in plan['items']}
            if any(row['revision'] != frozen[row['id']]['revision'] for row in rows):
                raise AppError('已删除记录已改变，请重新预览。', 'recovery_entry_changed', 409)
            self._validate(rows, plan['action'])
            with self.state.db:
                target = 'restored' if plan['action'] == 'restore' else 'purged'
                for row in rows: self._change_entry(row, target)
                self._sync_batches(row['batch_id'] for row in rows)
                revision = self._bump()
                result = dict(plan_id=plan_id, action=plan['action'], count=len(rows), status='completed', mode='simulation',
                              actual_reclaimed_bytes=0, revision=revision,
                              affected_batch_ids=sorted({row['batch_id'] for row in rows}),
                              items=[dict(id=row['id'], batch_id=row['batch_id'], original_path=row['original_path'], name=json.loads(row['payload'])['name'], status=target) for row in rows])
                self.state.db.execute("UPDATE recovery_plans SET status='completed',result=? WHERE id=?", (json.dumps(result, ensure_ascii=False), plan_id))
            self.state.revision += 1
            self.state.plans.clear()
            return result

    def undo_in_transaction(self, batch_id=None):
        """Undo only currently valid retained entries; purged entries stay hidden."""
        args = [] if batch_id is None else [batch_id]
        rows = self.state.db.execute("SELECT * FROM recovery_entries WHERE status='retained'" + (' AND batch_id=?' if batch_id else ''), args).fetchall()
        valid = [row for row in rows if not self._current_reason(row)]
        while True:
            selected = {row['id'] for row in valid}
            remaining = [row for row in valid if not self._ancestor_reason(row, selected)]
            if len(remaining) == len(valid): break
            valid = remaining
        for row in valid: self._change_entry(row, 'restored')
        if valid:
            self._sync_batches(row['batch_id'] for row in valid)
            self._bump()

    def permanent_signatures(self):
        revision = self.revision()
        if self._permanent_revision != revision:
            self._permanent = {(row[0], row[1]) for row in self.state.db.execute("SELECT item_id,signature FROM recovery_entries WHERE status='purged'")}
            self._permanent_revision = revision
        return self._permanent
