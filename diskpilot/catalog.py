"""Bounded browser projections over a revisioned in-memory observation index."""
from __future__ import annotations

import copy
import base64
from collections import Counter
import hashlib
import json

from .simulation import AppError, canonical, within, _parent
from .action_semantics import FILE_KINDS, KINDS, action_details, action_groups, in_task_scope, primary_action, unavailable_reason


class FileCatalog:
    def __init__(self, state, patterns, assessments=None):
        self.state, self.patterns = state, patterns
        self.assessments = assessments
        self._token = None
        self._rows = []
        self._nodes = {}
        self._children = {}
        self._selection_cache = {}

    def token(self):
        policy = hashlib.sha256(json.dumps(self.patterns(), ensure_ascii=False).encode()).hexdigest()[:16]
        suffix = ':' + self.assessments.revision() if self.assessments else ''
        return f'{self.state.revision}:{policy}{suffix}'

    def _index(self):
        token = self.token()
        if token != self._token:
            snapshot = self.state.snapshot(self.patterns())
            snapshot_index = {item['id']: item for item in snapshot['items']}
            resolved = self.assessments.resolve(snapshot['items']) if self.assessments else {}
            rows = [i for i in snapshot['items'] if i['kind'] in KINDS and not i['simulated']
                    and not i.get('details', {}).get('synthetic')]
            blocked = set()
            for item in rows:
                if item['risk'] != 'protected' or item['kind'] not in FILE_KINDS:
                    continue
                path = canonical(item['path'])
                while path:
                    blocked.add(path)
                    parent = _parent(path)
                    if path == parent:
                        break
                    path = parent
            self._rows = []
            for item in rows:
                details = item.get('details', {})
                selectable = bool(item.get('actions')) and item['risk'] != 'protected'
                if item['kind'] == 'directory':
                    selectable = selectable and details.get('complete') is True and details.get('subtree_complete') is True
                    selectable = selectable and details.get('can_simulate_delete') is not False and canonical(item['path']) not in blocked
                projected = {k: item.get(k) for k in ('id', 'kind', 'name', 'path', 'owner', 'category', 'size_bytes', 'allocated_bytes', 'risk', 'actions', 'signature', 'simulated')}
                projected['can_select'] = bool(selectable and primary_action(item))
                projected.update(action_details(primary_action(item)))
                projected['action_unavailable_reason'] = unavailable_reason(item)
                projected['details'] = {k: copy.deepcopy(details[k]) for k in (
                    'complete', 'subtree_complete', 'can_simulate_delete', 'file_identity', 'is_hardlink', 'hardlink_entities',
                    'timestamps', 'parent_id', 'root', 'scan_only', 'state', 'start_mode', 'pid',
                    'version', 'scope', 'has_uninstaller', 'observed_at', 'registry_location') if k in details}
                if item['kind'] == 'service':
                    projected['details']['state_label'] = {'Running': '正在运行', 'Stopped': '已停止',
                        'Start Pending': '正在启动', 'Stop Pending': '正在停止', 'Paused': '已暂停'}.get(details.get('state'), '状态尚未确认')
                assessment = copy.deepcopy(resolved.get(item['id'], dict(state='unanalysed', rating=None,
                    conclusion=None, stale=False, blocked_reasons=[])))
                assessment.update(analysis_state=assessment['state'], cleanup_rating=assessment['rating'])
                source_id = (assessment.get('inherited_from') or {}).get('item_id')
                source = snapshot_index.get(source_id)
                if source:
                    assessment.update(inherited_from_name=source['name'], inherited_from_path=source['path'])
                projected['assessment'] = assessment
                projected['navigation_only'] = False
                projected['can_prepare'] = bool(selectable and projected['action']
                    and not assessment.get('blocked_reasons') and not assessment.get('stale'))
                high = assessment['rating'] in (4, 5) and projected['can_prepare']
                assessment['eligible_direct'] = bool(high and assessment['state'] == 'explicit')
                assessment['eligible_with_inherited'] = bool(high and assessment['state'] in ('explicit', 'inherited'))
                self._rows.append(projected)
            self._rows.sort(key=lambda i: (-(i.get('size_bytes') or 0), str(i['name']).casefold(), i['id']))
            self._build_tree()
            self._selection_cache.clear()
            self._token = token
        return token, self._rows

    def _build_tree(self):
        """Link observations only. Missing intermediate nodes are navigation, never evidence."""
        self._nodes = {row['id']: row for row in self._rows}
        paths = {}
        for row in self._rows:
            if row['kind'] == 'directory':
                paths.setdefault(canonical(row['path']), row['id'])
        for row in list(self._rows):
            path = canonical(row['path'])
            parent_path = _parent(path)
            boundary = row.get('details', {}).get('root')
            boundary = canonical(boundary) if isinstance(boundary, str) and boundary else None
            # Scans may publish a leaf before intermediate observations. The
            # bridge is visibly unknown and has no selectable operation.
            while boundary and path != boundary and parent_path and within(parent_path, boundary) and parent_path not in paths:
                ident = 'navigation:' + hashlib.sha256(parent_path.encode()).hexdigest()[:24]
                paths[parent_path] = ident
                self._nodes[ident] = dict(id=ident, kind='directory', name=parent_path.replace('\\', '/').rstrip('/').rsplit('/', 1)[-1] or parent_path,
                    path=parent_path, owner='', category='navigation', size_bytes=None, allocated_bytes=None, risk='review',
                    actions=[], signature=None, simulated=False, can_select=False, can_prepare=False, navigation_only=True,
                    details={'root': boundary, 'complete': False, 'subtree_complete': False},
                    assessment=dict(state='unanalysed', rating=None, analysis_state='unanalysed', cleanup_rating=None,
                                    blocked_reasons=[], eligible_direct=False, eligible_with_inherited=False))
                if parent_path == boundary:
                    break
                path, parent_path = parent_path, _parent(parent_path)
        self._children = {}
        for row in self._nodes.values():
            parent = paths.get(_parent(canonical(row['path']))) if row['kind'] in FILE_KINDS else None
            if parent == row['id']:
                parent = None
            row['tree_parent_id'] = parent or ''
            self._children.setdefault(parent or '', []).append(row)
        for rows in self._children.values():
            rows.sort(key=lambda row: (row['kind'] != 'directory', str(row['name']).casefold(), row['id']))
        for row in self._nodes.values():
            row['children_count'] = len(self._children.get(row['id'], []))
            row['has_children'] = bool(row['children_count'] or row['kind'] == 'directory'
                and row.get('details', {}).get('complete') is not True)

    @staticmethod
    def _rating_matches(item, selected):
        value = item.get('assessment', {})
        score, state = value.get('rating'), value.get('state', 'unanalysed')
        return (selected == 'all' or selected == 'high' and score in (4, 5)
                or selected == 'low' and score in (1, 2) or selected == 'neutral' and score == 3
                or selected == 'unrated' and score is None and state not in ('skipped', 'no_advice')
                or selected in ('skipped', 'no_advice') and state == selected
                or selected in ('1', '2', '3', '4', '5') and score == int(selected))

    def selection_revision(self, task=None):
        """Bind logical choices to task intent as well as inventory/model data."""
        if task is None:
            return self.token() + ':all'
        if not isinstance(task, dict) or not isinstance(task.get('id'), str):
            raise AppError('请选择有效的整理任务。')
        fields = {key: task.get(key) for key in ('id', 'known_ids', 'target_ids', 'roots', 'action_directories',
            'scope_kind', 'analysis', 'analysis_generation', 'advice_revision', 'choices')}
        digest = hashlib.sha256(json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:24]
        return self.token() + ':' + digest

    @staticmethod
    def _task_holds(task, row):
        return bool(task and any(choice.get('choice') in ('keep', 'defer') and (ident == row['id']
            or choice.get('kind') == 'directory' and row['kind'] in FILE_KINDS and within(row['path'], choice.get('path', '')))
            for ident, choice in task.get('choices', {}).items()))

    @staticmethod
    def _manual_blockers(row):
        reasons = []
        if row.get('navigation_only'):
            reasons.append('navigation_only')
        if row.get('simulated'):
            reasons.append('already_simulated')
        if row.get('risk') == 'protected':
            reasons.append('protected')
        if not primary_action(row):
            reasons.append('unsupported_action')
        if not isinstance(row.get('signature'), str) or not row.get('signature'):
            reasons.append('missing_snapshot')
        return reasons

    def _selection_rows(self, task=None):
        _, rows = self._index()
        cache_key = self.selection_revision(task)
        if cache_key in self._selection_cache:
            return self._selection_cache[cache_key]
        if task is not None:
            known = set(task.get('known_ids', [])) | set(task.get('target_ids', []))
            rows = [row for row in rows if in_task_scope(task, row, known=known)]
        else:
            rows = [row for row in rows if row['kind'] in FILE_KINDS]
        result = copy.deepcopy(rows)
        resolved = self.assessments.resolve(self.state.snapshot(self.patterns())['items'], task=task) if task and self.assessments else {}
        for row in result:
            if row['id'] in resolved:
                row['assessment'] = copy.deepcopy(resolved[row['id']])
                row['assessment'].update(analysis_state=row['assessment']['state'], cleanup_rating=row['assessment']['rating'])
                row['can_prepare'] = bool(row['can_select'] and row['action'] and not row['assessment'].get('blocked_reasons')
                                          and not row['assessment'].get('stale'))
            row['can_logical_select'] = not self._manual_blockers(row)
            row['user_kept'] = self._task_holds(task, row)
            assessment = row['assessment']
            source = assessment.get('inherited_from') or assessment.get('source_ref') or {}
            current_source = task is not None and source.get('task_id') == task['id']
            assessment['from_current_task'] = current_source
            row['can_auto_select'] = bool(row['can_logical_select'] and row['can_prepare'] and not row['user_kept']
                and current_source and assessment.get('state') in ('explicit', 'inherited') and assessment.get('rating') in (4, 5)
                and not assessment.get('stale'))
        self._selection_cache[cache_key] = result
        if len(self._selection_cache) > 2:
            self._selection_cache.pop(next(iter(self._selection_cache)))
        return result

    @staticmethod
    def _category(row):
        assessment = row.get('assessment', {})
        if assessment.get('state') in ('skipped', 'no_advice'):
            return assessment['state']
        value = assessment.get('rating')
        return str(value) if type(value) is int and 1 <= value <= 5 else 'unrated'

    def _groups(self, rows):
        groups = {key: dict(key=key, total=0, eligible=0, automatic=0)
                  for key in ('1', '2', '3', '4', '5', 'skipped', 'unrated', 'no_advice')}
        for row in rows:
            group = groups[self._category(row)]
            group['total'] += 1
            group['eligible'] += int(row['can_logical_select'])
            group['automatic'] += int(row['can_auto_select'])
        return list(groups.values())

    @staticmethod
    def _filter_rows(rows, query='', scope='', risk='all', kind='all'):
        if not isinstance(query, str) or len(query) > 200 or not isinstance(scope, str) or len(scope) > 2000:
            raise AppError('搜索或范围过长。')
        if risk not in ('all', 'low', 'review', 'protected'):
            raise AppError('请选择有效的内容筛选。')
        if kind not in ('all', *KINDS):
            raise AppError('请选择有效的对象类型。')
        query = query.strip().casefold()
        return [row for row in rows if (not query or query in (row['name'] + ' ' + row['path']).casefold())
                and (not scope or row['kind'] in FILE_KINDS and within(row['path'], scope))
                and (risk == 'all' or row['risk'] == risk) and (kind == 'all' or row['kind'] == kind)]

    def _selection_tree(self, rows):
        index = {row['id']: row for row in rows}
        paths = {canonical(row['path']): row['id'] for row in rows if row['kind'] == 'directory'}
        children, roots = {}, []
        for row in rows:
            if row['kind'] not in FILE_KINDS:
                roots.append(row['id'])
                continue
            own_path = canonical(row['path'])
            path = _parent(own_path)
            while path and path not in paths:
                parent = _parent(path)
                if parent == path:
                    break
                path = parent
            parent_id = paths.get(path) if path != own_path else None
            if parent_id and parent_id != row['id']:
                children.setdefault(parent_id, []).append(row['id'])
            else:
                roots.append(row['id'])
        outside = set()
        for row in self._rows:
            if row['id'] in index or row['kind'] not in FILE_KINDS:
                continue
            path = _parent(canonical(row['path']))
            while path:
                if path in paths:
                    outside.add(paths[path])
                parent = _parent(path)
                if parent == path:
                    break
                path = parent
        order = lambda ident: (canonical(index[ident]['path']), ident)
        roots.sort(key=order)
        for values in children.values():
            values.sort(key=order)
        return index, children, roots, outside

    def _selection_page(self, ids, rows, *, task, counts, groups, fingerprint, limit, cursor,
                        expected=None, expected_selection_revision=None, extra=None):
        if type(limit) is not int or not 1 <= limit <= 500:
            raise AppError('每批请选择1到500项。')
        revision, selection_revision = self.token(), self.selection_revision(task)
        if expected is not None and expected != revision or expected_selection_revision is not None and expected_selection_revision != selection_revision:
            raise AppError('文件、评分或选择范围已更新，请重新核对。', 'selection_changed', 409)
        fingerprint = hashlib.sha256(json.dumps(fingerprint, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()[:24]
        offset = 0
        if cursor is not None:
            try:
                if not isinstance(cursor, str) or len(cursor) > 2000:
                    raise ValueError()
                value = json.loads(base64.urlsafe_b64decode(cursor + '=' * (-len(cursor) % 4)))
                if (not isinstance(value, dict) or set(value) != {'r', 'f', 'o'} or value['r'] != selection_revision
                        or value['f'] != fingerprint or type(value['o']) is not int or value['o'] < 0):
                    raise ValueError()
                offset = value['o']
            except (ValueError, TypeError, UnicodeError):
                raise AppError('分批选择已更新，请重新开始。', 'selection_changed', 409) from None
        chosen = ids[offset:offset + limit]
        has_more = offset + limit < len(ids)
        next_cursor = base64.urlsafe_b64encode(json.dumps(dict(r=selection_revision, f=fingerprint, o=offset + limit), separators=(',', ':')).encode()).decode().rstrip('=') if has_more else None
        index = {row['id']: row for row in rows}
        result = dict(ids=chosen, snapshots={ident: index[ident]['signature'] for ident in chosen},
            items=[dict({key: index[ident][key] for key in ('id', 'name', 'kind', 'path', 'signature')},
                        **action_details(primary_action(index[ident])),
                        category=self._category(index[ident]),
                        assessment={key: copy.deepcopy(index[ident]['assessment'].get(key)) for key in ('state', 'rating')}) for ident in chosen],
            revision=revision, catalog_revision=revision, selection_revision=selection_revision,
            task_id=task['id'] if task else None, counts=dict(counts, total_targets=len(ids), returned=len(chosen)),
            groups=groups, has_more=has_more, next_cursor=next_cursor, requires_batches=len(ids) > limit,
            offset=offset, limit=limit, observed_only=True)
        result['action_groups'] = action_groups([dict(index[ident], action=primary_action(index[ident])) for ident in ids], set(chosen))
        if extra:
            result.update(extra)
        return result

    def batch_selection(self, *, task=None, category='high', mode='manual', include_inherited=True,
                        query='', scope='', risk='all', kind='all', cursor=None, limit=500, expected=None,
                        expected_selection_revision=None):
        """Read the full filtered task, returning logical category roots in bounded batches."""
        if category == 'skip':
            category = 'skipped'
        if category not in ('all', 'high', 'low', 'neutral', 'unrated', 'skipped', 'no_advice', '1', '2', '3', '4', '5') or mode not in ('manual', 'automatic') or type(include_inherited) is not bool:
            raise AppError('请选择有效的评分分类和选择方式。')
        if mode == 'automatic' and task is None:
            raise AppError('自动选择需要当前分析任务。', 'task_scope_required')
        with self.state.lock:
            rows = self._selection_rows(task)
            filtered = self._filter_rows(rows, query, scope, risk, kind)
            matching = [row for row in filtered if self._rating_matches(row, category)]
            index, children, roots, outside = self._selection_tree(rows)
            eligible, reasons = set(), Counter()
            for row in matching:
                blockers = self._manual_blockers(row)
                if mode == 'automatic':
                    if row['user_kept']:
                        blockers.append('user_kept')
                    if not row['assessment']['from_current_task']:
                        blockers.append('other_task_advice')
                    if row['assessment'].get('rating') not in (4, 5) or row['assessment'].get('state') not in ('explicit', 'inherited'):
                        blockers.append('not_high_rating')
                    if not row['can_prepare']:
                        blockers.append('advisory_or_coverage_barrier')
                if not include_inherited and row['assessment'].get('state') == 'inherited':
                    blockers.append('inherited_not_included')
                if blockers:
                    reasons.update(set(blockers))
                else:
                    eligible.add(row['id'])
            full = {}
            for row in sorted(rows, key=lambda row: len(canonical(row['path'])), reverse=True):
                ident = row['id']
                full[ident] = (ident in eligible and ident not in outside and all(full[child] for child in children.get(ident, []))
                    and (row['kind'] != 'directory' or row['details'].get('complete') is True and row['details'].get('subtree_complete') is True
                         and row['details'].get('can_simulate_delete') is not False))
            chosen, expanded = [], 0
            pending = list(reversed(roots))
            while pending:
                ident = pending.pop()
                row = index[ident]
                if ident in eligible and (row['kind'] != 'directory' or full[ident]):
                    chosen.append(ident)
                    continue
                if row['kind'] == 'directory' and ident in eligible:
                    expanded += 1
                pending.extend(reversed(children.get(ident, [])))
            if mode == 'automatic':
                chosen.sort(key=lambda ident: (index[ident]['assessment'].get('state') != 'explicit',
                    -(index[ident]['assessment'].get('rating') or 0), -(index[ident].get('size_bytes') or 0),
                    canonical(index[ident]['path']), ident))
            return self._selection_page(chosen, rows, task=task,
                counts=dict(scope_rows=len(rows), filtered_rows=len(filtered), matched_rows=len(matching), eligible_rows=len(eligible),
                            expanded_directories=expanded, blocked_reasons=dict(reasons)), groups=self._groups(filtered),
                fingerprint=dict(kind='category', category=category, mode=mode, include_inherited=include_inherited,
                                 ordering='direct-rating-size-v1' if mode == 'automatic' else 'tree-v1', query=query, scope=scope, risk=risk, object_kind=kind),
                limit=limit, cursor=cursor, expected=expected, expected_selection_revision=expected_selection_revision)

    def compile_selection(self, selected_ids, excluded_ids=(), *, snapshots, expected, task=None,
                          expected_selection_revision=None, limit=500, cursor=None):
        """Compile nearest explicit checkbox rules without reading the filesystem."""
        if (not isinstance(selected_ids, (list, tuple)) or not isinstance(excluded_ids, (list, tuple))
                or any(not isinstance(ident, str) for ident in [*selected_ids, *excluded_ids])
                or len(selected_ids) + len(excluded_ids) > 5000
                or len(set(selected_ids)) != len(selected_ids) or len(set(excluded_ids)) != len(excluded_ids)
                or set(selected_ids) & set(excluded_ids)):
            raise AppError('目录选择规则无效，请重新勾选。')
        explicit = set(selected_ids) | set(excluded_ids)
        if not isinstance(expected, str) or not expected:
            raise AppError('请保留当前文件清单版本。', 'selection_changed', 409)
        if not isinstance(snapshots, dict) or set(snapshots) != explicit or any(not isinstance(value, str) for value in snapshots.values()):
            raise AppError('请保留所有选中和排除项目的当前签名。', 'selection_changed', 409)
        with self.state.lock:
            rows = self._selection_rows(task)
            index, children, roots, outside = self._selection_tree(rows)
            if any(ident not in index or snapshots[ident] != index[ident]['signature'] for ident in explicit):
                raise AppError('选中或排除的内容已变化，请重新核对。', 'selection_changed', 409)
            rules = {}
            for ident in [*selected_ids, *excluded_ids]:
                if index[ident]['kind'] not in FILE_KINDS:
                    continue
                path, choice = canonical(index[ident]['path']), ident in selected_ids
                if path in rules and rules[path] != choice:
                    raise AppError('同一位置有相互冲突的选择，请重新勾选。')
                rules[path] = choice
            def decision(row):
                if row['kind'] not in FILE_KINDS:
                    return row['id'] in selected_ids
                path = canonical(row['path'])
                while path:
                    if path in rules:
                        return rules[path]
                    parent = _parent(path)
                    if parent == path:
                        break
                    path = parent
                return False
            chosen_rows = {row['id'] for row in rows if decision(row)}
            eligible = {row['id'] for row in rows if row['id'] in chosen_rows and not self._manual_blockers(row)}
            full = {}
            partial, expanded, blocked, selected, overridden = set(), set(), Counter(), [], set()
            for row in sorted(rows, key=lambda row: len(canonical(row['path'])), reverse=True):
                ident, details = row['id'], row['details']
                hard = self._manual_blockers(row)
                if ident in chosen_rows:
                    blocked.update(set(hard))
                    if row['user_kept'] and not hard:
                        overridden.add(ident)
                directory_ok = True
                if row['kind'] == 'directory':
                    directory_ok = details.get('complete') is True and details.get('subtree_complete') is True and details.get('can_simulate_delete') is not False and ident not in outside
                    if ident in chosen_rows and not directory_ok:
                        partial.add(ident)
                    advisory = set(row['assessment'].get('blocked_reasons', [])) - {'user_kept', 'user_kept_descendant'}
                    # Empty containers can be an explicit user choice. For a
                    # populated directory, specific statements force expansion.
                    if children.get(ident) and advisory:
                        directory_ok = False
                full[ident] = ident in eligible and directory_ok and all(full[child] for child in children.get(ident, []))
            pending = list(reversed(roots))
            while pending:
                ident = pending.pop()
                row = index[ident]
                if ident in eligible and (row['kind'] != 'directory' or full[ident]):
                    selected.append(ident)
                    continue
                if row['kind'] == 'directory' and ident in chosen_rows:
                    expanded.add(ident)
                pending.extend(reversed(children.get(ident, [])))
            warnings = []
            if partial:
                warnings.append('部分目录尚未完整读取或含本轮范围外内容，只选择了已核对的具体项。')
            if blocked:
                warnings.append('受保护或不支持删除的项目已排除。')
            return self._selection_page(selected, rows, task=task,
                counts=dict(scope_rows=len(rows), logical_selected_rows=len(chosen_rows), eligible_rows=len(eligible),
                    expanded_directories=len(expanded), partial=bool(partial), partial_directories=len(partial),
                    unscanned_directories=sum(index[ident]['details'].get('complete') is not True or index[ident]['details'].get('subtree_complete') is not True for ident in partial),
                    scope_gap_directories=len(partial & outside), overridden_count=len(overridden), blocked_reasons=dict(blocked)),
                groups=self._groups(rows), fingerprint=dict(kind='compile', selected=sorted(selected_ids), excluded=sorted(excluded_ids)),
                limit=limit, cursor=cursor, expected=expected, expected_selection_revision=expected_selection_revision,
                extra=dict(logical=dict(selected_ids=list(selected_ids), excluded_ids=list(excluded_ids)), warnings=warnings))

    def selected(self, ids, *, expected):
        """Validate the frozen browser selection without scanning or executing."""
        token, rows = self._index()
        if expected != token:
            raise AppError('建议或文件清单已有更新，请重新查看后选择。', 'catalog_changed', 409)
        index = {row['id']: row for row in rows}
        if any(ident not in index or not index[ident]['can_prepare'] for ident in ids):
            raise AppError('所选内容暂不能整体删除，请检查保护项、保留建议和扫描范围。', 'selection_blocked', 409)
        return [index[ident] for ident in ids]

    def page(self, *, offset=0, limit=50, query='', scope='', risk='all', expected=None,
             view='all', parent_id='', rating='all', kind='all', task=None):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise AppError('每页显示1到100项，请使用有效页码。')
        if not isinstance(query, str) or len(query) > 200 or not isinstance(scope, str) or len(scope) > 2000:
            raise AppError('搜索或范围过长。')
        if risk not in ('all', 'low', 'review', 'protected'):
            raise AppError('请选择有效的内容筛选。')
        if view not in ('all', 'suggestions', 'tree') or not isinstance(parent_id, str) or len(parent_id) > 200:
            raise AppError('请选择有效的文件视图或目录。')
        if rating not in ('all', 'high', 'low', 'neutral', 'unrated', 'skipped', 'no_advice', '1', '2', '3', '4', '5'):
            raise AppError('请选择有效的评分筛选。')
        with self.state.lock:
            token, _ = self._index()
            rows = self._selection_rows(task)
            if view == 'tree' and task and task.get('scope_kind') == 'applications':
                raise AppError('后台和软件对象按身份列出，请使用建议或全部对象视图。', 'invalid_catalog_view')
            if expected and expected != token:
                raise AppError('检查清单已有更新，将重新载入第一页。', 'catalog_changed', 409)
            base_filtered = self._filter_rows(rows, query, scope, risk, kind)
            filtered = [row for row in base_filtered if self._rating_matches(row, rating)]
            breadcrumbs = []
            if view == 'suggestions':
                filtered = [i for i in filtered if i['assessment']['state'] in ('explicit', 'skipped', 'no_advice')
                            and (task is None or i['assessment'].get('from_current_task'))]
            elif view == 'tree':
                if parent_id and (parent_id not in self._nodes or self._nodes[parent_id]['kind'] != 'directory'):
                    raise AppError('目录已更新，请返回已扫描范围。', 'catalog_changed', 409)
                visible = {row['id'] for row in filtered}
                for ident in list(visible):
                    current = self._nodes[ident].get('tree_parent_id')
                    while current:
                        visible.add(current)
                        current = self._nodes[current].get('tree_parent_id')
                scoped = {row['id']: row for row in rows}
                selected_rows = []
                for row in self._children.get(parent_id, []):
                    if row['id'] not in visible:
                        continue
                    if row['id'] in scoped:
                        selected_rows.append(scoped[row['id']])
                    else:
                        navigation = copy.deepcopy(row)
                        navigation.update(navigation_only=True, can_select=False, can_prepare=False, can_logical_select=False, can_auto_select=False)
                        selected_rows.append(navigation)
                filtered = selected_rows
                current = parent_id
                while current:
                    row = self._nodes[current]
                    breadcrumbs.insert(0, {key: row[key] for key in ('id', 'name', 'path')})
                    current = row.get('tree_parent_id')
            return dict(revision=token, offset=offset, limit=limit, total=len(filtered), catalog_total=len(rows),
                        items=copy.deepcopy(filtered[offset:offset + limit]),
                        has_more=offset + limit < len(filtered), view=view, parent_id=parent_id, breadcrumbs=breadcrumbs, kind=kind,
                        task_id=task['id'] if task else None, selection_revision=self.selection_revision(task), groups=self._groups(base_filtered),
                        assessment_revision=self.assessments.revision() if self.assessments else None,
                        coverage=dict(known_items=len(rows), observed_only=True,
                            note=('仅展示本任务已读取的注册和运行元数据，不代表全机全部自启动机制。' if task and task.get('scope_kind') == 'applications' else
                                  '仅展示已扫描并保存的内容；展开目录不会新增扫描，未扫描部分不代表为空。')))
