"""Append-only operation log (JSON Lines) for every change PanXu makes."""
from __future__ import annotations

import json
import os
import threading
import time
import uuid

MAX_BYTES = 8 * 1024 * 1024
MAX_ITEMS_PER_ENTRY = 2000


class OperationLog:
    """Changes that take a while are written in three steps, so that a PanXu that is killed or crashes half way
    still leaves a record: ``begin`` (what is about to happen), ``progress`` (every few seconds) and the final
    entry, which ``replaces`` the first one. A begun entry nobody replaced shows as 「没有完成」."""

    PROGRESS_EVERY = 1.0

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._open: set[str] = set()         # begun in this run and not finished yet: 「进行中」
        self._progress_at: dict[str, float] = {}
        os.makedirs(os.path.dirname(path), exist_ok=True)

    def add(self, kind: str, title: str, *, freed: int = 0, items=None, failures=None, details=None,
            revert=None, replaces: str | None = None) -> dict:
        entry = {
            'id': uuid.uuid4().hex[:12],
            'time': time.time(),
            'kind': kind,
            'title': title,
            'freed': int(freed),
            'items': list(items or [])[:MAX_ITEMS_PER_ENTRY],
            'item_count': len(items or []),
            'failures': list(failures or [])[:200],
            'failure_count': len(failures or []),
            'details': details or {},
        }
        if revert is not None:
            entry['revert'] = revert
        if replaces:
            entry['replaces'] = replaces
        self._append(entry, rotate=True)
        if replaces:
            self._open.discard(replaces)
        return entry

    def begin(self, kind: str, title: str, items=None) -> str:
        """Write down a change that is about to start (see the class docstring); returns its id."""
        entry = self.add(kind, title, items=items, details={'pending': True})
        self._open.add(entry['id'])
        self._progress_at[entry['id']] = time.time()  # the first progress line after a second, not a zero at once
        return entry['id']

    def progress(self, entry_id: str | None, **values) -> None:
        """How far a begun change got (written at most once a second)."""
        if not entry_id:
            return
        now = time.time()
        if now - self._progress_at.get(entry_id, 0) < self.PROGRESS_EVERY:
            return
        self._progress_at[entry_id] = now
        self.mark(entry_id, progress=values)

    def drop(self, entry_id: str | None) -> None:
        """A begun change that did nothing after all (cancelled before the first file)."""
        if entry_id:
            self.mark(entry_id, dropped=True)
            self._open.discard(entry_id)

    def mark(self, entry_id: str, **changes) -> None:
        """Record a follow-up (e.g. reverted) as a new line referencing the entry."""
        self._append({'ref': entry_id, 'time': time.time(), **changes})

    def _append(self, row: dict, rotate: bool = False) -> None:
        line = json.dumps(row, ensure_ascii=False) + '\n'
        with self._lock:
            if rotate:
                self._rotate()
            # a line cut off by a crash must not swallow this one
            prefix = '\n' if self._ends_without_newline() else ''
            with open(self.path, 'a', encoding='utf-8') as fh:
                fh.write(prefix + line)
                fh.flush()

    def _ends_without_newline(self) -> bool:
        try:
            with open(self.path, 'rb') as fh:
                fh.seek(-1, os.SEEK_END)
                return fh.read(1) != b'\n'
        except (FileNotFoundError, OSError):  # missing or empty
            return False

    def _rotate(self):
        try:
            if os.path.getsize(self.path) > MAX_BYTES:
                old = self.path + '.1'
                if os.path.exists(old):
                    os.remove(old)
                os.replace(self.path, old)
        except FileNotFoundError:
            pass

    def entries(self, limit: int = 200) -> list[dict]:
        """Newest first, with follow-up marks merged into their entries."""
        rows, marks, replaced = [], {}, set()
        for path in (self.path + '.1', self.path):
            try:
                # errors='replace': a line cut off in the middle of a character is skipped, not fatal
                with open(path, encoding='utf-8', errors='replace') as fh:
                    for line in fh:
                        try:
                            row = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if not isinstance(row, dict):
                            continue
                        if 'ref' in row:
                            marks.setdefault(row['ref'], {}).update({k: v for k, v in row.items() if k != 'ref'})
                        elif 'id' in row:
                            rows.append(row)
                            if row.get('replaces'):
                                replaced.add(row['replaces'])
            except FileNotFoundError:
                continue
        rows = [r for r in rows if r['id'] not in replaced and not marks.get(r['id'], {}).get('dropped')]
        rows = rows[-limit:][::-1]
        for row in rows:
            if row['id'] in marks:
                row['followup'] = marks[row['id']]
            if (row.get('details') or {}).get('pending'):
                # begun but never finished: still running in this PanXu, or PanXu was closed / killed meanwhile
                row['state'] = 'running' if row['id'] in self._open else 'interrupted'
                got = (row.get('followup') or {}).get('progress') or {}
                row['freed'] = int(got.get('freed') or 0)
                row['done_count'] = got.get('done')
        return rows

    def get(self, entry_id: str) -> dict | None:
        for row in self.entries(limit=100000):
            if row['id'] == entry_id:
                return row
        return None
