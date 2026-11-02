"""Bounded browser projections over a revisioned in-memory observation index."""
from __future__ import annotations

import copy
import hashlib
import json

from .simulation import AppError, canonical, within, _parent


class FileCatalog:
    def __init__(self, state, patterns, assessments=None):
        self.state, self.patterns = state, patterns
        self.assessments = assessments
        self._token = None
        self._rows = []
        self._nodes = {}
        self._children = {}

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
            rows = [i for i in snapshot['items'] if i['kind'] in ('file', 'directory') and not i['simulated']
                    and not i.get('details', {}).get('synthetic')]
            blocked = set()
            for item in rows:
                if item['risk'] != 'protected':
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
                projected['can_select'] = bool(selectable)
                projected['details'] = {k: copy.deepcopy(details[k]) for k in (
                    'complete', 'subtree_complete', 'can_simulate_delete', 'file_identity', 'is_hardlink', 'hardlink_entities',
                    'timestamps', 'parent_id', 'root', 'scan_only') if k in details}
                assessment = copy.deepcopy(resolved.get(item['id'], dict(state='unanalysed', rating=None,
                    conclusion=None, stale=False, blocked_reasons=[])))
                assessment.update(analysis_state=assessment['state'], cleanup_rating=assessment['rating'])
                source_id = (assessment.get('inherited_from') or {}).get('item_id')
                source = snapshot_index.get(source_id)
                if source:
                    assessment.update(inherited_from_name=source['name'], inherited_from_path=source['path'])
                projected['assessment'] = assessment
                projected['navigation_only'] = False
                projected['can_prepare'] = bool(selectable and 'delete' in (item.get('actions') or [])
                    and not assessment.get('blocked_reasons') and not assessment.get('stale'))
                high = assessment['rating'] in (4, 5) and projected['can_prepare']
                assessment['eligible_direct'] = bool(high and assessment['state'] == 'explicit')
                assessment['eligible_with_inherited'] = bool(high and assessment['state'] in ('explicit', 'inherited'))
                self._rows.append(projected)
            self._rows.sort(key=lambda i: (-(i.get('size_bytes') or 0), str(i['name']).casefold(), i['id']))
            self._build_tree()
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
            parent = paths.get(_parent(canonical(row['path'])))
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
             view='all', parent_id='', rating='all'):
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
            token, rows = self._index()
            if expected and expected != token:
                raise AppError('检查清单已有更新，将重新载入第一页。', 'catalog_changed', 409)
            q = query.strip().casefold()
            filtered = [i for i in rows if (not q or q in (i['name'] + ' ' + i['path']).casefold())
                        and (not scope or within(i['path'], scope)) and (risk == 'all' or i['risk'] == risk)
                        and self._rating_matches(i, rating)]
            breadcrumbs = []
            if view == 'suggestions':
                filtered = [i for i in filtered if i['assessment']['state'] in ('explicit', 'skipped', 'no_advice')]
            elif view == 'tree':
                if parent_id and (parent_id not in self._nodes or self._nodes[parent_id]['kind'] != 'directory'):
                    raise AppError('目录已更新，请返回已扫描范围。', 'catalog_changed', 409)
                visible = {row['id'] for row in filtered}
                for ident in list(visible):
                    current = self._nodes[ident].get('tree_parent_id')
                    while current:
                        visible.add(current)
                        current = self._nodes[current].get('tree_parent_id')
                filtered = [row for row in self._children.get(parent_id, []) if row['id'] in visible]
                current = parent_id
                while current:
                    row = self._nodes[current]
                    breadcrumbs.insert(0, {key: row[key] for key in ('id', 'name', 'path')})
                    current = row.get('tree_parent_id')
            return dict(revision=token, offset=offset, limit=limit, total=len(filtered), catalog_total=len(rows),
                        items=copy.deepcopy(filtered[offset:offset + limit]),
                        has_more=offset + limit < len(filtered), view=view, parent_id=parent_id, breadcrumbs=breadcrumbs,
                        assessment_revision=self.assessments.revision() if self.assessments else None,
                        coverage=dict(known_items=len(rows), observed_only=True,
                            note='仅展示已扫描并保存的内容；展开目录不会新增扫描，未扫描部分不代表为空。'))
