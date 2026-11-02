"""Durable read-only work, owned by one task; workers never call a model."""
from __future__ import annotations

import copy
import hashlib
import json
import secrets
import threading
from datetime import datetime, timezone

from .simulation import AppError


def stamp():
    return datetime.now(timezone.utc).isoformat()


class TaskJobs:
    def __init__(self, state):
        self.state = state
        with state.lock, state.db:
            state.db.execute('CREATE TABLE IF NOT EXISTS task_work (id TEXT PRIMARY KEY,task_id TEXT,request_hash TEXT,payload TEXT)')
            if not hasattr(state, '_task_work_runtime'):
                state._task_work_runtime = (threading.RLock(), {}, threading.BoundedSemaphore(2))
            self.lock, self.controls, self.slots = state._task_work_runtime
            for row in state.db.execute('SELECT id,payload FROM task_work').fetchall():
                value = json.loads(row['payload'])
                if value['status'] in ('queued', 'running', 'cancelling') and row['id'] not in self.controls:
                    value.update(status='interrupted', updated_at=stamp(), error='上次只读工作中断，已有事实保留；不会自动重做。')
                    self._save(value)

    def _save(self, value):
        with self.state.lock, self.state.db:
            self.state.db.execute('INSERT OR REPLACE INTO task_work VALUES(?,?,?,?)',
                (value['id'], value['task_id'], value['request_hash'], json.dumps(value, ensure_ascii=False)))

    def _get(self, task_id, job_id):
        with self.state.lock:
            row = self.state.db.execute('SELECT payload FROM task_work WHERE id=? AND task_id=?', (job_id, task_id)).fetchone()
            if row is None:
                raise AppError('此工作不属于当前任务。', 'outside_scope', 404)
            return json.loads(row['payload'])

    @staticmethod
    def _public(value):
        result = {k: copy.deepcopy(value.get(k)) for k in ('id', 'task_id', 'kind', 'status', 'created_at', 'updated_at', 'progress', 'error')}
        data = value.get('result') or {}
        result.update(read_only=True, can_cancel=value['status'] in ('queued', 'running'),
                      summary=copy.deepcopy(data.get('summary', data.get('counts', {}))),
                      result_status=data.get('status'), partial=bool(data.get('partial', False)),
                      actual_reclaimed_bytes=0)
        if data.get('snapshot_id'):
            result['snapshot_id'] = data['snapshot_id']
        return result

    def read(self, task_id, job_id):
        return self._public(self._get(task_id, job_id))

    def recent(self, task_id, limit=4):
        with self.state.lock:
            rows = self.state.db.execute('SELECT payload FROM task_work WHERE task_id=? ORDER BY rowid DESC LIMIT ?', (task_id, limit)).fetchall()
            return [self._public(json.loads(row['payload'])) for row in rows]

    def result(self, task_id, job_id):
        value = self._get(task_id, job_id)
        if value['status'] not in ('completed', 'cancelled') or value.get('result') is None:
            raise AppError('工作尚未产生可读结果，请查看当前状态。', 'work_not_ready', 409)
        return value['result']

    def start(self, task_id, kind, request, worker, *, refresh=False):
        digest = hashlib.sha256(json.dumps(dict(kind=kind, request=request), sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with self.lock:
            with self.state.lock:
                old = self.state.db.execute('SELECT payload FROM task_work WHERE task_id=? AND request_hash=? ORDER BY rowid DESC LIMIT 1', (task_id, digest)).fetchone()
            if old and not refresh:
                value = json.loads(old['payload'])
                if value['status'] in ('queued', 'running', 'completed'):
                    return dict(self._public(value), reused=True)
            if not self.slots.acquire(blocking=False):
                raise AppError('已有两个后台检查，请等待或取消后再开始。', 'work_limit', 409)
            ident, cancel = 'work-' + secrets.token_hex(12), threading.Event()
            value = dict(id=ident, task_id=task_id, kind=kind, request_hash=digest, request=copy.deepcopy(request),
                         status='queued', created_at=stamp(), updated_at=stamp(), progress={}, result=None, error=None)
            self.controls[ident] = dict(cancel=cancel, thread=None, finished=threading.Event())
            try:
                self._save(value)
            except Exception:
                self.controls.pop(ident, None)
                self.slots.release()
                raise

            def progress(update):
                with self.state.lock:
                    current = self._get(task_id, ident)
                    if current['status'] not in ('queued', 'running', 'cancelling'):
                        return
                    current.update(progress=copy.deepcopy(update), updated_at=stamp())
                    self._save(current)

            def run():
                try:
                    # Do not race a cancellation persisted between the first
                    # read and the running transition; shared SQL stays locked.
                    with self.state.lock:
                        current = self._get(task_id, ident)
                        current.update(status='cancelling' if cancel.is_set() else 'running', updated_at=stamp())
                        self._save(current)
                    data = worker(cancel, progress)
                    with self.state.lock:
                        current = self._get(task_id, ident)
                        current.update(status='cancelled' if cancel.is_set() or data.get('status') == 'cancelled' else 'completed',
                                       result=data, updated_at=stamp())
                        self._save(current)
                except Exception as exc:
                    current = self._get(task_id, ident)
                    current.update(status='failed', error=str(exc) if isinstance(exc, AppError) else '只读检查未完成：' + type(exc).__name__, updated_at=stamp())
                    self._save(current)
                finally:
                    with self.lock:
                        self.controls[ident]['finished'].set()
                    self.slots.release()

            thread = threading.Thread(target=run, name='panxu-readonly-' + ident, daemon=True)
            self.controls[ident]['thread'] = thread
            try:
                thread.start()
            except Exception:
                self.controls.pop(ident, None)
                value.update(status='failed', error='后台检查未能启动，请重试。', updated_at=stamp())
                try:
                    self._save(value)
                finally:
                    self.slots.release()
                raise
            return self._public(value)

    def cancel(self, task_id, job_id):
        # The worker persists completion under state.lock. Keep this read and
        # transition atomic too, or a stale running snapshot can overwrite its
        # completed result with cancelling after the thread has already exited.
        with self.lock, self.state.lock:
            value = self._get(task_id, job_id)
            if value['status'] in ('queued', 'running', 'cancelling'):
                control = self.controls.get(job_id)
                if control:
                    control['cancel'].set()
                value.update(status='cancelling', updated_at=stamp())
                self._save(value)
            return self._public(value)

    def wait(self, task_id, job_id, *, cancel=None, progress=None):
        while True:
            value = self.read(task_id, job_id)
            if value['status'] not in ('queued', 'running', 'cancelling'):
                return value
            if cancel is not None and cancel.is_set():
                self.cancel(task_id, job_id)
            if progress:
                progress(dict(message=(value.get('progress') or {}).get('message', '后台只读检查正在进行…'), work=value))
            with self.lock:
                control = self.controls.get(job_id)
            if control:
                control['finished'].wait(.2)
            else:
                return value

    def close(self):
        with self.lock:
            controls = list(self.controls.values())
            for control in controls:
                control['cancel'].set()
        for control in controls:
            if control['thread'] is not threading.current_thread():
                control['thread'].join()
