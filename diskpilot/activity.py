"""Small, durable tool-event journal for later model context.

Only registry labels, fixed event states, opaque source references and numeric
outcomes are stored. This is deliberately not an execution adapter or trace log.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re

from .simulation import AppError


STATUSES = frozenset(('opened', 'accepted', 'declined', 'answered', 'cancelled',
                      'completed', 'interrupted', 'undone'))
_SOURCE_FIELDS = ('task_id', 'item_id', 'suggestion_id', 'option_id', 'plan_id')
_COUNTS = ('total', 'succeeded', 'failed', 'cancelled', 'unverified', 'pending', 'undone')
_PUBLIC_OPTIONS = frozenset(('查看清理方案', '暂不清理', '暂缓', '保留', '使用专项工具', '打开工具'))


class ToolActivity:
    def __init__(self, state):
        self.state = state
        with state.lock, state.db:
            state.db.execute('''CREATE TABLE IF NOT EXISTS tool_activity (
                id TEXT PRIMARY KEY, event_key TEXT NOT NULL UNIQUE,
                at TEXT NOT NULL, tool_id TEXT NOT NULL, status TEXT NOT NULL,
                payload TEXT NOT NULL)''')

    @staticmethod
    def _counts(result):
        if not isinstance(result, dict):
            return {}
        counts = {}
        for name in _COUNTS:
            value = result.get(name)
            if name == 'total' and value is None:
                value = next((result.get(key) for key in ('item_count', 'count')
                              if type(result.get(key)) is int), None)
            if type(value) is int and 0 <= value <= 1_000_000_000:
                counts[name] = value
        return counts

    @staticmethod
    def _source(value):
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise AppError('工具活动来源格式无效。', 'invalid_activity_source')
        return {key: value[key] for key in _SOURCE_FIELDS if isinstance(value.get(key), str)
                and re.fullmatch(r'[A-Za-z0-9_-]{1,160}', value[key])}

    def record(self, event_key, tool_id, title, status, *, source=None, parameters=None,
               result=None, option_label=None, occurred_at=None):
        """Record one event once; retrying never runs a tool or changes its receipt.

        event_key identifies a single transition (for example plan-ID:completed),
        not a whole plan. Raw event keys and parameters are intentionally omitted.
        Supplied titles/options are never trusted as free-form model context.
        """
        from .cleanup_tools import REGISTRY

        if not isinstance(event_key, str) or not 1 <= len(event_key) <= 512:
            raise AppError('工具活动标识无效。', 'invalid_activity_key')
        if not isinstance(tool_id, str) or tool_id not in REGISTRY:
            raise AppError('工具活动引用了未知工具。', 'invalid_activity_tool')
        if not isinstance(title, str) or not 1 <= len(title) <= 240:
            raise AppError('工具活动标题无效。', 'invalid_activity_title')
        if not isinstance(status, str) or status not in STATUSES:
            raise AppError('工具活动状态无效。', 'invalid_activity_status')
        digest = hashlib.sha256(event_key.encode('utf-8')).hexdigest()
        ident = 'activity-' + digest[:24]
        # A user opening an actual program is still only an opened event.
        # In this build only mutation receipts use the simulation backend.
        backend = 'simulation' if status in ('completed', 'interrupted', 'undone') else 'host'
        if occurred_at is not None:
            try:
                stamp = datetime.fromisoformat(occurred_at)
                if not stamp.tzinfo:
                    raise ValueError()
                stamp = stamp.astimezone(timezone.utc).isoformat()
            except (TypeError, ValueError, OverflowError):
                raise AppError('工具活动时间无效。', 'invalid_activity_time') from None
        else:
            stamp = datetime.now(timezone.utc).isoformat()
        entry = dict(id=ident, time=stamp, tool_id=tool_id,
                     title=REGISTRY[tool_id]['title'], status=status, backend=backend,
                     simulation_only=True, applied=False, actual_reclaimed_bytes=0)
        counts = self._counts(result)
        if counts:
            entry['counts'] = counts
        if isinstance(option_label, str) and option_label in _PUBLIC_OPTIONS:
            entry['option_label'] = option_label
        safe_source = self._source(source)
        # Only opaque linkage is retained; no paths or display text enter it.
        stored = dict(entry, source=safe_source) if safe_source else entry
        with self.state.lock, self.state.db:
            old = self.state.db.execute('SELECT tool_id,status,payload FROM tool_activity WHERE event_key=?',
                                        (digest,)).fetchone()
            if old:
                if old['tool_id'] != tool_id or old['status'] != status:
                    raise AppError('此活动标识已经用于其他状态，请使用新的事件标识。', 'activity_event_conflict', 409)
                return self._public(json.loads(old['payload']))
            self.state.db.execute('INSERT INTO tool_activity VALUES(?,?,?,?,?,?)',
                (ident, digest, entry['time'], tool_id, status, json.dumps(stored, ensure_ascii=False)))
        return self._public(stored)

    @staticmethod
    def _public(value):
        entry = {key: value[key] for key in ('id', 'time', 'tool_id', 'title', 'status', 'backend',
                'simulation_only', 'applied', 'actual_reclaimed_bytes', 'counts', 'option_label') if key in value}
        source = ToolActivity._source(value.get('source'))
        if source:
            entry['source'] = source
        return entry

    def recent(self, limit=6, *, task_id=None, suggestion_id=None, statuses=None):
        if type(limit) is not int or not 1 <= limit <= 20:
            raise AppError('每次可查看1至20条工具活动。', 'invalid_activity_limit')
        clauses, parameters = [], []
        for field, value in (('task_id', task_id), ('suggestion_id', suggestion_id)):
            if value is None:
                continue
            if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', value):
                raise AppError('工具活动来源筛选无效。', 'invalid_activity_filter')
            clauses.append("json_extract(payload,'$.source." + field + "')=?")
            parameters.append(value)
        if statuses is not None:
            if (not isinstance(statuses, (list, tuple)) or not 1 <= len(statuses) <= len(STATUSES)
                    or any(not isinstance(value, str) or value not in STATUSES for value in statuses)
                    or len(set(statuses)) != len(statuses)):
                raise AppError('工具活动状态筛选无效。', 'invalid_activity_filter')
            clauses.append('status IN (' + ','.join('?' for _ in statuses) + ')')
            parameters.extend(statuses)
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        with self.state.lock:
            rows = self.state.db.execute('SELECT payload FROM tool_activity' + where + ' ORDER BY at DESC,rowid DESC LIMIT ?',
                                         [*parameters, limit]).fetchall()
            return [self._public(json.loads(row['payload'])) for row in rows]
