"""Cooperative controls between simulation commits, never a live executor."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import threading

from .simulation import AppError


TERMINAL = {'succeeded', 'failed', 'unverified', 'cancelled'}
FINISHED = {'completed', 'cancelled', 'interrupted'}


class ExecutionControl:
    def __init__(self, task_id, plan_version, entries):
        self._condition = threading.Condition(threading.RLock())
        self.task_id, self.plan_version = task_id, plan_version
        self._items = {entry['id']: dict(id=entry['id'], name=entry['name'], action=entry['action'],
                                         status='pending', verified=False, message='等待处理。') for entry in entries}
        self._status, self._current, self._committing = 'running', None, False
        self._pause, self._cancel, self._revision = False, False, 0
        self._touch()

    def _touch(self):
        self._revision += 1
        self._updated_at = datetime.now(timezone.utc).isoformat()

    def _snapshot(self, include_items=True):
        counts = {name: sum(item['status'] == name for item in self._items.values())
                  for name in ('pending', 'running', *TERMINAL)}
        processed = sum(counts[name] for name in TERMINAL)
        active = self._status not in FINISHED
        value = dict(task_id=self.task_id, plan_version=self.plan_version, status=self._status,
                     revision=self._revision, updated_at=self._updated_at,
                     total=len(self._items), processed=processed,
                     succeeded=counts['succeeded'], failed=counts['failed'], unverified=counts['unverified'],
                     cancelled=counts['cancelled'], pending=counts['pending'],
                     current=copy.deepcopy(self._items.get(self._current)) if self._current else None,
                     can_pause=active and not self._pause and not self._cancel,
                     can_resume=active and self._pause and not self._cancel,
                     can_cancel=active and not self._cancel, actual_reclaimed_bytes=0, backend='simulation')
        if include_items:
            value['items'] = copy.deepcopy(list(self._items.values()))
        return value

    def snapshot(self, *, include_items=True):
        with self._condition:
            return self._snapshot(include_items)

    def request(self, action):
        with self._condition:
            if action not in ('pause', 'resume', 'cancel'):
                raise AppError('请选择暂停、恢复或取消。', 'invalid_execution_control')
            if self._status in FINISHED:
                if action == 'cancel':
                    return self._snapshot()
                raise AppError('这一批已经结束，请查看结果。', 'execution_finished', 409)
            if action == 'cancel':
                self._cancel, self._pause, self._status = True, False, 'cancelling'
            elif self._cancel:
                raise AppError('正在取消，不能恢复本批。', 'execution_cancelling', 409)
            elif action == 'pause':
                self._pause, self._status = True, 'pausing'
            else:
                self._pause, self._status = False, 'running'
            self._touch()
            self._condition.notify_all()
            return self._snapshot()

    def wait_ready(self, cancel=None, progress=None):
        """May wait only outside application/state/settings locks."""
        while True:
            event = None
            with self._condition:
                if cancel is not None and cancel.is_set() and not self._cancel:
                    self._cancel, self._pause, self._status = True, False, 'cancelling'
                    self._touch()
                if self._cancel:
                    return False
                if not self._pause:
                    return True
                if self._status != 'paused':
                    self._status = 'paused'
                    if self._current and not self._committing:
                        self._items[self._current].update(status='pending', message='已暂停，尚未提交。')
                        self._current = None
                    self._touch()
                    event = self._snapshot()
            if event is not None and progress:
                progress({'message': '已暂停，尚未提交的项目正在等待。', 'execution': event})
            with self._condition:
                if self._pause and not self._cancel:
                    # The condition is independent of state/settings. Polling
                    # also observes an older caller's plain cancellation Event.
                    self._condition.wait(.2)

    def begin(self, entry):
        with self._condition:
            row = self._items[entry['id']]
            if row['status'] not in TERMINAL:
                row.update(status='running', message='正在核对并处理。')
                self._current, self._committing = entry['id'], False
                if not self._pause and not self._cancel:
                    self._status = 'running'
                self._touch()

    def enter_commit(self, entry, cancel=None):
        """A short gate under the application's final commit locks; never waits.

        A pause/cancel after this gate may let the current atomic commit finish.
        Every following entry still has to pass wait_ready and this gate.
        """
        with self._condition:
            if cancel is not None and cancel.is_set():
                self._cancel, self._pause, self._status = True, False, 'cancelling'
                self._touch()
            if self._pause or self._cancel:
                return False
            self._current, self._committing = entry['id'], True
            self._items[entry['id']].update(status='running', message='正在提交模拟记录。')
            self._touch()
            return True

    def finished_item(self, entry, status, data=None):
        data = data or {}
        with self._condition:
            row = self._items[entry['id']]
            row.update(status=status if status in TERMINAL else 'unverified',
                       message=data.get('message') or '尚未确认结果。', verified=bool(data.get('verified')))
            if self._current == entry['id']:
                self._current, self._committing = None, False
            self._touch()

    def finish(self, result, *, interrupted=False):
        with self._condition:
            for row in result.get('items', []):
                if row['id'] in self._items:
                    self._items[row['id']].update({key: row[key] for key in ('status', 'verified', 'message') if key in row})
                    if interrupted and row['status'] == 'running':
                        self._items[row['id']].update(status='unverified', verified=False,
                            message='上次提交被中断，尚未确认；不会自动重放。')
            self._current, self._committing = None, False
            self._status = ('interrupted' if interrupted else 'cancelled'
                            if any(row['status'] == 'cancelled' for row in self._items.values()) else 'completed')
            self._touch()
            self._condition.notify_all()
            return self._snapshot()
