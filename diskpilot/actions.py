"""Shared action lifecycle and durable attempts; only a simulation backend exists."""
from __future__ import annotations

import hashlib
from contextlib import nullcontext
import json
from datetime import datetime, timezone
import threading

from .simulation import AppError
from .windows import validate_system_items


def now():
    return datetime.now(timezone.utc).isoformat()


class SimulationBackend:
    name = 'simulation'

    def __init__(self, state, *, validator=validate_system_items):
        self.state, self.validator = state, validator

    def apply(self, entry, operation_id, patterns):
        receipt = self.state.receipt(operation_id)
        if receipt:
            return receipt
        current = next((x for x in self.state.view(patterns)['items'] if x['id'] == entry['id']), None)
        if not current or current['signature'] != entry['signature']:
            raise AppError('内容在你确认前后有了变化，请重新检查。', 'target_changed', 409)
        plan = self.state.prepare([entry['id']], patterns, entry['action'])
        return self.state.execute(plan['id'], patterns, self.validator, operation_id=operation_id)['batch']

    def verify(self, entry, batch):
        with self.state.lock:
            row = self.state.db.execute('SELECT signature,action,active FROM overlay WHERE batch_id=? AND item_id=?',
                                        (batch['id'], entry['id'])).fetchone()
            return bool(batch['status'] == 'simulated' and row and row['active'] == 1
                        and row['signature'] == entry['signature'] and row['action'] == entry['action'])


class ActionRuntime:
    """Intent is durable before apply; receipt and virtual changes commit together.

    A future live backend must supply its own apply/verify/recovery semantics.
    The test-only fault hook cannot be configured via a product API.
    """
    def __init__(self, state, *, backend=None, fault=None):
        self.state = state
        self.backend = backend or SimulationBackend(state)
        self.fault = fault
        with state.lock, state.db:
            state.db.execute('''CREATE TABLE IF NOT EXISTS action_attempts (
                id TEXT PRIMARY KEY, task_id TEXT, plan_version INTEGER,
                status TEXT, created_at TEXT, updated_at TEXT, payload TEXT)''')

    def _save(self, operation_id, task_id, version, status, data):
        with self.state.lock, self.state.db:
            self.state.db.execute('''INSERT INTO action_attempts VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET status=excluded.status,
                updated_at=excluded.updated_at,payload=excluded.payload''',
                (operation_id, task_id, version, status, now(), now(), json.dumps(data, ensure_ascii=False)))

    def recover(self):
        """Resolve interrupted simulation attempts from atomic receipts, never replay."""
        with self.state.lock:
            rows = self.state.db.execute("SELECT * FROM action_attempts WHERE status='running'").fetchall()
        for row in rows:
            data = json.loads(row['payload'])
            batch = self.state.receipt(row['id'])
            verified = bool(batch and self.backend.verify(data['entry'], batch))
            data.update(batch_id=batch['id'] if batch else None, verified=verified,
                        message='已从处理记录核实上次结果。' if verified else '上次处理被中断，未确认完成；不会自动重复。')
            self._save(row['id'], row['task_id'], row['plan_version'], 'succeeded' if verified else 'unverified', data)

    def run(self, task_id, version, entries, *, patterns=(), cancel=None, progress=None, guard=None, apply_context=None):
        cancel = cancel or threading.Event()
        for entry in entries:
            operation_id = hashlib.sha256(f'{task_id}:{version}:{entry["id"]}'.encode()).hexdigest()
            with self.state.lock:
                previous = self.state.db.execute('SELECT status FROM action_attempts WHERE id=?', (operation_id,)).fetchone()
            if previous:
                continue  # Failed/unverified attempts need a new reviewed plan, not a blind retry.
            data = {'entry': entry, 'backend': self.backend.name, 'verified': False,
                    'actual_reclaimed_bytes': 0, 'recovery': '撤销此工具中的处理记录'}
            if cancel.is_set():
                data['message'] = '你已停止处理，这项没有继续。'
                self._save(operation_id, task_id, version, 'cancelled', data)
                continue
            self._save(operation_id, task_id, version, 'running', data)
            if progress:
                progress({'message': '正在处理并核对：' + entry['name']})
            try:
                injected = self.fault(entry, 'before') if self.fault else None
                if injected:
                    raise AppError('测试场景：这项暂时无法处理。', 'simulated_failure')
                with self.state.lock, apply_context(entry) if apply_context else nullcontext(patterns) as current_patterns:
                    if guard:
                        guard(entry)
                    batch = self.backend.apply(entry, operation_id, current_patterns)
                data['batch_id'] = batch['id']
                injected = self.fault(entry, 'after') if self.fault else None
                verified = not injected and self.backend.verify(entry, batch)
                data.update(verified=verified, message='已核对处理结果。' if verified else '已尝试处理，但结果尚未确认。')
                self._save(operation_id, task_id, version, 'succeeded' if verified else 'unverified', data)
            except Exception as exc:
                data['message'] = str(exc) if isinstance(exc, AppError) else '这项没有处理完成，可以重新检查后再试。'
                self._save(operation_id, task_id, version, 'failed', data)
        return self.result(task_id, version)

    def result(self, task_id, version):
        with self.state.lock:
            rows = self.state.db.execute('SELECT * FROM action_attempts WHERE task_id=? AND plan_version=? ORDER BY rowid',
                                         (task_id, version)).fetchall()
        items = []
        for row in rows:
            data = json.loads(row['payload'])
            with self.state.lock:
                batch = self.state.db.execute('SELECT status FROM batches WHERE id=?', (data.get('batch_id'),)).fetchone()
            items.append(dict(id=data['entry']['id'], name=data['entry']['name'], status=row['status'],
                              message=data.get('message', ''), verified=data['verified'], backend=data['backend'],
                              batch_id=data.get('batch_id'), action=data['entry']['action'],
                              current_state='undone' if batch and batch['status'] == 'undone' else 'recorded'))
        succeeded = sum(x['status'] == 'succeeded' for x in items)
        status = 'succeeded' if items and succeeded == len(items) else 'partial' if succeeded else 'failed'
        if not succeeded and any(x['status'] in ('running', 'unverified') for x in items):
            status = 'unverified'
        if items and all(x['status'] == 'cancelled' for x in items):
            status = 'cancelled'
        return dict(plan_version=version, backend=self.backend.name, status=status, items=items,
                    succeeded=succeeded, total=len(items), actual_reclaimed_bytes=0)
