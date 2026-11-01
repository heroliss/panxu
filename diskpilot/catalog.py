"""Bounded browser projections over a revisioned in-memory observation index."""
from __future__ import annotations

import copy
import hashlib
import json

from .simulation import AppError, canonical, within, _parent


class FileCatalog:
    def __init__(self, state, patterns):
        self.state, self.patterns = state, patterns
        self._token = None
        self._rows = []

    def token(self):
        policy = hashlib.sha256(json.dumps(self.patterns(), ensure_ascii=False).encode()).hexdigest()[:16]
        return f'{self.state.revision}:{policy}'

    def _index(self):
        token = self.token()
        if token != self._token:
            snapshot = self.state.snapshot(self.patterns())
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
                    'complete', 'subtree_complete', 'can_simulate_delete', 'file_identity', 'is_hardlink', 'hardlink_entities') if k in details}
                self._rows.append(projected)
            self._rows.sort(key=lambda i: (-(i.get('size_bytes') or 0), str(i['name']).casefold(), i['id']))
            self._token = token
        return token, self._rows

    def page(self, *, offset=0, limit=50, query='', scope='', risk='all', expected=None):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise AppError('每页显示1到100项，请使用有效页码。')
        if not isinstance(query, str) or len(query) > 200 or not isinstance(scope, str) or len(scope) > 2000:
            raise AppError('搜索或范围过长。')
        if risk not in ('all', 'low', 'review', 'protected'):
            raise AppError('请选择有效的内容筛选。')
        with self.state.lock:
            token, rows = self._index()
            if expected and expected != token:
                raise AppError('检查清单已有更新，将重新载入第一页。', 'catalog_changed', 409)
            q = query.strip().casefold()
            filtered = [i for i in rows if (not q or q in (i['name'] + ' ' + i['path']).casefold())
                        and (not scope or within(i['path'], scope)) and (risk == 'all' or i['risk'] == risk)]
            return dict(revision=token, offset=offset, limit=limit, total=len(filtered), catalog_total=len(rows),
                        items=copy.deepcopy(filtered[offset:offset + limit]),
                        has_more=offset + limit < len(filtered))
