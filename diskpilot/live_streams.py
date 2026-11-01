"""Bounded, volatile public reply previews; never a task or action record.

The model adapter supplies already-sanitized cumulative text. No partial tool
arguments, credentials, private reasoning or provider continuations belong here.
"""
from collections import OrderedDict
import copy
from datetime import datetime, timezone
import secrets
import threading
import time


class LiveStreams:
    def __init__(self, *, max_tasks=16, max_text=8000, max_bytes=512 * 1024, ttl=600):
        if any(type(value) is not int or value <= 0 for value in (max_tasks, max_text, max_bytes)) or ttl <= 0:
            raise ValueError('Stream limits must be positive')
        self.epoch = secrets.token_hex(8)
        self.max_tasks, self.max_text, self.max_bytes, self.ttl = max_tasks, max_text, max_bytes, ttl
        self.condition = threading.Condition()
        self.rows = OrderedDict()
        self.recent_turns = OrderedDict()
        self.sequence = 0
        self.closed = False

    def _trim(self):
        now = time.monotonic()
        for key in list(self.rows):
            if now - self.rows[key][0] > self.ttl:
                del self.rows[key]
        while len(self.rows) > self.max_tasks or sum(len(r[1]['text'].encode('utf-8')) for r in self.rows.values()) > self.max_bytes:
            self.rows.popitem(last=False)

    def record(self, task_id, event):
        if not isinstance(task_id, str) or not 0 < len(task_id) <= 128 or not isinstance(event, dict):
            return
        kind, turn = event.get('kind'), event.get('turn_id')
        if kind not in ('turn_start', 'stream_delta', 'turn_end') or not isinstance(turn, str) or not 0 < len(turn) <= 100:
            return
        if 'text' in event and not isinstance(event['text'], str):
            return
        with self.condition:
            if self.closed:
                return
            self._trim()
            previous = self.rows.get(task_id)
            if kind == 'turn_start':
                key = (task_id, turn)
                if key in self.recent_turns:
                    return
                self.recent_turns[key] = None
                while len(self.recent_turns) > self.max_tasks * 8:
                    self.recent_turns.popitem(last=False)
            if kind != 'turn_start' and (not previous or previous[1]['turn_id'] != turn):
                return  # a late response from a superseded model turn
            if kind != 'turn_start' and previous[1]['status'] != 'streaming':
                return  # terminal snapshots are immutable
            value = dict(epoch=self.epoch, sequence=0, turn_id=turn, protocol=str(event.get('protocol', ''))[:32],
                         text='', tool_pending=False, status='streaming', delivery='', truncated=False) if kind == 'turn_start' else copy.deepcopy(previous[1])
            if 'text' in event:
                if not isinstance(event['text'], str):
                    return
                text = event['text'].encode('utf-8', errors='replace').decode('utf-8')
                value['text'] = text[:self.max_text]
                value['truncated'] = bool(event.get('truncated')) or len(text) > self.max_text
            if type(event.get('tool_pending')) is bool:
                value['tool_pending'] = event['tool_pending']
            if isinstance(event.get('delivery'), str):
                value['delivery'] = event['delivery'][:24]
            if kind == 'turn_end':
                status = event.get('status')
                value['status'] = status if status in ('completed', 'error', 'cancelled') else 'error'
                if isinstance(event.get('code'), str):
                    value['code'] = event['code'][:80]
            self.sequence += 1
            value.update(sequence=self.sequence, updated_at=datetime.now(timezone.utc).isoformat())
            self.rows[task_id] = (time.monotonic(), value)
            self.rows.move_to_end(task_id)
            self._trim()
            self.condition.notify_all()

    def snapshot(self, task_id):
        with self.condition:
            self._trim()
            row = self.rows.get(task_id)
            return copy.deepcopy(row[1]) if row else None

    def wait(self, task_id, after=-1, timeout=1):
        with self.condition:
            row = self.snapshot(task_id)
            if not self.closed and (not row or row['sequence'] == after):
                self.condition.wait(min(max(timeout, 0), 2))
                row = self.snapshot(task_id)
            return row

    def close(self):
        with self.condition:
            self.closed = True
            self.rows.clear()
            self.recent_turns.clear()
            self.condition.notify_all()
