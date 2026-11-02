"""Durable, bounded, read-only capability diagnostics shared by UI and tasks.

The first pass never calls every specialist's full inspector. Fixed system
probes are shared; known cache locations receive a small metadata budget.
Unknown collectors, absent locations and missing input remain distinct.
"""
from __future__ import annotations

from collections import Counter
import copy
import json
import ntpath
import os
from pathlib import Path
import secrets
import time

from .actions import now
from .cleanup_tools import REGISTRY, REGISTRY_VERSION
from .simulation import AppError, canonical, within
from .system_evidence import system_configuration, volume_snapshot
from .tool_inspection import candidate_paths, measure_paths, memory_presentation, volume_presentation


MAX_LIGHT_SECONDS = 1.2
MAX_TOTAL_ENTRIES = 1200
MAX_TOOL_ENTRIES = 200
MAX_DETAIL_PAGE = 20
CACHE_TOOLS = frozenset(('windows_temp', 'thumbnail_cache', 'diagnostic_files', 'printer_queue',
    'uv_cache', 'pip_cache', 'npm_cache', 'gpu_cache', 'browser_models', 'nuget_cache', 'conda_cache',
    'hf_cache', 'ollama_models', 'adobe_cache', 'steam_cache', 'browser_cache'))
MEMORY_TOOLS = frozenset(('hibernation', 'pagefile', 'memory_usage'))
VOLUME_TOOLS = frozenset(('windows_temp', 'recycle_bin', 'storage_sense'))


def _stopped(cancel):
    return bool(cancel and (cancel() if callable(cancel) else cancel.is_set()))


def _paths(values):
    if (not isinstance(values, (list, tuple)) or not 1 <= len(values) <= 8 or
            any(not isinstance(path, str) or not path.strip() or len(path) > 32767 or '\x00' in path
                or path.strip().startswith(('\\\\', '//', '\\??\\')) or
                not (os.path.isabs(path.strip()) or re_local_drive(path)) for path in values)):
        raise AppError('请选择1到8个本地绝对位置进行诊断。', 'invalid_diagnostic_scope')
    result = []
    for raw in values:
        value = ntpath.normpath(raw.strip()) if re_local_drive(raw) else os.path.abspath(raw.strip())
        if canonical(value) not in {canonical(path) for path in result}:
            result.append(value)
    return result


def re_local_drive(path):
    drive, tail = ntpath.splitdrive(path.strip())
    return bool(len(drive) == 2 and drive[0].isascii() and drive[0].isalpha() and drive[1] == ':'
                and tail.startswith(('\\', '/')))


def _unknown(reason, *, status='unknown'):
    return dict(status=status, complete=False, summary=reason, metrics=[], items=[], warnings=[])


def _measured_presentation(measured):
    found = measured['roots_found'] > 0
    complete = measured['complete'] and found
    metrics = [dict(key='observed_bytes', value=measured['observed_bytes'], unit='bytes'),
               dict(key='observed_files', value=measured['observed_files'], unit='count')] if found else []
    return dict(kind='cache', status=measured['status'], complete=complete,
        summary='已检查部分缓存位置' if found and not complete else '已检查缓存位置' if found else
                '已知位置未找到，不能据此认定程序未安装' if measured['status'] == 'not_found' else '缓存位置尚未完整检查',
        metrics=metrics, items=[], warnings=[],
        action_availability=dict(available=bool(measured['observed_files']) if complete else
                                True if found and measured['observed_files'] > 0 else None))


def _applicability(presentation, scope_state, evidence):
    if scope_state == 'outside_scope':
        return 'not_applicable'
    if presentation['status'] == 'needs_input':
        return 'needs_input'
    # Missing default directories or a failed executable lookup are not an
    # authoritative application inventory. Reserve this for verified adapters.
    if presentation['status'] == 'not_installed' and evidence.get('installation_verified') is True:
        return 'not_installed'
    return 'applicable' if presentation['status'] in ('observed', 'partial') else 'unknown'


def _browser_discovery_in_scope(params, paths):
    """Profile discovery itself enumerates names, so scope it before calling."""
    local = os.environ.get('LOCALAPPDATA', '')
    if not local or not os.path.isabs(local):
        return True  # The underlying resolver will return no known location.
    base = Path(local) / ('Google/Chrome/User Data' if params.get('browser') == 'chrome'
                         else 'Microsoft/Edge/User Data')
    return any(within(str(base), root) for root in paths)


class DiagnosticsStore:
    """Only this facade persists snapshots; no diagnostic grants action scope."""
    def __init__(self, state, toolkit):
        self.state, self.toolkit = state, toolkit
        with state.lock, state.db:
            state.db.execute('''CREATE TABLE IF NOT EXISTS cleanup_diagnostics (
                id TEXT PRIMARY KEY, task_id TEXT, created_at TEXT NOT NULL, payload TEXT NOT NULL)''')

    def scan(self, paths, *, task_id=None, cancel=None, progress=None):
        paths = _paths(paths)
        if task_id is not None and (not isinstance(task_id, str) or not 1 <= len(task_id) <= 160):
            raise AppError('诊断任务来源无效。', 'invalid_diagnostic_task')
        started, stamp = time.monotonic(), now()
        deadline = started + MAX_LIGHT_SECONDS
        volumes = list(dict.fromkeys(ntpath.splitdrive(path)[0].upper() for path in paths
                                     if re_local_drive(path)))
        sources, details, rows, measurement_cache = {}, {}, [], {}
        budget = dict(visited_entries=0, max_entries=MAX_TOTAL_ENTRIES, max_seconds=MAX_LIGHT_SECONDS,
                      max_depth=2, cooperative=True)

        def probe(key, function):
            if _stopped(cancel):
                return
            try:
                sources[key] = function()
            except (OSError, ValueError, AttributeError, NotImplementedError) as exc:
                sources[key] = dict(status='unknown', reason='只读系统检查未完成', error_type=type(exc).__name__)

        # Always share the fixed context rather than repeating it for each row.
        probe('memory_power', system_configuration)
        for volume in volumes:
            probe('volume:' + volume, lambda volume=volume: volume_snapshot(volume + '\\'))

        for definition in self.toolkit.list()['items']:
            tool_id = definition['id']
            if tool_id not in REGISTRY:
                continue  # User recipes have their own explicit-target inspector.
            fields = REGISTRY[tool_id]['parameters']
            defaults = {field['name']: copy.deepcopy(field['default']) for field in fields if 'default' in field}
            has_volume = any(field['name'] == 'volume' for field in fields)
            parameter_sets = [dict(defaults, volume=volume) for volume in volumes] if has_volume and volumes else [defaults]
            variants = []
            for params in parameter_sets:
                missing = [field['name'] for field in fields if field.get('required') and field['name'] not in params]
                source_refs, evidence = [], {}
                scope_state = 'in_scope'
                if missing:
                    presentation = _unknown('需要选择具体目标或补充参数', status='needs_input')
                elif _stopped(cancel):
                    presentation = _unknown('诊断已取消，尚未检查此项')
                    scope_state = 'not_checked'
                elif tool_id in MEMORY_TOOLS:
                    evidence = sources.get('memory_power', {})
                    presentation = memory_presentation(tool_id, evidence)
                    source_refs = ['memory_power']
                    scope_state = 'context_only'
                elif tool_id in VOLUME_TOOLS and tool_id != 'windows_temp':
                    key = 'volume:' + str(params.get('volume', ''))
                    evidence = sources.get(key, {})
                    presentation = volume_presentation(tool_id, params, evidence)
                    source_refs = [key]
                    scope_state = 'context_only'
                elif tool_id in CACHE_TOOLS:
                    if tool_id == 'browser_cache' and not _browser_discovery_in_scope(params, paths):
                        presentation = _unknown('浏览器资料根目录不在本次范围；可按需选择具体配置', status='not_applicable')
                        scope_state = 'outside_scope'
                    elif time.monotonic() >= deadline or budget['visited_entries'] >= MAX_TOTAL_ENTRIES:
                        presentation = _unknown('轻量检查预算已用完，可按需深入此项')
                        scope_state = 'not_checked'
                    else:
                        candidates, location_source = candidate_paths(tool_id, params)
                        selected = [path for path in candidates if any(within(str(path), root) for root in paths)]
                        if candidates and not selected:
                            presentation = _unknown('已知位置不在本次所选范围', status='not_applicable')
                            scope_state = 'outside_scope'
                        elif not selected:
                            presentation = _unknown('没有可直接检查的已知位置，安装状态仍未知')
                        else:
                            key = (tuple(canonical(str(path)) for path in selected), tool_id == 'thumbnail_cache')
                            if key not in measurement_cache:
                                remaining = min(MAX_TOOL_ENTRIES, MAX_TOTAL_ENTRIES - budget['visited_entries'])
                                measured = measure_paths(selected, max_entries=remaining,
                                    max_seconds=max(.001, min(.10, deadline - time.monotonic())), max_depth=2,
                                    filename_prefix='thumbcache_' if tool_id == 'thumbnail_cache' else None)
                                measurement_cache[key] = measured
                                budget['visited_entries'] += measured['visited_entries']
                            measured = measurement_cache[key]
                            evidence = dict(measured, location_source=location_source)
                            presentation = _measured_presentation(measured)
                else:
                    presentation = _unknown('此项需要专用检查或在对应程序中核对')
                availability = presentation.get('action_availability', {}).get('available')
                # An unknown collector/absent location is never an empty result.
                candidate = availability if type(availability) is bool else None
                variant = dict(parameters=params, status=presentation['status'],
                    complete=presentation.get('complete') is True, candidate=candidate,
                    applicability=_applicability(presentation, scope_state, evidence),
                    scope_status=scope_state, summary=presentation['summary'], source_refs=source_refs,
                    metrics=presentation.get('metrics', [])[:6])
                if missing:
                    variant['missing_parameters'] = missing
                variants.append(variant)
                details.setdefault(tool_id, []).append(dict(variant, presentation=presentation,
                    evidence={} if source_refs else evidence))
            if any(value['candidate'] is True for value in variants):
                candidate = True
            elif variants and all(value['candidate'] is False and value['complete'] for value in variants):
                candidate = False
            else:
                candidate = None
            states = [value['status'] for value in variants]
            status = states[0] if len(set(states)) == 1 else 'partial'
            applicability_states = [value['applicability'] for value in variants]
            applicability = ('applicable' if 'applicable' in applicability_states else
                             applicability_states[0] if len(set(applicability_states)) == 1 else 'unknown')
            rows.append(dict(tool_id=tool_id, title=definition['title'], category=definition['category'],
                status=status, applicability=applicability, candidate=candidate, complete=all(value['complete'] for value in variants),
                summary=variants[0]['summary'] if len(variants) == 1 else '按所选磁盘分别检查，详见variants',
                parameter_coverage='defaults_and_selected_volumes_only', variants=variants,
                next_tool='describe_cleanup_tool' if any(value.get('missing_parameters') for value in variants)
                          else 'inspect_cleanup_tool'))
            if progress:
                progress(dict(phase='diagnostics', processed=len(rows), total=len(REGISTRY),
                              message='正在归纳可用工具与轻量观察', current_tool_id=tool_id))

        counts = dict(Counter(row['status'] for row in rows))
        coverage = dict(registry_complete=len(rows) == len(REGISTRY), all_tools_fully_diagnosed=False,
            tool_count=len(rows), complete_tools=sum(row['complete'] for row in rows),
            unknown_tools=sum(row['status'] == 'unknown' for row in rows),
            needs_input=sum(row['status'] == 'needs_input' for row in rows),
            not_checked=sum(any(value['scope_status'] == 'not_checked' for value in row['variants']) for row in rows),
            reads_file_contents=False, budget=budget, shared_probe_count=len(sources),
            parameter_variants='默认参数与所选卷；其他浏览器/资料配置/项目需按需检查。',
            note='48项能力清单覆盖不等于48项完整采集器。观察占用不是预计可释放空间；诊断不扩大操作范围。')
        snapshot = dict(snapshot_id='diagnostic-' + secrets.token_hex(12), task_id=task_id, at=stamp,
            completed_at=now(), status='cancelled' if _stopped(cancel) else 'completed', counts=counts,
            registry_version=REGISTRY_VERSION, scope=dict(path_count=len(paths), volumes=volumes),
            items=rows, coverage=coverage, read_only=True, simulation_only=True,
            actual_reclaimed_bytes=0, _paths=paths, _details=details, _sources=sources)
        with self.state.lock, self.state.db:
            self.state.db.execute('INSERT INTO cleanup_diagnostics VALUES(?,?,?,?)',
                (snapshot['snapshot_id'], task_id, stamp, json.dumps(snapshot, ensure_ascii=False, allow_nan=False)))
        return self._public(snapshot)

    @staticmethod
    def _public(snapshot):
        return copy.deepcopy({key: value for key, value in snapshot.items() if not key.startswith('_')})

    def _load(self, snapshot_id, task_id=None):
        if not isinstance(snapshot_id, str) or len(snapshot_id) > 160:
            raise AppError('诊断记录无效。', 'invalid_diagnostic_snapshot')
        with self.state.lock:
            row = self.state.db.execute('SELECT payload FROM cleanup_diagnostics WHERE id=?', (snapshot_id,)).fetchone()
        if row is None:
            raise AppError('诊断记录不存在，请重新检查。', 'diagnostic_not_found', 404)
        snapshot = json.loads(row[0])
        if task_id is not None and snapshot['task_id'] != task_id:
            raise AppError('不能读取其他任务的诊断记录。', 'outside_scope', 409)
        return snapshot

    def summary(self, snapshot_id, *, task_id=None):
        return self._public(self._load(snapshot_id, task_id))

    def read(self, snapshot_id, *, task_id=None, offset=0, limit=12, tool_ids=None):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= MAX_DETAIL_PAGE:
            raise AppError('诊断分页参数无效。', 'invalid_diagnostic_page')
        snapshot = self._load(snapshot_id, task_id)
        if tool_ids is not None and (not isinstance(tool_ids, list) or not 1 <= len(tool_ids) <= MAX_DETAIL_PAGE
                or any(not isinstance(key, str) or key not in REGISTRY for key in tool_ids)
                or len(set(tool_ids)) != len(tool_ids)):
            raise AppError('请选择有效的诊断工具记录。', 'invalid_diagnostic_page')
        pool = snapshot['items'] if tool_ids is None else [row for row in snapshot['items'] if row['tool_id'] in tool_ids]
        rows = pool[offset:offset + limit]
        sources = {}
        for row in rows:
            row['details'] = snapshot['_details'].get(row['tool_id'], [])
            for variant in row['variants']:
                for key in variant['source_refs']:
                    if key in snapshot['_sources']:
                        sources[key] = snapshot['_sources'][key]
        return dict(snapshot_id=snapshot_id, task_id=snapshot['task_id'], at=snapshot['at'],
            completed_at=snapshot['completed_at'], status=snapshot['status'], read_only=True,
            offset=offset, limit=limit, total=len(pool), items=rows,
            has_more=offset + len(rows) < len(pool), sources=sources,
            coverage=snapshot['coverage'], scope=snapshot['scope'], simulation_only=True)
