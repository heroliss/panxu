"""Persistent user intent, independent of model advice and execution records."""
from __future__ import annotations

from datetime import datetime, timezone
import json

from .simulation import AppError, canonical, _parent


LABELS = {'lean_clean': '倾向清理', 'lean_keep': '倾向保留',
          'direct_clean': '直接清理', 'direct_keep': '直接保留'}


class DispositionStore:
    def __init__(self, state):
        self.state = state
        self._revision, self._records = None, {}
        self._current_key, self._current_values = None, {}
        with state.lock, state.db:
            state.db.executescript('''
                CREATE TABLE IF NOT EXISTS item_dispositions (
                    item_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS disposition_state (id INTEGER PRIMARY KEY, revision INTEGER NOT NULL);
                INSERT OR IGNORE INTO disposition_state VALUES(1,0);
            ''')

    def revision(self):
        with self.state.lock:
            return str(self.state.db.execute('SELECT revision FROM disposition_state WHERE id=1').fetchone()[0])

    def records(self):
        with self.state.lock:
            revision = self.revision()
            if self._revision != revision:
                self._records = {row[0]: json.loads(row[1]) for row in self.state.db.execute('SELECT item_id,payload FROM item_dispositions')}
                self._revision = revision
            return self._records

    def set(self, item, value, snapshot):
        if value not in (*LABELS, None) or not isinstance(snapshot, str) or snapshot != item.get('signature'):
            raise AppError('项目或清理偏好已变化，请刷新后再选择。', 'selection_changed', 409)
        if item.get('simulated') or not isinstance(item.get('signature'), str):
            raise AppError('请先刷新项目再设置清理偏好。', 'selection_changed', 409)
        with self.state.lock, self.state.db:
            if value is None:
                self.state.db.execute('DELETE FROM item_dispositions WHERE item_id=?', (item['id'],))
            else:
                row = dict(id=item['id'], value=value, signature=item['signature'], path=item['path'], kind=item['kind'],
                           file_identity=item.get('details', {}).get('file_identity'),
                           updated_at=datetime.now(timezone.utc).isoformat())
                self.state.db.execute('INSERT OR REPLACE INTO item_dispositions VALUES(?,?)', (item['id'], json.dumps(row, ensure_ascii=False)))
            self.state.db.execute('UPDATE disposition_state SET revision=revision+1 WHERE id=1')
            if self.state.db.execute("SELECT 1 FROM sqlite_master WHERE name='assessment_state'").fetchone():
                self.state.db.execute('UPDATE assessment_state SET revision=revision+1 WHERE id=1')
        return self.resolve([item]).get(item['id'])

    def resolve(self, items):
        """Nearest observed directory intent inherits; replacement entities do not."""
        with self.state.lock:
            return self._resolve(items)

    def _resolve(self, items):
        records = self.records()
        current = {item['id']: item for item in items}
        parents = {canonical(row['path']): row for row in records.values() if row['kind'] == 'directory'}
        result = {}
        for item in items:
            row = records.get(item['id'])
            inherited = False
            if row is None and item['kind'] in ('file', 'directory'):
                path = canonical(item['path'])
                while True:
                    parent = _parent(path)
                    if not parent or parent == path:
                        break
                    if parent in parents:
                        row, inherited = parents[parent], True
                        break
                    path = parent
            if row is None:
                continue
            source = current.get(row['id'])
            stale = not self._matches(row, source)
            result[item['id']] = dict(value=row['value'], label=LABELS[row['value']], inherited=inherited,
                source_id=row['id'], updated_at=row['updated_at'], stale=stale)
        return result

    def direct_keep_paths(self, items):
        with self.state.lock:
            current = {item['id']: item for item in items}
            return [row['path'] for row in self.records().values() if row['value'] == 'direct_keep'
                    and row['kind'] in ('file', 'directory') and row['id'] in current
                    and self._matches(row, current[row['id']])]

    @staticmethod
    def _matches(row, source):
        if source is None:
            return False
        if source.get('signature') == row['signature']:
            return True
        # A keep decision protects the same entity through ordinary edits.
        # Path-only identities cannot distinguish replacement objects.
        identity = row.get('file_identity')
        return bool(row['value'] == 'direct_keep' and row['kind'] in ('file', 'directory')
                    and isinstance(identity, str) and identity and not identity.startswith('path:')
                    and source.get('details', {}).get('file_identity') == identity
                    and source.get('kind') == row['kind'] and canonical(source['path']) == canonical(row['path']))

    def current(self):
        with self.state.lock:
            key = (self.state.revision, self.revision())
            if key != self._current_key:
                self._current_values = self._resolve(self.state.snapshot()['items'])
                self._current_key = key
            return self._current_values

    def get(self, item):
        with self.state.lock:
            return self.current().get(item['id'])

    def strong(self, item):
        with self.state.lock:
            value = self.get(item)
            return value['value'] if value and not value['stale'] and value['value'].startswith('direct_') else None
