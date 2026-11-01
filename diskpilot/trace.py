"""Opt-in, bounded local protocol journal. Never stores auth or opaque reasoning."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import threading

from .simulation import AppError

MAX_ENTRIES, MAX_BYTES, MAX_EVENT_BYTES = 200, 32 * 1024 * 1024, 1024 * 1024
OMIT = {'api_key', 'authorization', 'proxy-authorization', 'password', 'secret', 'access_token',
        'refresh_token', 'reasoning_content', 'thinking', 'redacted_thinking', 'encrypted_content',
        '_continuation', 'thought', 'thought_signature', 'reasoning', 'reasoning_details', 'reasoning_text'}


def timestamp():
    return datetime.now(timezone.utc).isoformat()


class DeveloperTrace:
    def __init__(self, data_dir, cleaner):
        self.lock, self.local, self.cleaner = threading.RLock(), threading.local(), cleaner
        self.db = sqlite3.connect(Path(data_dir) / 'developer_trace.sqlite3', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA auto_vacuum=INCREMENTAL')
        self.db.executescript('CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, enabled INTEGER, started TEXT);'
                              'INSERT OR IGNORE INTO settings VALUES(1,0,NULL);'
                              'CREATE TABLE IF NOT EXISTS entries (seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, task_id TEXT, kind TEXT, payload TEXT, bytes INTEGER);')
        self.db.commit()

    def sanitize(self, value):
        if isinstance(value, dict):
            return {str(k): '[未记录：密钥或协议内部推理]' if str(k).casefold() in OMIT and not (
                        isinstance(v, dict) and ((str(k).casefold() == 'thinking' and set(v) <= {'type', 'budget_tokens', 'clear_thinking'})
                        or (str(k).casefold() == 'reasoning' and set(v) <= {'effort', 'summary'}))) else self.sanitize(v)
                    for k, v in value.items()}
        if isinstance(value, list):
            return [self.sanitize(v) for v in value]
        if isinstance(value, str):
            return self.cleaner(value)
        if value is None or type(value) in (bool, int, float):
            return value
        return '[不支持的记录类型]'

    @contextmanager
    def context(self, task_id):
        previous = getattr(self.local, 'task_id', None)
        self.local.task_id = task_id
        try:
            yield
        finally:
            self.local.task_id = previous

    def current_task_id(self):
        """Task association for transient public stream hooks, per worker thread."""
        return getattr(self.local, 'task_id', None)

    def record(self, event):
        # Mode is checked at insertion time, including responses after disabling.
        with self.lock:
            if not self.db.execute('SELECT enabled FROM settings WHERE id=1').fetchone()[0]:
                return
            safe = self.sanitize({k: v for k, v in event.items() if k != 'kind'})
            raw = json.dumps(safe, ensure_ascii=False)
            encoded = raw.encode('utf-8')
            if len(encoded) > MAX_EVENT_BYTES:
                raw = json.dumps({'truncated': True, 'original_bytes': len(encoded),
                                  'sha256': hashlib.sha256(encoded).hexdigest(),
                                  'preview': encoded[:MAX_EVENT_BYTES // 2].decode('utf-8', errors='ignore')}, ensure_ascii=False)
            with self.db:
                self.db.execute('INSERT INTO entries(at,task_id,kind,payload,bytes) VALUES(?,?,?,?,?)',
                    (timestamp(), getattr(self.local, 'task_id', None), str(event.get('kind', 'event'))[:80], raw, len(raw.encode('utf-8'))))
                self.db.execute('DELETE FROM entries WHERE seq NOT IN (SELECT seq FROM entries ORDER BY seq DESC LIMIT ?)', (MAX_ENTRIES,))
                while self.db.execute('SELECT COALESCE(SUM(bytes),0) FROM entries').fetchone()[0] > MAX_BYTES:
                    self.db.execute('DELETE FROM entries WHERE seq=(SELECT MIN(seq) FROM entries)')
            self.db.execute('PRAGMA incremental_vacuum(64)')

    def update(self, *, enabled=None, clear=False):
        if enabled is not None and type(enabled) is not bool or type(clear) is not bool:
            raise AppError('开发模式设置格式无效。')
        with self.lock, self.db:
            if clear:
                self.db.execute('DELETE FROM entries')
            if enabled is not None:
                before = bool(self.db.execute('SELECT enabled FROM settings WHERE id=1').fetchone()[0])
                self.db.execute('UPDATE settings SET enabled=?, started=CASE WHEN ? THEN ? ELSE started END WHERE id=1',
                                (int(enabled), int(enabled and not before), timestamp()))
        return self.page()

    def page(self, offset=0, limit=10):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 30:
            raise AppError('请选择有效的开发记录页码。')
        with self.lock:
            setting = self.db.execute('SELECT enabled,started FROM settings WHERE id=1').fetchone()
            total = self.db.execute('SELECT COUNT(*) FROM entries').fetchone()[0]
            rows = self.db.execute('SELECT seq,at,task_id,kind,payload FROM entries ORDER BY seq DESC LIMIT ? OFFSET ?', (limit, offset)).fetchall()
            return dict(enabled=bool(setting['enabled']), capture_started_at=setting['started'],
                        offset=offset, limit=limit, total=total, has_more=offset + limit < total,
                        entries=[dict(seq=r['seq'], at=r['at'], task_id=r['task_id'], kind=r['kind'], payload=json.loads(r['payload'])) for r in rows],
                        retention=dict(max_entries=MAX_ENTRIES, max_bytes=MAX_BYTES, max_event_bytes=MAX_EVENT_BYTES),
                        note='仅记录开启后的请求、回复和工具往返；密钥与协议内部推理不记录，超限会明确标注。')

    def close(self):
        with self.lock:
            self.db.close()
