"""Sparse host-owned assessment index and observed-only inheritance.

Ratings describe cleanup inclination. This module never grants an action,
traverses a directory, or treats an absent observation as a complete tree.
"""
from __future__ import annotations

import copy
from datetime import datetime
from functools import cmp_to_key
import json

from .simulation import canonical, within, _parent
from .dispositions import DispositionStore


UNKNOWN_PROVENANCE = dict(source='unknown', provider=None, model=None, protocol=None, generated_at=None)


def rating(value):
    return value if type(value) is int and 1 <= value <= 5 else None


class AssessmentStore:
    def __init__(self, state, patterns):
        self.state, self.patterns = state, patterns
        self._cache_revision, self._records = None, {}
        self.dispositions = DispositionStore(state)
        with state.lock, state.db:
            state.db.executescript('''
                CREATE TABLE IF NOT EXISTS assessment_generations (
                    generation INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT UNIQUE NOT NULL);
                CREATE TABLE IF NOT EXISTS assessment_intents (item_id TEXT PRIMARY KEY, generation INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS assessment_scopes (path TEXT PRIMARY KEY, generation INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS assessment_records (
                    item_id TEXT PRIMARY KEY, signature TEXT, path TEXT, kind TEXT,
                    generation INTEGER NOT NULL, response_revision INTEGER NOT NULL,
                    revision INTEGER NOT NULL, payload TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS assessment_path ON assessment_records(path);
                CREATE TABLE IF NOT EXISTS assessment_state (id INTEGER PRIMARY KEY, revision INTEGER NOT NULL);
                INSERT OR IGNORE INTO assessment_state VALUES(1,0);
            ''')
            migrated = state.db.execute("SELECT value FROM meta WHERE key='assessment_index_v1'").fetchone()
            if not migrated:
                # Creation event order survives tasks' INSERT OR REPLACE updates.
                tasks = state.db.execute('''SELECT t.payload,e.first_seq,e.created_at FROM tasks t LEFT JOIN
                    (SELECT task_id,MIN(seq) AS first_seq,MIN(created_at) AS created_at FROM task_events WHERE kind='created' GROUP BY task_id) e
                    ON e.task_id=t.id''').fetchall()
                generations = {row[0]: row[1] for row in state.db.execute('SELECT task_id,generation FROM assessment_generations')}
                candidates = []
                for row in tasks:
                    task = json.loads(row[0])
                    generation = generations.get(task['id'], task.get('analysis_generation'))
                    generation = generation if type(generation) is int and generation > 0 else None
                    try:
                        created = datetime.fromisoformat(task.get('created_at') or row[2])
                        created = created.timestamp() if created.tzinfo is not None else None
                    except (ValueError, TypeError, OverflowError):
                        created = None
                    candidates.append((task, (generation, created, row[1])))

                def compare(a, b):
                    for index in (0, 1, 2):
                        left, right = a[1][index], b[1][index]
                        if left is not None and right is not None:
                            if left != right:
                                return (left > right) - (left < right)
                    return 0

                candidates.sort(key=cmp_to_key(compare))
                current = {row['id']: row for row in state.snapshot(patterns())['items']}
                sources = {}
                for task, order in candidates:
                    self.register(task)
                    additions = {}
                    for ident, advice in task.get('advice', {}).items():
                        item = current.get(ident)
                        if item:
                            additions[ident] = dict(advice, path=item['path'], kind=item['kind'])
                        elif advice.get('path') and advice.get('kind'):
                            additions[ident] = advice
                        else:
                            old = next((entry for entry in (task.get('plan') or {}).get('items', []) if entry['id'] == ident), None)
                            scopes = [root['path'] for root in task.get('roots', [])]
                            if old:
                                additions[ident] = dict(advice, path=old['path'], kind=old['kind'])
                            elif scopes:
                                # Legacy JSON may lack the child's path after a
                                # catalog replacement. Keep an explicit unknown
                                # barrier in its old scope rather than dropping it.
                                additions[ident] = dict(advice, path=scopes[0], kind='directory', unlocated_scope_paths=scopes)
                    for ident, advice in additions.items():
                        sources.setdefault(ident, []).append((task, order, copy.deepcopy(advice)))
                    self.publish(task, additions, migrating=True)
                ceiling = max((task['analysis_generation'] for task, _ in candidates), default=0)
                for ident, choices in sources.items():
                    if len(choices) < 2:
                        continue
                    # Missing retained creation evidence is not a newest date.
                    # If overlapping generations cannot be ordered, retain a
                    # host unknown barrier until a genuinely new analysis.
                    # One shared ordering coordinate prevents mixing a
                    # monotonic generation with incomparable wall-clock dates.
                    coordinate = next((index for index in (0, 2, 1)
                        if all(choice[1][index] is not None for choice in choices)), None)
                    ambiguous = coordinate is None
                    if coordinate is not None:
                        maximum = max(choice[1][coordinate] for choice in choices)
                        winners = [choice for choice in choices if choice[1][coordinate] == maximum]
                        ambiguous = len(winners) != 1
                        if not ambiguous:
                            task, _, advice = winners[0]
                            self.publish(task, {ident: advice}, migrating=True)
                    if ambiguous:
                        task, _, advice = choices[-1]
                        advice.update(conclusion='no_advice', cleanup_rating=None, impact_rating=None, inheritance='none',
                            provenance=copy.deepcopy(UNKNOWN_PROVENANCE),
                            reason='历史建议先后顺序不完整，请重新分析。',
                            migration_generation_ceiling=ceiling, migration_uncertain=True)
                        self.publish(task, {ident: advice}, migrating=True)
                state.db.execute("INSERT OR REPLACE INTO meta VALUES('assessment_index_v1','1')")

    def revision(self):
        with self.state.lock:
            return str(self.state.db.execute('SELECT revision FROM assessment_state WHERE id=1').fetchone()[0])

    def register(self, task):
        """Reserve newer analysis intent; caller owns the database transaction."""
        db = self.state.db
        db.execute('INSERT OR IGNORE INTO assessment_generations(task_id) VALUES(?)', (task['id'],))
        generation = db.execute('SELECT generation FROM assessment_generations WHERE task_id=?', (task['id'],)).fetchone()[0]
        task['analysis_generation'] = generation
        if task.get('non_analysis_intent') is True:
            return generation
        for ident in task.get('target_ids', task.get('known_ids', [])):
            db.execute('INSERT INTO assessment_intents VALUES(?,?) ON CONFLICT(item_id) DO UPDATE SET generation=MAX(generation,excluded.generation)', (ident, generation))
        directories = task.get('action_directories') if task.get('analysis') else [r['path'] for r in task.get('roots', [])]
        for path in directories or []:
            db.execute('INSERT INTO assessment_scopes VALUES(?,?) ON CONFLICT(path) DO UPDATE SET generation=MAX(generation,excluded.generation)', (canonical(path), generation))
        return generation

    def publish(self, task, additions, *, migrating=False):
        """Publish accepted direct records atomically with their task JSON.

        A newer task's intent blocks a late older response, while its failure
        leaves the previous accepted record intact. Old task views stay intact.
        """
        if not additions:
            return
        row = self.state.db.execute('SELECT generation FROM assessment_generations WHERE task_id=?', (task['id'],)).fetchone()
        generation = row[0] if row else self.register(task)
        task['analysis_generation'] = generation
        response_revision = task.get('advice_revision', 0)
        scopes = [(row[0], row[1]) for row in self.state.db.execute('SELECT path,generation FROM assessment_scopes')]
        for ident, advice in additions.items():
            path = canonical(advice['path'])
            intent = self.state.db.execute('SELECT generation FROM assessment_intents WHERE item_id=?', (ident,)).fetchone()
            newest = max([intent[0] if intent else 0] + [value for scope, value in scopes
                if advice['kind'] in ('file', 'directory') and within(path, scope)])
            old = self.state.db.execute('SELECT generation,response_revision,payload FROM assessment_records WHERE item_id=?', (ident,)).fetchone()
            if not migrating and (generation < newest or old and ((generation, response_revision) < (old[0], old[1])
                    or json.loads(old[2]).get('migration_generation_ceiling', -1) >= generation)):
                continue
            revision = int(self.revision()) + 1
            advice.update(task_id=task['id'], record_revision=revision)
            payload = dict(copy.deepcopy(advice), task_id=task['id'], advice_id=advice.get('advice_id') or f'legacy:{task["id"]}:{ident}',
                           cleanup_rating=rating(advice.get('cleanup_rating')), impact_rating=rating(advice.get('impact_rating')), inheritance=advice.get('inheritance', 'none'),
                           record_revision=revision)
            self.state.db.execute('INSERT OR REPLACE INTO assessment_records VALUES(?,?,?,?,?,?,?,?)',
                (ident, advice['signature'], path, advice['kind'], generation, response_revision, revision, json.dumps(payload, ensure_ascii=False)))
            self.state.db.execute('UPDATE assessment_state SET revision=? WHERE id=1', (revision,))
        self._cache_revision = None

    def _index(self):
        revision = self.revision()
        if revision != self._cache_revision:
            self._records = {row['item_id']: json.loads(row['payload']) for row in self.state.db.execute('SELECT item_id,payload FROM assessment_records')}
            self._cache_revision = revision
        return self._records

    @staticmethod
    def _ref(record):
        return dict(task_id=record.get('task_id'), advice_id=record.get('advice_id'), item_id=record['id'], revision=record.get('record_revision'))

    @staticmethod
    def _empty():
        return dict(state='unanalysed', rating=None, impact_rating=None, conclusion=None, purpose=None, reason=None, impact=None,
                    inheritance='none', provenance=copy.deepcopy(UNKNOWN_PROVENANCE), source_ref=None,
                    inherited_from=None, stale=False, blocked_reasons=[])

    def _direct(self, record, item):
        result = self._empty()
        stale = not item or item['signature'] != record['signature']
        conclusion = record.get('conclusion')
        result.update(state={'skip': 'skipped', 'no_advice': 'no_advice'}.get(conclusion, 'explicit'),
                      rating=None if stale else rating(record.get('cleanup_rating')),
                      impact_rating=None if stale else rating(record.get('impact_rating')),
                      conclusion=conclusion, inheritance=record.get('inheritance', 'none'),
                      provenance=copy.deepcopy(record.get('provenance') or UNKNOWN_PROVENANCE),
                      source_ref=self._ref(record), stale=stale,
                      **{key: record.get(key) for key in ('purpose', 'reason', 'impact')})
        for key in ('specialist', 'extra_suggestion_id'):
            if key in record:
                result[key] = copy.deepcopy(record[key])
        if (record.get('specialist') or {}).get('mode') == 'replace' or record.get('extra_suggestion_id'):
            result['blocked_reasons'].append('specialist_required')
        if stale:
            result['blocked_reasons'].append('stale_assessment')
        elif conclusion in ('skip', 'no_advice'):
            result['rating'] = None
            result['blocked_reasons'].append(conclusion)
        elif result['rating'] is None:
            result['blocked_reasons'].append('unscored_assessment')
        return result

    @staticmethod
    def _ancestors(path):
        current = _parent(canonical(path))
        while current:
            yield current
            parent = _parent(current)
            if current == parent:
                break
            current = parent

    def resolve(self, items, task=None):
        with self.state.lock:
            records = dict(self._index())
            if task:
                current = {item['id']: item for item in items}
                for ident, advice in task.get('advice', {}).items():
                    if ident in current:
                        records[ident] = dict(advice, task_id=task['id'], path=current[ident]['path'], kind=current[ident]['kind'])
            return self._resolve(items, records, task)

    def _resolve(self, items, records, task):
        current = {item['id']: item for item in items}
        paths = {canonical(item['path']): item for item in items if item['kind'] in ('file', 'directory') and item.get('path')}
        parent_records = {canonical(row['path']): row for row in records.values() if row.get('kind') == 'directory'}
        result = {}
        dispositions = self.dispositions.current()
        for item in items:
            record = records.get(item['id'])
            value = self._direct(record, item) if record else self._empty()
            if not record and item['kind'] in ('file', 'directory'):
                for parent in self._ancestors(item['path']):
                    ancestor = parent_records.get(parent)
                    if not ancestor:
                        continue
                    base = self._direct(ancestor, paths.get(parent))
                    if base['stale'] or base['rating'] is None or ancestor.get('inheritance', 'none') != 'subtree':
                        value['blocked_reasons'].append('ancestor_barrier')
                    else:
                        value = copy.deepcopy(base)
                        value.update(state='inherited', inheritance='none', inherited_from=self._ref(ancestor))
                    break  # A more specific statement is always a boundary.
            if item.get('risk') == 'protected':
                value['blocked_reasons'].append('protected')
            disposition = dispositions.get(item['id'])
            if disposition and not disposition['stale'] and disposition['value'] == 'direct_keep':
                value['blocked_reasons'].append('user_direct_keep')
            if task and any(choice['choice'] in ('keep', 'defer') and (key == item['id'] or
                    choice.get('kind') == 'directory' and within(item['path'], choice['path'])) for key, choice in task.get('choices', {}).items()):
                value['blocked_reasons'].append('user_kept')
            result[item['id']] = value
        # Global explicit exceptions remain relevant even if an older task is
        # being viewed; user holds, however, belong only to the current task.
        blockers = self._directory_blockers(items, self._index(), task)
        for item in items:
            if item['kind'] == 'directory':
                value = result[item['id']]
                value['blocked_reasons'] = list(dict.fromkeys(value['blocked_reasons'] + blockers.get(canonical(item['path']), [])))
        return result

    def _directory_blockers(self, items, records, task):
        current = {item['id']: item for item in items}
        directories = {canonical(item['path']): item for item in items if item['kind'] == 'directory'}
        children = set(parent for item in items if item['kind'] in ('file', 'directory') for parent in self._ancestors(item['path']))
        blocked = {}

        def add(path, reason, include_self=False):
            for parent in ([canonical(path)] if include_self else []) + list(self._ancestors(path)):
                if parent in directories:
                    values = blocked.setdefault(parent, [])
                    if reason not in values:
                        values.append(reason)

        for item in items:
            if item['kind'] in ('file', 'directory') and item.get('risk') == 'protected':
                add(item['path'], 'protected_descendant', item['kind'] == 'directory')
            if item['kind'] == 'directory':
                details = item.get('details', {})
                if details.get('complete') is not True or details.get('subtree_complete') is not True or details.get('can_simulate_delete') is False:
                    add(item['path'], 'coverage_incomplete', True)
        for record in records.values():
            if record.get('kind') not in ('file', 'directory'):
                continue
            item = current.get(record['id'])
            for scope in record.get('unlocated_scope_paths', []):
                add(scope, 'legacy_unknown_descendant', True)
            value = rating(record.get('cleanup_rating'))
            if not item or item['signature'] != record['signature']:
                reason = 'stale_or_unknown_descendant'
            elif (record.get('specialist') or {}).get('mode') == 'replace' or record.get('extra_suggestion_id'):
                reason = 'specialist_descendant'
            elif record.get('conclusion') in ('keep', 'skip', 'no_advice'):
                reason = 'explicit_' + record['conclusion']
            elif value is None:
                reason = 'unscored_descendant'
            elif value <= 3:
                reason = 'undecided_descendant'
            elif record.get('kind') == 'directory' and record.get('inheritance', 'none') != 'subtree' and canonical(record['path']) in children:
                reason = 'self_only_directory'
            else:
                continue
            add(record['path'], reason, record.get('kind') == 'directory')
        for choice in (task or {}).get('choices', {}).values():
            if choice['choice'] in ('keep', 'defer') and choice.get('kind') in ('file', 'directory'):
                add(choice['path'], 'user_kept_descendant', choice['kind'] == 'directory')
        for path in self.dispositions.direct_keep_paths(items):
            add(path, 'user_direct_keep_descendant', True)
        return blocked

    def directory_conflicts(self, item, items, task=None):
        with self.state.lock:
            reasons = self._directory_blockers(items, self._index(), task).get(canonical(item['path']), [])
        return dict(blocked=bool(reasons), reasons=reasons)
