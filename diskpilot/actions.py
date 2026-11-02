"""Shared action lifecycle and durable attempts; only a simulation backend exists."""
from __future__ import annotations

import hashlib
from collections import Counter
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

    def accepts(self, entry):
        return entry.get('kind') != 'cleanup_tool'

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

    def entry_state(self, entry, batch_id):
        """Read the current effect of this exact receipt, not its whole batch.

        A batch can contain restored and permanently purged siblings. Historical
        success remains immutable; recovery/overlay records describe today.
        """
        if not batch_id:
            return dict(current_state='unverified')
        with self.state.lock:
            retained = self.state.db.execute('''SELECT id,status FROM recovery_entries
                WHERE batch_id=? AND item_id=? AND signature=?''',
                (batch_id, entry['id'], entry['signature'])).fetchone()
            overlay = self.state.db.execute('''SELECT signature,action,active FROM overlay
                WHERE batch_id=? AND item_id=?''', (batch_id, entry['id'])).fetchone()
            recovery = dict(recovery_entry_id=retained['id'], recovery_status=retained['status']) if retained else {}
            if retained and retained['status'] == 'purged':
                return dict(current_state='purged', **recovery)
            if retained and retained['status'] == 'restored':
                return dict(current_state='undone', **recovery)
            if overlay and overlay['signature'] == entry['signature'] and overlay['action'] == entry['action']:
                return dict(current_state='recorded' if overlay['active'] else 'undone', **recovery)
            # For pre-journal operations only an explicit undone batch is known.
            batch = self.state.db.execute('SELECT status FROM batches WHERE id=?', (batch_id,)).fetchone()
            return dict(current_state='undone' if batch and batch['status'] == 'undone' else 'unverified', **recovery)


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
            if hasattr(self.backend, 'accepts') and not self.backend.accepts(data['entry']):
                continue
            batch = (self.backend.receipt(row['id']) if hasattr(self.backend, 'receipt')
                     else self.state.receipt(row['id']))
            verified = bool(batch and self.backend.verify(data['entry'], batch))
            data.update(batch_id=batch['id'] if batch else None, verified=verified,
                        message='已从处理记录核实上次结果。' if verified else '上次处理被中断，未确认完成；不会自动重复。')
            self._save(row['id'], row['task_id'], row['plan_version'], 'succeeded' if verified else 'unverified', data)

    def run(self, task_id, version, entries, *, patterns=(), cancel=None, progress=None, guard=None, apply_context=None, control=None):
        cancel = cancel or threading.Event()

        def emit(message):
            if progress:
                payload = {'message': message}
                if control:
                    payload['execution'] = control.snapshot()
                progress(payload)

        for entry in entries:
            operation_id = hashlib.sha256(f'{task_id}:{version}:{entry["id"]}'.encode()).hexdigest()
            with self.state.lock:
                previous = self.state.db.execute('SELECT status,payload FROM action_attempts WHERE id=?', (operation_id,)).fetchone()
            if previous:
                if control:
                    control.finished_item(entry, previous['status'], json.loads(previous['payload']))
                continue  # Reviewed retries never blindly reapply a durable attempt.
            data = {'entry': entry, 'backend': self.backend.name, 'verified': False,
                    'actual_reclaimed_bytes': 0, 'recovery': '撤销此工具中的处理记录'}
            ready = control.wait_ready(cancel, progress) if control else not cancel.is_set()
            if not ready:
                data['message'] = '你已取消，这项没有提交。'
                self._save(operation_id, task_id, version, 'cancelled', data)
                if control:
                    control.finished_item(entry, 'cancelled', data)
                emit(data['message'])
                continue
            if control:
                control.begin(entry)
            self._save(operation_id, task_id, version, 'running', data)
            emit('正在处理并核对：' + entry['name'])
            try:
                injected = self.fault(entry, 'before') if self.fault else None
                if injected:
                    raise AppError('测试场景：这项暂时无法处理。', 'simulated_failure')
                cancelled_before_commit = False
                while True:
                    # A pause may arrive while preparing the entry. Wait outside
                    # both commit locks, then recheck the request atomically.
                    ready = control.wait_ready(cancel, progress) if control else not cancel.is_set()
                    if not ready:
                        cancelled_before_commit = True
                        break
                    if control:
                        control.begin(entry)
                    with self.state.lock, apply_context(entry) if apply_context else nullcontext(patterns) as current_patterns:
                        if cancel.is_set():
                            cancelled_before_commit = True
                            break
                        if control and not control.enter_commit(entry, cancel):
                            continue
                        if guard:
                            guard(entry)
                        batch = self.backend.apply(entry, operation_id, current_patterns)
                        break
                if cancelled_before_commit:
                    data['message'] = '你已取消，这项没有提交。'
                    status = 'cancelled'
                else:
                    data['batch_id'] = batch['id']
                    injected = self.fault(entry, 'after') if self.fault else None
                    verified = not injected and self.backend.verify(entry, batch)
                    data.update(verified=verified, message='操作结果已记录并核验。' if verified else '已尝试记录操作，但结果尚未确认。')
                    status = 'succeeded' if verified else 'unverified'
                self._save(operation_id, task_id, version, status, data)
            except Exception as exc:
                data['message'] = str(exc) if isinstance(exc, AppError) else '这项没有处理完成，可以重新检查后再试。'
                status = 'failed'
                self._save(operation_id, task_id, version, status, data)
            except BaseException:
                if control:
                    control.finish(self.result(task_id, version), interrupted=True)
                    emit('处理被中断，已提交记录保留，未执行项不会自动继续。')
                raise
            if control:
                control.finished_item(entry, status, data)
            emit(data['message'])
        result = self.result(task_id, version)
        if control:
            control.finish(result)
            emit('这一批已结束，操作记录已核对。')
        return result

    def result(self, task_id, version):
        with self.state.lock:
            rows = self.state.db.execute('SELECT * FROM action_attempts WHERE task_id=? AND plan_version=? ORDER BY rowid',
                                         (task_id, version)).fetchall()
        items = []
        for row in rows:
            data = json.loads(row['payload'])
            current = {}
            if hasattr(self.backend, 'entry_state'):
                current = self.backend.entry_state(data['entry'], data.get('batch_id'))
                current_state = current['current_state']
            elif hasattr(self.backend, 'current_state'):
                current_state = self.backend.current_state(data.get('batch_id'))
            else:
                with self.state.lock:
                    batch = self.state.db.execute('SELECT status FROM batches WHERE id=?', (data.get('batch_id'),)).fetchone()
                current_state = 'undone' if batch and batch['status'] == 'undone' else 'recorded'
            items.append(dict(id=data['entry']['id'], name=data['entry']['name'], status=row['status'],
                              message=data.get('message', ''), verified=data['verified'], backend=data['backend'],
                              batch_id=data.get('batch_id'), action=data['entry']['action'],
                              current_state=current_state,
                              **{key: value for key, value in current.items() if key != 'current_state'}))
        succeeded = sum(x['status'] == 'succeeded' for x in items)
        status = 'succeeded' if items and succeeded == len(items) else 'partial' if succeeded else 'failed'
        if not succeeded and any(x['status'] in ('running', 'unverified') for x in items):
            status = 'unverified'
        if items and all(x['status'] == 'cancelled' for x in items):
            status = 'cancelled'
        action_results = {}
        for item in items:
            group = action_results.setdefault(item['action'], dict(total=0, succeeded=0, failed=0, cancelled=0, unverified=0, undone=0, purged=0))
            group['total'] += 1
            outcome = 'unverified' if item['status'] == 'running' else item['status']
            if outcome in group:
                group[outcome] += 1
            group['undone'] += item['current_state'] == 'undone'
            group['purged'] += item['current_state'] == 'purged'
        return dict(plan_version=version, backend=self.backend.name, status=status, items=items,
                    succeeded=succeeded, total=len(items), actual_reclaimed_bytes=0,
                    failed=sum(x['status'] == 'failed' for x in items),
                    unverified=sum(x['status'] in ('unverified', 'running') for x in items),
                    cancelled=sum(x['status'] == 'cancelled' for x in items),
                    undone=sum(x['current_state'] == 'undone' for x in items),
                    purged=sum(x['current_state'] == 'purged' for x in items),
                    action_results=action_results,
                    actions=dict(Counter(item['action'] for item in items)),
                    undone_actions=dict(Counter(item['action'] for item in items if item['current_state'] == 'undone')),
                    purged_actions=dict(Counter(item['action'] for item in items if item['current_state'] == 'purged')))
