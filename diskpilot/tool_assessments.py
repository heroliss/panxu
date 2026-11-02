"""Shared, state-aware assessments for registered specialist tools.

The registry is a capability catalogue, not a claim of collector coverage. All
amounts below are observed logical bytes; even a complete observation can only
provide an upper bound for a simulated cleanup, never promised disk savings.
"""
from __future__ import annotations

from collections import OrderedDict
import copy
import hashlib
import json
import ntpath
import secrets
import threading
import time

from .actions import now
from .cleanup_tools import REGISTRY, MAX_BATCH
from .simulation import AppError


CACHE_SIZE = 96
CACHE_TTL = 60
MAX_ASSESSMENTS = 64
_CACHE_TOOLS = frozenset(('windows_temp', 'thumbnail_cache', 'gpu_cache', 'steam_cache',
    'adobe_cache', 'uv_cache', 'pip_cache', 'npm_cache', 'unity_cache', 'nuget_cache',
    'cargo_cache', 'conda_cache', 'browser_cache', 'browser_models', 'hf_cache', 'ollama_models',
    'diagnostic_files', 'printer_queue'))
# These observations refer to regenerable caches. They still need a complete
# current observation; broad model/package stores and personal data never opt in.
_AUTO_TOOLS = frozenset(('windows_temp', 'thumbnail_cache', 'gpu_cache', 'browser_cache'))
_CONFIG_CHOICES = {'hibernation': ('mode',), 'pagefile': ('mode', 'volume'),
                   'system_restore': ('operation',)}
_HIGH_IMPACT = frozenset(('system_restore', 'pagefile', 'hibernation', 'old_drivers',
    'optional_features', 'docker_volumes', 'docker_cache', 'software_uninstall', 'app_reset',
    'file_history', 'cloud_local_copy', 'virtual_disk', 'wsl_storage', 'printer_queue'))


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _nonnegative(value):
    return value if type(value) is int and value >= 0 else None


def _path_key(path):
    return 'path:' + ntpath.normcase(ntpath.normpath(str(path)))


def _coverage_overlap(left, right):
    if set(left.get('file_keys', [])) & set(right.get('file_keys', [])):
        return True
    a, b = set(left['coverage_keys']), set(right['coverage_keys'])
    if a & b:
        return True
    # Ancestor lookup grows with path depth, not the Cartesian root set.
    for paths, other in ((a, b), (b, a)):
        for key in paths:
            if key.startswith('path:'):
                current = key[5:]
                while True:
                    parent = ntpath.dirname(current)
                    if parent == current or not parent:
                        break
                    if 'path:' + parent in other:
                        return True
                    current = parent
    return False


class ToolAssessments:
    """One cache shared by direct UI, model tasks and exact batch preparation.

    ``advice_provider(row)`` may restore a durable task suggestion. It is only
    accepted when its tool reference and diagnostic signature still match.
    """
    def __init__(self, toolkit, *, ttl=CACHE_TTL, max_entries=CACHE_SIZE,
                 clock=time.monotonic, advice_provider=None):
        if ttl <= 0 or max_entries < 1:
            raise ValueError('Assessment cache bounds must be positive.')
        self.toolkit, self.ttl, self.max_entries = toolkit, ttl, max_entries
        self.clock, self.advice_provider = clock, advice_provider
        self._cache, self._ai = OrderedDict(), {}
        self._lock = threading.RLock()
        self._generation, self._serial = 0, {}

    def _reference(self, raw):
        if not isinstance(raw, dict) or set(raw) - {'tool_id', 'parameters', 'inspection_id'}:
            raise AppError('请选择有效的工具及参数。', 'invalid_tool_reference')
        return self.toolkit.validate_reference({key: raw[key] for key in ('tool_id', 'parameters') if key in raw},
                                              allow_missing=True)

    @staticmethod
    def _key(ref):
        return _digest(ref)

    def _empty(self, ref, supplied=None):
        definition = copy.deepcopy(REGISTRY[ref['tool_id']])
        definition.pop('read', None)
        tool_id, params = ref['tool_id'], ref['parameters']
        missing = [f['name'] for f in definition['parameters'] if f.get('required') and f['name'] not in params]
        choice_missing = [name for name in _CONFIG_CHOICES.get(tool_id, ()) if name not in (supplied or {})]
        missing = list(dict.fromkeys(missing + choice_missing))
        kind = ('migration' if definition['category'] == 'migration' else
                'diagnostic' if tool_id in ('memory_usage', 'startup_apps', 'background_services', 'npm_cache')
                    and params.get('operation', 'verify') == 'verify' else
                'configuration' if definition['category'] == 'system' or tool_id == 'storage_sense' else
                'privacy' if definition['category'] == 'privacy' and tool_id != 'browser_cache' else 'space')
        reason = '请先选择具体参数。' if missing else '检查后再判断是否值得清理。'
        advice = '先补充选项' if missing else '先检查状态'
        return dict(id='tool-assessment-' + self._key(ref)[:24], tool_id=tool_id,
            title=definition['title'], name=definition['title'], category=definition['category'],
            tool_ref=copy.deepcopy(ref), definition=definition,
            recommendation=dict(rating=None, purpose=definition['description'], reason=reason,
                advice=advice, impact=definition['impact'], impact_rating=4 if tool_id in _HIGH_IMPACT else 3,
                recovery=definition['recovery'], source='builtin', eligible_auto_select=False),
            benefit=dict(observed_bytes=None, estimated_reclaimable_bytes=None, estimate_kind='unknown',
                count=None, kind=kind, complete=False, coverage_keys=[], coverage_complete=False,
                file_keys=[],
                estimate_label='清理量待确认'),
            inspection=dict(id=None, at=None, status='needs_input' if missing else 'uninspected',
                complete=False, cached=False, stale=False), inspection_id=None, signature=None,
            status='needs_input' if missing else 'uninspected', selectable=True, can_execute=False,
            requires_parameters=bool(missing), missing_parameters=missing,
            availability=dict(can_execute=False, reason=reason), source='builtin',
            provenance=dict(source='builtin'), simulation_only=True)

    def _project(self, ref, result, supplied):
        row = self._empty(ref, supplied)
        presentation, evidence = result.get('presentation') or {}, result.get('evidence') or {}
        data = evidence.get('temporary_files', evidence) if ref['tool_id'] == 'windows_temp' else evidence
        status = data.get('status', presentation.get('status', 'unknown'))
        complete = data.get('complete', presentation.get('complete', False)) is True
        if row['requires_parameters'] or result.get('missing_parameters'):
            status, complete = 'needs_input', False
        if status not in ('observed', 'partial', 'unknown', 'not_found', 'needs_input', 'not_applicable'):
            status, complete = 'unknown', False
        metrics = {m.get('key'): m.get('value') for m in presentation.get('metrics', []) if isinstance(m, dict)}
        size = _nonnegative(metrics.get('observed_bytes', data.get('observed_bytes')))
        count = _nonnegative(metrics.get('observed_files', data.get('observed_files')))
        if ref['tool_id'] == 'recycle_bin':
            size, count = _nonnegative(metrics.get('recycle_bytes')), _nonnegative(metrics.get('recycle_count'))
        # An absent/inaccessible location is not proof of an empty cache.
        if status in ('unknown', 'not_found', 'needs_input'):
            size = count = None
            complete = False
        if status == 'partial':
            complete = False
        keys = [_path_key(path) for path in data.get('coverage_paths', []) if isinstance(path, str)]
        coverage_complete = bool(keys) and data.get('coverage_complete') is True
        if ref['tool_id'] == 'recycle_bin' and complete:
            keys, coverage_complete = ['recycle:' + ref['parameters']['volume']], True
        benefit = row['benefit']
        benefit.update(observed_bytes=size, count=count, complete=complete, coverage_keys=list(dict.fromkeys(keys)),
                       coverage_complete=coverage_complete,
                       file_keys=[key for key in data.get('observed_file_keys', [])
                                  if isinstance(key, str) and len(key) <= 80][:600])
        known_target = complete and status == 'observed' and count is not None
        can_execute = known_target and count > 0 and (ref['tool_id'] in _CACHE_TOOLS or ref['tool_id'] == 'recycle_bin')
        # A whole-root count cannot confirm a narrower age/prune/wheel subset.
        subset_unverified = (ref['tool_id'] == 'uv_cache' and ref['parameters'].get('operation') == 'prune' or
            ref['tool_id'] == 'pip_cache' and ref['parameters'].get('operation') == 'wheels' or
            ref['tool_id'] == 'browser_cache' and ref['parameters'].get('time_range') != 'all')
        if subset_unverified:
            can_execute = False
        if complete and benefit['kind'] in ('configuration', 'migration'):
            # Existence of a destination or a RAM snapshot is not a complete
            # configuration/dependency/precondition diagnosis.
            can_execute = False
        if ref['tool_id'] == 'hibernation':
            power = evidence.get('power_capabilities') or {}
            if power.get('hibernation_supported') is False and ref['parameters'].get('mode') != 'off':
                status, complete, can_execute = 'not_applicable', True, False
                benefit.update(complete=True, estimate_kind='not_applicable', estimate_label='当前设备不适用此操作')
        diagnostic = benefit['kind'] == 'diagnostic'
        if diagnostic:
            can_execute = False
            benefit.update(estimate_kind='not_applicable', estimate_label='本项用于检查，不预估释放空间')
        elif known_target and coverage_complete and benefit['kind'] == 'space':
            # Prune/wheels/all-time limits can target a subset. The complete
            # measured root remains only an explicitly labelled upper bound.
            benefit.update(estimated_reclaimable_bytes=size, estimate_kind='logical_upper_bound',
                           estimate_label='最多涉及的已统计文件大小，实际释放量可能更少')
        if status == 'needs_input':
            reason, rating = '请先选择具体参数，再检查当前状态。', None
        elif status == 'not_applicable':
            reason, rating = '当前设备不适用此项操作。', 1
        elif not complete or status in ('unknown', 'not_found'):
            reason, rating = ('仅取得部分占用，尚不能确认完整清理范围。' if status == 'partial' else
                              '目前缺少足够状态信息，请到对应工具进一步核对。'), None
        elif known_target and count == 0:
            reason, rating = '检查范围内没有可清理文件。', 1
        elif diagnostic:
            reason, rating = '可用于了解当前状态，不直接释放磁盘空间。', 3
        elif ref['tool_id'] in _AUTO_TOOLS and can_execute:
            reason, rating = '已找到可重新生成的缓存，可按需清理。', 4
        elif can_execute:
            reason, rating = '已找到相关内容，确认用途和影响后再清理。', 3
        else:
            reason, rating = '需进一步核对使用需要和操作条件，不建议直接批量处理。', 3
        if row['requires_parameters']:
            can_execute = False
        advice = ('先补充选项' if status == 'needs_input' else '当前无需清理' if rating == 1 else
                  '先核对状态' if not complete or status in ('unknown', 'not_found') else
                  '可按需清理' if can_execute and rating in (4, 5) else
                  '查看检查结果' if diagnostic else '按需要选择')
        row['recommendation'].update(rating=rating, reason=reason, advice=advice,
            eligible_auto_select=bool(can_execute and rating in (4, 5) and ref['tool_id'] in _AUTO_TOOLS))
        row.update(status=status, can_execute=can_execute,
            availability=dict(can_execute=can_execute, reason='' if can_execute else reason),
            presentation=copy.deepcopy(presentation))
        diagnostic_data = dict(status=status, complete=complete, benefit=copy.deepcopy(benefit),
                               presentation=copy.deepcopy(presentation), evidence=copy.deepcopy(evidence))
        row['signature'] = 'tool-diagnostic-' + _digest(dict(tool_ref=ref, diagnostic=diagnostic_data))
        row['inspection'] = dict(id='tool-inspection-' + secrets.token_hex(12), at=result.get('at') or now(),
                                 status=status, complete=complete, cached=False, stale=False)
        row['inspection_id'] = row['inspection']['id']
        return row, diagnostic_data

    @staticmethod
    def _stale(row):
        row = copy.deepcopy(row)
        row.update(status='stale', can_execute=False, availability=dict(can_execute=False, reason='检查结果已过期，请刷新。'))
        row['inspection'].update(cached=True, stale=True)
        row['recommendation'].update(eligible_auto_select=False, stale=True)
        # Completed model advice remains readable as history; expiration only
        # removes its authority to preselect or execute current targets.
        if row['recommendation']['source'] != 'llm':
            row['recommendation'].update(rating=None, reason='检查结果已过期，请刷新。', advice='先刷新状态')
        row['benefit'].update(estimated_reclaimable_bytes=None, estimate_kind='unknown', estimate_label='上次占用，仅供参考')
        return row

    def _advice(self, row):
        with self._lock:
            advice = copy.deepcopy(self._ai.get(self._key(row['tool_ref'])))
        if advice is None and self.advice_provider is not None and row.get('signature'):
            advice = self.advice_provider(copy.deepcopy(row))
        if not isinstance(advice, dict) or (advice.get('provenance') or {}).get('source') != 'llm':
            return row
        try:
            ref = self._reference(advice.get('tool_ref'))
        except AppError:
            return row
        if ref != row['tool_ref'] or advice.get('signature') != row.get('signature') or row['inspection']['stale']:
            return row
        rec = row['recommendation']
        for key in ('purpose', 'reason', 'impact', 'recovery'):
            if isinstance(advice.get(key), str) and len(advice[key]) <= 12000:
                rec[key] = advice[key]
        if isinstance(advice.get('conclusion'), str):
            rec['conclusion'] = advice['conclusion'][:12000]
            rec['advice'] = {'recommend': '建议清理', 'keep': '建议保留', 'ask': '按使用需要选择',
                             'skip': '暂时跳过'}.get(advice['conclusion'], advice['conclusion'][:12000])
        rating = advice.get('cleanup_rating', advice.get('rating'))
        if type(rating) is int and 1 <= rating <= 5 or rating is None:
            rec['rating'] = rating
        impact = advice.get('impact_rating')
        if type(impact) is int and 1 <= impact <= 5:
            rec['impact_rating'] = impact
        # AI advice never grants execution or overrides collector safety.
        rec['eligible_auto_select'] = bool(row['can_execute'] and rec['rating'] in (4, 5)
                                          and row['tool_id'] in _AUTO_TOOLS)
        rec.update(source='llm', provenance=copy.deepcopy(advice['provenance']),
                   at=advice.get('at'), task_id=advice.get('task_id'), advice_id=advice.get('advice_id'))
        row.update(source='llm', provenance=copy.deepcopy(advice['provenance']))
        return row

    def _cached(self, key):
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            self._cache.move_to_end(key)
            row = copy.deepcopy(entry['row'])
            expired = self.clock() - entry['checked_at'] >= self.ttl
        row = self._advice(row)
        if expired:
            return self._stale(row)
        row['inspection']['cached'] = True
        return row

    def catalog(self, query='', category='all', volume=None):
        listing = self.toolkit.list(query, category)
        items = []
        for tool in listing['items']:
            params = {'volume': volume} if volume is not None and any(
                field['name'] == 'volume' for field in REGISTRY[tool['id']]['parameters']) else {}
            ref = self._reference(dict(tool_id=tool['id'], parameters=params))
            items.append(self._cached(self._key(ref)) or self._empty(ref))
        return dict(listing, items=items, total=len(items), cache_ttl_seconds=self.ttl)

    def inspect(self, tool_id, parameters=None, refresh=False):
        if type(refresh) is not bool:
            raise AppError('刷新选项不正确。', 'invalid_request')
        ref = self._reference(dict(tool_id=tool_id, parameters={} if parameters is None else parameters))
        key = self._key(ref)
        existing = self._cached(key)
        supplied_missing_choice = existing is not None and any(name in (parameters or {})
            for name in existing.get('missing_parameters', []))
        if not refresh and existing is not None and not existing['inspection']['stale'] and not supplied_missing_choice:
            return existing
        with self._lock:
            generation = self._generation
            serial = self._serial[key] = self._serial.get(key, 0) + 1
            started_at = self.clock()
        # Read-only inspection runs outside the lock, allowing independent tools.
        try:
            result = self.toolkit.inspect(ref['tool_id'], ref['parameters'])
            row, diagnostic = self._project(ref, result, parameters or {})
        except BaseException:
            with self._lock:
                if self._serial.get(key) == serial and key not in self._cache:
                    self._serial.pop(key, None)
            raise
        with self._lock:
            if generation != self._generation or self._serial.get(key) != serial:
                return self._stale(row)
            self._cache[key] = dict(row=copy.deepcopy(row), diagnostic=diagnostic, checked_at=started_at)
            self._cache.move_to_end(key)
            while len(self._cache) > self.max_entries:
                old, _ = self._cache.popitem(last=False)
                self._ai.pop(old, None)
                self._serial.pop(old, None)
        result_row = self._advice(row)
        return self._stale(result_row) if self.clock() - started_at >= self.ttl else result_row

    def invalidate(self, tool_ids=None):
        with self._lock:
            self._generation += 1
            if tool_ids is None:
                self._cache.clear(); self._ai.clear(); self._serial.clear()
            else:
                names = {tool_ids} if isinstance(tool_ids, str) else set(tool_ids)
                for key in [key for key, entry in self._cache.items() if entry['row']['tool_id'] in names]:
                    self._cache.pop(key, None); self._ai.pop(key, None); self._serial.pop(key, None)

    def _selection(self, items, *, strict):
        if not isinstance(items, list) or not 1 <= len(items) <= MAX_ASSESSMENTS:
            raise AppError('每次请选择1至64项工具。', 'invalid_tool_batch')
        rows, seen = [], set()
        for raw in items:
            ref = self._reference(raw)
            key = self._key(ref)
            if key in seen:
                continue
            seen.add(key)
            row = self._cached(key)
            if strict and (row is None or row['inspection']['stale'] or not raw.get('inspection_id')
                           or raw.get('inspection_id') != row.get('inspection_id')):
                raise AppError('检查结果已有变化或已过期，请刷新后重新选择。', 'tool_inspection_changed', 409)
            rows.append(row or self._empty(ref))
        return rows

    def freeze_targets(self, items):
        rows = self._selection(items, strict=True)
        targets = []
        with self._lock:
            for row in rows:
                key = self._key(row['tool_ref'])
                entry = self._cache.get(key)
                if not entry or entry['row']['inspection_id'] != row['inspection_id']:
                    raise AppError('检查结果已有变化，请刷新。', 'tool_inspection_changed', 409)
                targets.append(dict(id=row['id'], tool_ref=copy.deepcopy(row['tool_ref']),
                    inspection_id=row['inspection_id'], signature=row['signature'], name=row['title'],
                    assessment=copy.deepcopy(row), diagnostic=copy.deepcopy(entry['diagnostic'])))
        return targets

    def is_current(self, tool_ref, inspection_id, signature=None):
        """No inspection I/O; useful for historical task/advice badges."""
        try:
            row = self._selection([dict(tool_ref, inspection_id=inspection_id)], strict=True)[0]
        except (AppError, TypeError):
            return False
        return signature is None or row['signature'] == signature

    def publish_ai(self, tool_ref, inspection_id, advice):
        row = self._selection([dict(tool_ref, inspection_id=inspection_id)], strict=False)[0]
        if inspection_id is None or inspection_id != row.get('inspection_id'):
            raise AppError('检查结果已改变。', 'tool_inspection_changed', 409)
        if not isinstance(advice, dict) or (advice.get('provenance') or {}).get('source') != 'llm':
            raise AppError('只能保存来源明确的模型建议。', 'invalid_tool_advice')
        candidate = dict(copy.deepcopy(advice), tool_ref=copy.deepcopy(row['tool_ref']), signature=row['signature'])
        # If supplied, a different diagnostic signature cannot be rebound.
        if advice.get('signature') not in (None, row['signature']):
            raise AppError('建议对应的检查结果已改变。', 'tool_inspection_changed', 409)
        with self._lock:
            current = self._cache.get(self._key(row['tool_ref']))
            if not current or current['row']['inspection_id'] != inspection_id:
                raise AppError('检查结果已改变。', 'tool_inspection_changed', 409)
            self._ai[self._key(row['tool_ref'])] = candidate
        # Model analysis can outlive the inspection TTL. Preserve its historical
        # result for the exact cached snapshot, while keeping stale execution off.
        return self._cached(self._key(row['tool_ref']))

    def summarize(self, items):
        rows = self._selection(items, strict=False) if items else []
        conflicts = []
        for index, left in enumerate(rows):
            for right in rows[index + 1:]:
                same_scope = left['tool_id'] == right['tool_id'] and not (
                    left['tool_ref']['parameters'].get('volume') and right['tool_ref']['parameters'].get('volume')
                    and left['tool_ref']['parameters']['volume'] != right['tool_ref']['parameters']['volume'])
                if (same_scope and not (left['benefit']['coverage_complete'] and right['benefit']['coverage_complete'])) or _coverage_overlap(
                        left['benefit'], right['benefit']):
                    conflicts.append(dict(ids=[left['id'], right['id']], reason='操作范围可能重叠，请选择其中一项。'))
        known = [row for row in rows if row['benefit']['estimated_reclaimable_bytes'] is not None
                 and row['benefit']['coverage_complete'] and not row['inspection']['stale']]
        unknown = len(rows) - len(known)
        # With an overlap there is no honest aggregate without a per-file union.
        estimated = sum(row['benefit']['estimated_reclaimable_bytes'] for row in known) if known and not conflicts else None
        observed = sum(row['benefit']['observed_bytes'] for row in known) if known and not conflicts else None
        return dict(selected_count=len(rows), executable_count=sum(row['can_execute'] for row in rows),
            observed_bytes=observed, estimated_reclaimable_bytes=estimated,
            estimate_kind='logical_upper_bound' if estimated is not None else 'unknown',
            estimate_complete=bool(rows) and unknown == 0 and not conflicts,
            unknown_count=unknown, overlap_count=len(conflicts), conflicts=conflicts,
            estimate_label='已确认范围的逻辑大小上限，不保证实际释放空间',
            simulation_only=True, actual_reclaimed_bytes=0)

    def prepare_selection(self, items, source=None):
        if not isinstance(items, list) or not 1 <= len(items) <= MAX_BATCH:
            raise AppError('每批最多执行40项专项操作，请分批处理。', 'invalid_tool_batch')
        # Serialize validation + plan freezing against refresh/invalidation.
        with self._lock:
            rows = self._selection(items, strict=True)
            if any(not row['can_execute'] for row in rows):
                raise AppError('所选项尚未确认可清理，请查看当前状态或选择具体参数。', 'tool_not_actionable', 409)
            summary = self.summarize([dict(row['tool_ref'], inspection_id=row['inspection_id']) for row in rows])
            if summary['conflicts']:
                raise AppError('所选工具范围重叠，请保留一个清理方式。', 'tool_selection_overlap', 409)
            # The registry sees only its own reference contract, never host snapshots.
            return self.toolkit.prepare_many([copy.deepcopy(row['tool_ref']) for row in rows], source=source)

    @staticmethod
    def compact(row):
        """Task/model projection: useful facts, no UI definition or raw JSON echo."""
        return dict(id=row['id'], title=row['title'], tool_ref=copy.deepcopy(row['tool_ref']),
            inspection_id=row['inspection_id'], signature=row['signature'], status=row['status'],
            complete=row['inspection']['complete'], can_execute=row['can_execute'],
            requires_parameters=row['requires_parameters'], missing_parameters=row['missing_parameters'],
            benefit={key: copy.deepcopy(row['benefit'][key]) for key in
                     ('kind', 'observed_bytes', 'estimated_reclaimable_bytes', 'estimate_kind', 'count', 'complete')},
            recommendation={key: copy.deepcopy(row['recommendation'][key]) for key in
                            ('rating', 'purpose', 'reason', 'impact', 'impact_rating', 'recovery', 'source')})
