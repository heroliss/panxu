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
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(path), exist_ok=True)

    def add(self, kind: str, title: str, *, freed: int = 0, items=None, failures=None, details=None,
            revert=None) -> dict:
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
        line = json.dumps(entry, ensure_ascii=False)
        with self._lock:
            self._rotate()
            with open(self.path, 'a', encoding='utf-8') as fh:
                fh.write(line + '\n')
        return entry

    def mark(self, entry_id: str, **changes) -> None:
        """Record a follow-up (e.g. reverted) as a new line referencing the entry."""
        with self._lock:
            with open(self.path, 'a', encoding='utf-8') as fh:
                fh.write(json.dumps({'ref': entry_id, 'time': time.time(), **changes}, ensure_ascii=False) + '\n')

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
        rows, marks = [], {}
        for path in (self.path + '.1', self.path):
            try:
                with open(path, encoding='utf-8') as fh:
                    for line in fh:
                        try:
                            row = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if 'ref' in row:
                            marks.setdefault(row['ref'], {}).update({k: v for k, v in row.items() if k != 'ref'})
                        else:
                            rows.append(row)
            except FileNotFoundError:
                continue
        rows = rows[-limit:][::-1]
        for row in rows:
            if row['id'] in marks:
                row['followup'] = marks[row['id']]
        return rows

    def get(self, entry_id: str) -> dict | None:
        for row in self.entries(limit=100000):
            if row['id'] == entry_id:
                return row
        return None
